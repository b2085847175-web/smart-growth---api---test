from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_object.auth_api import AuthAPI
from common.http_client import HttpClient, create_http_client
from config.project_env import reload_project_env
from config.settings import settings


SOURCE_ENV = "console"
SOURCE_BASE_URL = "https://console.zhiyan.chat"
SOURCE_SHOP_ID = "576"
TARGET_ENV = "dev"
TARGET_BASE_URL = "https://dev.zhiyan.chat"
TARGET_SHOP_ID = "585"
SCHEMA_VERSION = 1

PRODUCT_FIELDS = {
    "product_id",
    "product_title",
    "name",
    "enabled",
    "recommend",
    "attributes",
}
ATTRIBUTE_FIELDS = {"attribute_name", "attribute_value"}
FORBIDDEN_PRODUCT_FIELDS = {"_id", "shop_id", "created_at", "updated_at", "productId"}


class MigrationError(RuntimeError):
    pass


class RestrictedHttpClient:
    def __init__(
        self,
        client: HttpClient,
        *,
        expected_base_url: str,
        allowed_methods: Iterable[str],
    ) -> None:
        self._client = client
        self.base_url = expected_base_url.rstrip("/")
        self._expected_origin = _url_origin(self.base_url)
        self._allowed_methods = {str(method).upper() for method in allowed_methods}

    def request(self, method: str, endpoint: str, **kwargs):
        normalized_method = str(method).upper()
        if normalized_method not in self._allowed_methods:
            raise MigrationError(
                f"HTTP method {normalized_method} is blocked for migration client {self.base_url}"
            )
        if (
            endpoint.startswith(("http://", "https://"))
            and _url_origin(endpoint) != self._expected_origin
        ):
            raise MigrationError(f"cross-environment request blocked: {endpoint}")
        if _url_origin(self._client.base_url) != self._expected_origin:
            raise MigrationError(
                f"client base URL changed unexpectedly: {self._client.base_url}"
            )
        return self._client.request(normalized_method, endpoint, **kwargs)

    def close(self) -> None:
        self._client.close()


@dataclass
class SnapshotValidation:
    manifest: Dict[str, Any]
    products: List[Dict[str, Any]]
    errors: List[str]
    warnings: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def _url_origin(url: str) -> Tuple[str, str, Optional[int]]:
    parsed = urlsplit(str(url).rstrip("/"))
    if not parsed.scheme or not parsed.hostname:
        raise MigrationError(f"invalid absolute URL: {url}")
    return parsed.scheme.lower(), parsed.hostname.lower(), parsed.port


