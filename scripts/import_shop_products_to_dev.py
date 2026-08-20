from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_object.product_api import ProductAPI
from api_object.product_attribute_api import ProductAttributeAPI
from scripts.product_migration_common import (
    MigrationError,
    TARGET_BASE_URL,
    TARGET_ENV,
    TARGET_SHOP_ID,
    create_migration_client,
    file_sha256,
    login_for_environment,
    partition_round_robin,
    validate_snapshot,
)


class ExistingProductError(MigrationError):
    pass


class ImportState:
    def __init__(self, path: Path, snapshot_sha256: str) -> None:
        self.path = path.resolve()
        self.snapshot_sha256 = snapshot_sha256
        self.lock = threading.Lock()
        self.created: Dict[str, str] = {}
        self.completed: Set[str] = set()
        self.skipped_existing: Set[str] = set()
        self.api_existing: Set[str] = set()
        self.attributes_created = 0
        self.attributes_updated = 0
        self._started = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as file:
            for line_number, line in enumerate(file, start=1):
                normalized = line.strip()
                if not normalized:
                    continue
                try:
                    event = json.loads(normalized)
                except json.JSONDecodeError as error:
                    raise MigrationError(
                        f"invalid import state JSON at line {line_number}: {error}"
                    ) from error
                event_type = event.get("event")
                product_id = str(event.get("product_id") or "")
                if event_type == "run_started":
                    existing_hash = str(event.get("snapshot_sha256") or "")
                    if existing_hash != self.snapshot_sha256:
                        raise MigrationError(
                            "import state belongs to a different snapshot"
                        )
                    self._started = True
                elif (
                    event_type in {"product_created", "product_created_recovered"}
                    and product_id
                ):
                    target_internal_id = str(event.get("target_internal_id") or "")
                    if target_internal_id:
                        self.created[product_id] = target_internal_id
                elif event_type == "product_complete" and product_id:
                    self.completed.add(product_id)
                elif event_type == "product_skipped_existing" and product_id:
                    self.skipped_existing.add(product_id)
                elif event_type in {
                    "product_skipped_existing_api",
                    "product_create_conflict",
                } and product_id:
                    self.api_existing.add(product_id)
                elif event_type == "product_failed" and product_id:
                    error_text = str(event.get("error") or "")
                    if (
                        error_text.startswith("create target product")
                        and "code=400" in error_text
                        and "商品ID已存在" in error_text
                    ):
                        self.api_existing.add(product_id)
                if event_type in {"attribute_created", "attribute_created_recovered"}:
                    self.attributes_created += 1
                elif event_type in {
                    "attribute_updated",
                    "attribute_updated_recovered",
                }:
                    self.attributes_updated += 1

    def ensure_started(self, snapshot_dir: Path) -> None:
        if self._started:
            return
        self.append(
            {
                "event": "run_started",
                "snapshot_dir": str(snapshot_dir.resolve()),
                "snapshot_sha256": self.snapshot_sha256,
                "target_base_url": TARGET_BASE_URL,
                "target_shop_id": TARGET_SHOP_ID,
            }
        )
        self._started = True

    def append(self, event: Dict[str, Any]) -> None:
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(serialized + "\n")
                file.flush()
                os.fsync(file.fileno())
            event_type = event.get("event")
            product_id = str(event.get("product_id") or "")
            if (
                event_type in {"product_created", "product_created_recovered"}
                and product_id
            ):
                self.created[product_id] = str(event.get("target_internal_id") or "")
            elif event_type == "product_complete" and product_id:
                self.completed.add(product_id)
            elif event_type == "product_skipped_existing" and product_id:
                self.skipped_existing.add(product_id)
            elif event_type in {
                "product_skipped_existing_api",
                "product_create_conflict",
            } and product_id:
                self.api_existing.add(product_id)
            if event_type in {"attribute_created", "attribute_created_recovered"}:
                self.attributes_created += 1
            elif event_type in {"attribute_updated", "attribute_updated_recovered"}:
                self.attributes_updated += 1


def _require_success(response: Dict[str, Any], operation: str) -> None:
    if response.get("status_code") != 200 or response.get("code") != 200:
        body = response.get("data", {})
        message = (
            body.get("message") or body.get("msg") if isinstance(body, dict) else ""
        )
        raise MigrationError(
            f"{operation} failed: status={response.get('status_code')} "
            f"code={response.get('code')} message={message}"
        )


def _fetch_all_target_products(
    product_api: ProductAPI, page_size: int
) -> List[Dict[str, Any]]:
    page = 1
    products: List[Dict[str, Any]] = []
    while True:
        response = product_api.get_products(
            shop_id=TARGET_SHOP_ID,
            page=page,
            page_size=page_size,
            sort_by="updated_at",
            sort_order="asc",
        )
        _require_success(response, f"fetch target product page {page}")
        current_products = response.get("products", [])
        products.extend(current_products)
        result = response.get("result", {})
        total = (
            int(result.get("total", len(products)))
            if isinstance(result, dict)
            else len(products)
        )
        if len(products) >= total or not current_products:
            return products
        page += 1


def _fetch_all_target_attributes(
    attribute_api: ProductAttributeAPI,
    product_internal_id: str,
    page_size: int,
) -> List[Dict[str, Any]]:
    page = 1
    attributes: List[Dict[str, Any]] = []
    while True:
        response = attribute_api.get_product_attributes(
            product_internal_id=product_internal_id,
            shop_id=TARGET_SHOP_ID,
            page=page,
            page_size=page_size,
        )
        _require_success(
            response, f"fetch target attributes for product {product_internal_id}"
        )
        current_attributes = response.get("attributes", [])
        attributes.extend(current_attributes)
        result = response.get("result", {})
        total = (
            int(result.get("total", len(attributes)))
            if isinstance(result, dict)
            else len(attributes)
        )
        if len(attributes) >= total or not current_attributes:
            return attributes
        page += 1


def _attribute_pair(attribute: Dict[str, Any]) -> Tuple[str, str]:
    name = str(
        attribute.get("attribute_name")
        or attribute.get("attributeName")
        or attribute.get("name")
        or ""
    )
    raw_value = attribute.get("attribute_value")
    if raw_value is None:
        raw_value = attribute.get("attributeValue")
    if raw_value is None:
        raw_value = attribute.get("value")
    return name, "" if raw_value is None else str(raw_value)


def _attribute_hash(pair: Tuple[str, str]) -> str:
    return hashlib.sha256(f"{pair[0]}\0{pair[1]}".encode("utf-8")).hexdigest()


def _merge_attributes_by_name(
    attributes: Sequence[Dict[str, Any]],
) -> List[Tuple[str, str]]:
    values_by_name: Dict[str, List[str]] = {}
    for attribute in attributes:
        name, value = _attribute_pair(attribute)
        values = values_by_name.setdefault(name, [])
        if value not in values:
            values.append(value)
    return [(name, "\n".join(values)) for name, values in values_by_name.items()]


def _find_exact_product(
    product_api: ProductAPI, product_id: str
) -> Optional[Dict[str, Any]]:
    response = product_api.get_products(
        shop_id=TARGET_SHOP_ID,
        page=1,
        page_size=50,
        search=product_id,
    )
    _require_success(response, f"recover target product {product_id}")
    for product in response.get("products", []):
        if str(product.get("product_id") or "") == product_id:
            return product
    return None


def _create_or_recover_product(
    product_api: ProductAPI,
    product: Dict[str, Any],
    state: ImportState,
) -> Tuple[str, bool]:
    product_id = product["product_id"]
    existing_state_id = state.created.get(product_id)
    if existing_state_id:
        return existing_state_id, False

    response: Optional[Dict[str, Any]] = None
    try:
        response = product_api.create_product(
            enabled=product["enabled"],
            name=product["name"],
            product_id=product_id,
            product_title=product["product_title"],
            recommend=product["recommend"],
            shop_id=TARGET_SHOP_ID,
        )
        _require_success(response, f"create target product {product_id}")
        target_internal_id = str(response.get("product_internal_id") or "")
        if not target_internal_id:
            raise MigrationError(
                f"create target product {product_id} returned no internal ID"
            )
        state.append(
            {
                "event": "product_created",
                "product_id": product_id,
                "target_internal_id": target_internal_id,
            }
        )
        return target_internal_id, True
    except Exception as error:
        recovered = _find_exact_product(product_api, product_id)
        if not recovered or not recovered.get("_id"):
            response_data = response.get("data", {}) if response else {}
            message = (
                str(response_data.get("message") or response_data.get("msg") or "")
                if isinstance(response_data, dict)
                else ""
            )
            if response and response.get("code") == 400 and message == "商品ID已存在":
                raise ExistingProductError(str(error)) from error
            raise
        target_internal_id = str(recovered["_id"])
        state.append(
            {
                "event": "product_created_recovered",
                "product_id": product_id,
                "target_internal_id": target_internal_id,
            }
        )
        return target_internal_id, False