def _first_nonempty(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _build_auth_header(access_token: str) -> str:
    normalized = str(access_token or "").strip()
    if normalized.lower().startswith("bearer "):
        return normalized
    return f"Bearer {normalized}"


def load_login_credentials(environment: str) -> Tuple[str, str]:
    reload_project_env()
    env_key = environment.upper()
    account = _first_nonempty(
        os.getenv(f"LOGIN_ACCOUNT_{env_key}"),
        os.getenv(f"CONTEXT_{env_key}_LOGIN_ACCOUNT"),
        os.getenv("LOGIN_ACCOUNT"),
    )
    password = _first_nonempty(
        os.getenv(f"LOGIN_PASSWORD_{env_key}"),
        os.getenv(f"CONTEXT_{env_key}_LOGIN_PASSWORD"),
        os.getenv("LOGIN_PASSWORD"),
    )
    if not account or not password:
        raise MigrationError(
            f"missing login credentials for environment: {environment}"
        )
    return account, password


def login_for_environment(environment: str, base_url: str) -> str:
    expected_base_url = {
        SOURCE_ENV: SOURCE_BASE_URL,
        TARGET_ENV: TARGET_BASE_URL,
    }.get(environment)
    if expected_base_url != base_url.rstrip("/"):
        raise MigrationError(
            f"environment/base URL mismatch: environment={environment}, base_url={base_url}"
        )

    account, password = load_login_credentials(environment)
    client = create_http_client(
        base_url=expected_base_url,
        default_headers=settings.get_headers(),
        retry_allowed_methods={"GET"},
    )
    try:
        response = AuthAPI(client=client).login(account, password)
    finally:
        client.close()

    data = response.get("data", {})
    token = str(response.get("access_token") or "").strip()
    if (
        response.get("status_code") != 200
        or not isinstance(data, dict)
        or data.get("code") != 200
        or not token
    ):
        raise MigrationError(f"login failed for environment: {environment}")
    return token


def create_migration_client(
    *,
    base_url: str,
    access_token: str,
    allowed_methods: Iterable[str],
) -> RestrictedHttpClient:
    normalized_methods = {str(method).upper() for method in allowed_methods}
    retry_methods = normalized_methods.intersection({"GET", "HEAD", "OPTIONS"})
    client = create_http_client(
        base_url=base_url,
        default_headers=settings.get_headers(),
        retry_allowed_methods=retry_methods,
    )
    client.set_header("Authorization", _build_auth_header(access_token))
    return RestrictedHttpClient(
        client,
        expected_base_url=base_url,
        allowed_methods=normalized_methods,
    )


def normalize_source_product(
    product: Dict[str, Any],
    attributes: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    product_id = str(product.get("product_id") or "").strip()
    product_title = str(
        product.get("product_title") or product.get("name") or ""
    ).strip()
    name = str(product.get("name") or product_title).strip()
    errors: List[str] = []

    if not product_id:
        errors.append("missing product_id")
    if not product_title:
        errors.append(f"product {product_id or '<unknown>'} missing product_title")
    if not name:
        errors.append(f"product {product_id or '<unknown>'} missing name")
    if not isinstance(product.get("enabled"), bool):
        errors.append(f"product {product_id or '<unknown>'} enabled is not boolean")
    if not isinstance(product.get("recommend"), bool):
        errors.append(f"product {product_id or '<unknown>'} recommend is not boolean")

    normalized_attributes: List[Dict[str, str]] = []
    for index, attribute in enumerate(attributes):
        attribute_name = str(
            attribute.get("name")
            or attribute.get("attributeName")
            or attribute.get("attribute_name")
            or ""
        ).strip()
        raw_value = attribute.get("value")
        if raw_value is None:
            raw_value = attribute.get("attributeValue")
        if raw_value is None:
            raw_value = attribute.get("attribute_value")
        attribute_value = "" if raw_value is None else str(raw_value)
        if not attribute_name:
            errors.append(
                f"product {product_id or '<unknown>'} attribute #{index + 1} missing name"
            )
            continue
        normalized_attributes.append(
            {
                "attribute_name": attribute_name,
                "attribute_value": attribute_value,
            }
        )

    return {
        "product_id": product_id,
        "product_title": product_title,
        "name": name,
        "enabled": product.get("enabled"),
        "recommend": product.get("recommend"),
        "attributes": normalized_attributes,
    }, errors


def write_snapshot(
    products: Sequence[Dict[str, Any]],
    output_dir: Path,
    *,
    source_shop_id: str,
    source_total_before: int,
    source_total_after: int,
    page_size: int,
    attribute_page_size: int,
    workers: int,
    limit: Optional[int],
    part_size: int,
) -> Path:
    output_dir = output_dir.resolve()
    if output_dir.exists():
        unexpected_files = [
            path.name
            for path in output_dir.iterdir()
            if path.name != "export-progress.jsonl"
        ]
        if unexpected_files:
            raise MigrationError(
                f"snapshot output directory contains unexpected files: {unexpected_files}"
            )
    output_dir.mkdir(parents=True, exist_ok=True)

    parts: List[Dict[str, Any]] = []
    total_attributes = 0
    for part_index, start in enumerate(range(0, len(products), part_size), start=1):
        part_products = list(products[start : start + part_size])
        attribute_count = sum(len(item.get("attributes", [])) for item in part_products)
        total_attributes += attribute_count
        file_name = f"products-{part_index:04d}.yaml"
        part_path = output_dir / file_name
        _write_yaml(part_path, {"products": part_products})
        parts.append(
            {
                "file": file_name,
                "product_count": len(part_products),
                "attribute_count": attribute_count,
                "sha256": file_sha256(part_path),
            }
        )

    complete_export = (
        source_total_before == source_total_after
        and len(products) == source_total_before
        and (limit is None or limit >= source_total_before)
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "environment": SOURCE_ENV,
            "base_url": SOURCE_BASE_URL,
            "shop_id": str(source_shop_id),
            "read_only": True,
        },
        "intended_target": {
            "environment": TARGET_ENV,
            "base_url": TARGET_BASE_URL,
            "shop_id": TARGET_SHOP_ID,
        },
        "exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "complete_export": complete_export,
        "export_options": {
            "page_size": page_size,
            "attribute_page_size": attribute_page_size,
            "workers": workers,
            "limit": limit,
            "part_size": part_size,
        },
        "summary": {
            "source_total_before": source_total_before,
            "source_total_after": source_total_after,
            "product_count": len(products),
            "attribute_count": total_attributes,
        },
        "parts": parts,
    }
    manifest_path = output_dir / "manifest.yaml"
    _write_yaml(manifest_path, manifest)
    return manifest_path


def validate_snapshot(
    snapshot_dir: Path,
    *,
    allow_partial: bool = False,
    expected_source_shop_id: str = SOURCE_SHOP_ID,
) -> SnapshotValidation:
    snapshot_dir = snapshot_dir.resolve()
    manifest_path = snapshot_dir / "manifest.yaml"
    errors: List[str] = []
    warnings: List[str] = []
    products: List[Dict[str, Any]] = []

    if not manifest_path.exists():
        return SnapshotValidation({}, [], [f"manifest not found: {manifest_path}"], [])

    manifest = _read_yaml(manifest_path)
    if not isinstance(manifest, dict):
        return SnapshotValidation({}, [], ["manifest must be a mapping"], [])

    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported schema_version: {manifest.get('schema_version')}")
    _validate_environment_block(
        manifest.get("source"),
        label="source",
        expected=(SOURCE_ENV, SOURCE_BASE_URL, str(expected_source_shop_id)),
        errors=errors,
    )
    _validate_environment_block(
        manifest.get("intended_target"),
        label="intended_target",
        expected=(TARGET_ENV, TARGET_BASE_URL, TARGET_SHOP_ID),
        errors=errors,
    )
    if manifest.get("source", {}).get("read_only") is not True:
        errors.append("manifest source.read_only must be true")
    if not manifest.get("complete_export"):
        message = "snapshot is partial and must not be used for a full migration"
        if allow_partial:
            warnings.append(message)
        else:
            errors.append(message)

    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts:
        errors.append("manifest parts must be a non-empty list")
        parts = []

    expected_root = snapshot_dir
    for part_index, part in enumerate(parts, start=1):
        if not isinstance(part, dict):
            errors.append(f"part #{part_index} must be a mapping")
            continue
        file_name = str(part.get("file") or "")
        part_path = (snapshot_dir / file_name).resolve()
        if part_path.parent != expected_root:
            errors.append(
                f"part #{part_index} path escapes snapshot directory: {file_name}"
            )
            continue
        if not part_path.exists():
            errors.append(f"part file not found: {file_name}")
            continue
        expected_hash = str(part.get("sha256") or "")
        actual_hash = file_sha256(part_path)
        if expected_hash != actual_hash:
            errors.append(f"part checksum mismatch: {file_name}")
            continue
        part_data = _read_yaml(part_path)
        part_products = (
            part_data.get("products") if isinstance(part_data, dict) else None
        )
        if not isinstance(part_products, list):
            errors.append(f"part products must be a list: {file_name}")
            continue
        if part.get("product_count") != len(part_products):
            errors.append(f"part product_count mismatch: {file_name}")
        actual_attribute_count = sum(
            len(item.get("attributes", []))
            for item in part_products
            if isinstance(item, dict) and isinstance(item.get("attributes"), list)
        )
        if part.get("attribute_count") != actual_attribute_count:
            errors.append(f"part attribute_count mismatch: {file_name}")
        products.extend(part_products)

    seen_product_ids: Set[str] = set()
    for index, product in enumerate(products, start=1):
        prefix = f"product #{index}"
        if not isinstance(product, dict):
            errors.append(f"{prefix} must be a mapping")
            continue
        unexpected_fields = set(product).difference(PRODUCT_FIELDS)
        forbidden_fields = set(product).intersection(FORBIDDEN_PRODUCT_FIELDS)
        if unexpected_fields:
            errors.append(
                f"{prefix} has unexpected fields: {sorted(unexpected_fields)}"
            )
        if forbidden_fields:
            errors.append(f"{prefix} has forbidden fields: {sorted(forbidden_fields)}")
        product_id = product.get("product_id")
        if not isinstance(product_id, str) or not product_id.strip():
            errors.append(f"{prefix} product_id must be a non-empty string")
        elif product_id in seen_product_ids:
            errors.append(f"duplicate product_id: {product_id}")
        else:
            seen_product_ids.add(product_id)
        for field in ("product_title", "name"):
            if (
                not isinstance(product.get(field), str)
                or not product.get(field, "").strip()
            ):
                errors.append(f"{prefix} {field} must be a non-empty string")
        for field in ("enabled", "recommend"):
            if not isinstance(product.get(field), bool):
                errors.append(f"{prefix} {field} must be boolean")
        attributes = product.get("attributes")
        if not isinstance(attributes, list):
            errors.append(f"{prefix} attributes must be a list")
            continue
        for attribute_index, attribute in enumerate(attributes, start=1):
            attribute_prefix = f"{prefix} attribute #{attribute_index}"
            if not isinstance(attribute, dict):
                errors.append(f"{attribute_prefix} must be a mapping")
                continue
            unexpected_attribute_fields = set(attribute).difference(ATTRIBUTE_FIELDS)
            if unexpected_attribute_fields:
                errors.append(
                    f"{attribute_prefix} has unexpected fields: {sorted(unexpected_attribute_fields)}"
                )
            if (
                not isinstance(attribute.get("attribute_name"), str)
                or not attribute.get("attribute_name", "").strip()
            ):
                errors.append(
                    f"{attribute_prefix} attribute_name must be a non-empty string"
                )
            if not isinstance(attribute.get("attribute_value"), str):
                errors.append(f"{attribute_prefix} attribute_value must be a string")

    summary = manifest.get("summary", {})
    if isinstance(summary, dict):
        if summary.get("product_count") != len(products):
            errors.append("manifest summary.product_count mismatch")
        actual_attribute_count = sum(
            len(product.get("attributes", []))
            for product in products
            if isinstance(product, dict) and isinstance(product.get("attributes"), list)
        )
        if summary.get("attribute_count") != actual_attribute_count:
            errors.append("manifest summary.attribute_count mismatch")
    else:
        errors.append("manifest summary must be a mapping")

    return SnapshotValidation(manifest, products, errors, warnings)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_snapshot_dir(prefix: str = "export") -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return ROOT / "artifacts" / "product_migration" / f"{prefix}-{timestamp}"


def partition_round_robin(items: Sequence[Any], workers: int) -> List[List[Any]]:
    worker_count = max(1, min(int(workers), len(items) or 1))
    partitions: List[List[Any]] = [[] for _ in range(worker_count)]
    for index, item in enumerate(items):
        partitions[index % worker_count].append(item)
    return partitions


def _write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(
            data,
            file,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    temporary_path.replace(path)


def _read_yaml(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def _validate_environment_block(
    block: Any,
    *,
    label: str,
    expected: Tuple[str, str, str],
    errors: List[str],
) -> None:
    if not isinstance(block, dict):
        errors.append(f"manifest {label} must be a mapping")
        return
    expected_environment, expected_base_url, expected_shop_id = expected
    if block.get("environment") != expected_environment:
        errors.append(f"manifest {label}.environment must be {expected_environment}")
    if str(block.get("base_url") or "").rstrip("/") != expected_base_url:
        errors.append(f"manifest {label}.base_url must be {expected_base_url}")
    if str(block.get("shop_id") or "") != expected_shop_id:
        errors.append(f"manifest {label}.shop_id must be {expected_shop_id}")