def _import_product(
    product_api: ProductAPI,
    attribute_api: ProductAttributeAPI,
    product: Dict[str, Any],
    state: ImportState,
    *,
    page_size: int,
) -> Dict[str, Any]:
    product_id = product["product_id"]
    if product_id in state.completed:
        return {
            "status": "already_complete",
            "product_id": product_id,
            "attributes_created": 0,
        }

    target_internal_id, created_now = _create_or_recover_product(
        product_api, product, state
    )
    existing_by_name: Dict[str, Dict[str, Any]] = {}
    if not created_now:
        existing_attributes = _fetch_all_target_attributes(
            attribute_api,
            target_internal_id,
            page_size,
        )
        for attribute in existing_attributes:
            name, _ = _attribute_pair(attribute)
            existing_by_name.setdefault(name, attribute)

    desired_attributes = _merge_attributes_by_name(product.get("attributes", []))
    attributes_created = 0
    attributes_updated = 0
    for attribute_index, pair in enumerate(desired_attributes, start=1):
        existing = existing_by_name.get(pair[0])
        operation = "create"
        try:
            if existing:
                if _attribute_pair(existing)[1] == pair[1]:
                    continue
                attribute_id = str(existing.get("_id") or existing.get("id") or "")
                if not attribute_id:
                    raise MigrationError(
                        f"target attribute {pair[0]!r} for product {product_id} has no ID"
                    )
                operation = "update"
                response = attribute_api.update_product_attribute(
                    attribute_id=attribute_id,
                    attribute_name=pair[0],
                    attribute_value=pair[1],
                    product_internal_id=target_internal_id,
                    shop_id=TARGET_SHOP_ID,
                )
            else:
                response = attribute_api.create_product_attribute(
                    attribute_name=pair[0],
                    attribute_value=pair[1],
                    product_internal_id=target_internal_id,
                    shop_id=TARGET_SHOP_ID,
                )
            _require_success(
                response,
                f"{operation} attribute #{attribute_index} for target product {product_id}",
            )
        except Exception:
            refreshed_attributes = _fetch_all_target_attributes(
                attribute_api,
                target_internal_id,
                page_size,
            )
            refreshed_by_name = {
                _attribute_pair(item)[0]: item for item in refreshed_attributes
            }
            refreshed = refreshed_by_name.get(pair[0])
            if not refreshed or _attribute_pair(refreshed)[1] != pair[1]:
                raise
            existing_by_name = refreshed_by_name
            state.append(
                {
                    "event": f"attribute_{operation}d_recovered",
                    "product_id": product_id,
                    "target_internal_id": target_internal_id,
                    "attribute_index": attribute_index,
                    "attribute_hash": _attribute_hash(pair),
                }
            )
            continue

        existing_by_name[pair[0]] = {
            "_id": existing.get("_id") if existing else "",
            "attributeName": pair[0],
            "attributeValue": pair[1],
        }
        if operation == "create":
            attributes_created += 1
        else:
            attributes_updated += 1
        state.append(
            {
                "event": f"attribute_{operation}d",
                "product_id": product_id,
                "target_internal_id": target_internal_id,
                "attribute_index": attribute_index,
                "attribute_hash": _attribute_hash(pair),
            }
        )

    if desired_attributes:
        verified_attributes = _fetch_all_target_attributes(
            attribute_api,
            target_internal_id,
            page_size,
        )
        verified_by_name = {
            _attribute_pair(item)[0]: _attribute_pair(item)[1]
            for item in verified_attributes
        }
        missing_pairs = [
            pair for pair in desired_attributes if verified_by_name.get(pair[0]) != pair[1]
        ]
        if missing_pairs:
            raise MigrationError(
                f"attribute verification failed for product {product_id}: "
                f"missing_count={len(missing_pairs)}"
            )

    state.append(
        {
            "event": "product_complete",
            "product_id": product_id,
            "target_internal_id": target_internal_id,
            "attribute_count": len(desired_attributes),
            "source_attribute_count": len(product.get("attributes", [])),
        }
    )
    return {
        "status": "created" if created_now else "resumed",
        "product_id": product_id,
        "attributes_created": attributes_created,
        "attributes_updated": attributes_updated,
    }


def _write_summary(path: Path, summary: Dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(summary, file, allow_unicode=True, sort_keys=False, width=120)
    temporary_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a validated shop 576 YAML snapshot into dev shop 585."
    )
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--attribute-page-size", type=int, default=100)
    parser.add_argument("--max-products", type=int, default=None)
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--allow-partial-snapshot", action="store_true")
    parser.add_argument(
        "--retry-product-conflicts",
        action="store_true",
        help="Retry product IDs previously rejected by the create API but absent from shop 585.",
    )
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.workers <= 0 or args.workers > 16:
        raise MigrationError("workers must be between 1 and 16")
    if args.page_size <= 0:
        raise MigrationError("page-size must be positive")
    if args.attribute_page_size <= 0 or args.attribute_page_size > 100:
        raise MigrationError("attribute-page-size must be between 1 and 100")
    if args.max_products is not None and args.max_products <= 0:
        raise MigrationError("max-products must be positive")

    snapshot_dir = args.snapshot_dir.resolve()
    validation = validate_snapshot(
        snapshot_dir,
        allow_partial=args.allow_partial_snapshot,
    )
    for warning in validation.warnings:
        print(f"IMPORT_WARNING {warning}", flush=True)
    if validation.errors:
        raise MigrationError(
            "snapshot validation failed: " + "; ".join(validation.errors[:10])
        )

    manifest_path = snapshot_dir / "manifest.yaml"
    snapshot_sha256 = file_sha256(manifest_path)
    state_path = args.state_file or snapshot_dir / "import-progress.jsonl"
    state = ImportState(state_path, snapshot_sha256)

    print(
        f"IMPORT_PREFLIGHT target={TARGET_BASE_URL} shop_id={TARGET_SHOP_ID} "
        f"execute={args.execute} workers={args.workers}",
        flush=True,
    )
    token = login_for_environment(TARGET_ENV, TARGET_BASE_URL)
    preflight_client = create_migration_client(
        base_url=TARGET_BASE_URL,
        access_token=token,
        allowed_methods={"GET"},
    )
    try:
        target_products = _fetch_all_target_products(
            ProductAPI(client=preflight_client),
            args.page_size,
        )
    finally:
        preflight_client.close()

    target_by_product_id: Dict[str, Dict[str, Any]] = {}
    for target_product in target_products:
        product_id = str(target_product.get("product_id") or "")
        if product_id:
            target_by_product_id.setdefault(product_id, target_product)

    candidates: List[Dict[str, Any]] = []
    skipped_existing: List[str] = []
    pending_product_conflicts: List[str] = []
    already_complete: List[str] = []
    for product in validation.products:
        product_id = product["product_id"]
        if product_id in state.completed:
            already_complete.append(product_id)
            continue
        if product_id in target_by_product_id and product_id not in state.created:
            skipped_existing.append(product_id)
            continue
        if (
            product_id in state.api_existing
            and product_id not in state.created
            and not args.retry_product_conflicts
        ):
            pending_product_conflicts.append(product_id)
            continue
        candidates.append(product)

    if args.max_products is not None:
        candidates = candidates[: args.max_products]
    candidate_attribute_count = sum(
        len(product["attributes"]) for product in candidates
    )
    print(
        f"IMPORT_PLAN snapshot_products={len(validation.products)} target_existing={len(target_by_product_id)} "
        f"skip_duplicates={len(skipped_existing)} "
        f"pending_product_conflicts={len(pending_product_conflicts)} "
        f"already_complete={len(already_complete)} "
        f"to_process={len(candidates)} attributes={candidate_attribute_count}",
        flush=True,
    )
    if not args.execute:
        print("IMPORT_DRY_RUN no data was written", flush=True)
        return 0

    state.ensure_started(snapshot_dir)
    for product_id in skipped_existing:
        if product_id not in state.skipped_existing:
            state.append(
                {"event": "product_skipped_existing", "product_id": product_id}
            )

    progress_lock = threading.Lock()
    progress = {
        "completed": 0,
        "failed": 0,
        "product_conflicts": 0,
        "attributes_created": 0,
        "attributes_updated": 0,
    }

    def process_partition(partition: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
        client = create_migration_client(
            base_url=TARGET_BASE_URL,
            access_token=token,
            allowed_methods={"GET", "POST", "PUT"},
        )
        product_api = ProductAPI(client=client)
        attribute_api = ProductAttributeAPI(client=client)
        results: List[Dict[str, Any]] = []
        try:
            for product in partition:
                product_id = product["product_id"]
                try:
                    result = _import_product(
                        product_api,
                        attribute_api,
                        product,
                        state,
                        page_size=args.attribute_page_size,
                    )
                    results.append(result)
                    with progress_lock:
                        progress["completed"] += 1
                        progress["attributes_created"] += result["attributes_created"]
                        progress["attributes_updated"] += result["attributes_updated"]
                except ExistingProductError as error:
                    state.append(
                        {
                            "event": "product_create_conflict",
                            "product_id": product_id,
                            "error": str(error),
                        }
                    )
                    results.append(
                        {
                            "status": "product_conflict",
                            "product_id": product_id,
                        }
                    )
                    with progress_lock:
                        progress["product_conflicts"] += 1
                except Exception as error:
                    state.append(
                        {
                            "event": "product_failed",
                            "product_id": product_id,
                            "target_internal_id": state.created.get(product_id, ""),
                            "error": str(error),
                        }
                    )
                    results.append(
                        {
                            "status": "failed",
                            "product_id": product_id,
                            "error": str(error),
                        }
                    )
                    with progress_lock:
                        progress["failed"] += 1
                with progress_lock:
                    processed = (
                        progress["completed"]
                        + progress["failed"]
                        + progress["product_conflicts"]
                    )
                    if processed % 25 == 0 or processed == len(candidates):
                        print(
                            f"IMPORT_PROGRESS processed={processed} total={len(candidates)} "
                            f"completed={progress['completed']} failed={progress['failed']} "
                            f"product_conflicts={progress['product_conflicts']}",
                            flush=True,
                        )
            return results
        finally:
            client.close()

    all_results: List[Dict[str, Any]] = []
    partitions = partition_round_robin(candidates, args.workers)
    if candidates:
        with ThreadPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(process_partition, partition)
                for partition in partitions
                if partition
            ]
            for future in as_completed(futures):
                all_results.extend(future.result())

    failed_results = [
        result for result in all_results if result.get("status") == "failed"
    ]
    summary = {
        "snapshot_dir": str(snapshot_dir),
        "target": {
            "environment": TARGET_ENV,
            "base_url": TARGET_BASE_URL,
            "shop_id": TARGET_SHOP_ID,
        },
        "snapshot_product_count": len(validation.products),
        "target_existing_before": len(target_by_product_id),
        "skipped_existing": len(skipped_existing),
        "pending_product_conflicts": len(pending_product_conflicts)
        + progress["product_conflicts"],
        "already_complete": len(already_complete),
        "processed": len(all_results),
        "completed": progress["completed"],
        "failed": progress["failed"],
        "attributes_created": progress["attributes_created"],
        "attributes_updated": progress["attributes_updated"],
        "failed_product_ids": [result["product_id"] for result in failed_results],
        "cumulative": {
            "products_created": len(state.created),
            "products_completed": len(state.completed),
            "skipped_existing": len(state.skipped_existing),
            "pending_product_conflicts": len(
                state.api_existing - set(state.created)
            ),
            "attributes_created": state.attributes_created,
            "attributes_updated": state.attributes_updated,
        },
    }
    _write_summary(snapshot_dir / "import-summary.yaml", summary)
    print(
        f"IMPORT_COMPLETE completed={progress['completed']} failed={progress['failed']} "
        f"skipped_existing={len(skipped_existing)} "
        f"pending_product_conflicts={len(pending_product_conflicts) + progress['product_conflicts']} "
        f"attributes_created={progress['attributes_created']} "
        f"attributes_updated={progress['attributes_updated']}",
        flush=True,
    )
    return 1 if failed_results or pending_product_conflicts or progress["product_conflicts"] else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as error:
        print(f"IMPORT_FAILED error={error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
