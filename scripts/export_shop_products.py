from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_object.product_api import ProductAPI
from api_object.product_attribute_api import ProductAttributeAPI
from scripts.product_migration_common import (
    MigrationError,
    SOURCE_BASE_URL,
    SOURCE_ENV,
    SOURCE_SHOP_ID,
    create_migration_client,
    default_snapshot_dir,
    login_for_environment,
    normalize_source_product,
    partition_round_robin,
    write_snapshot,
)


class ExportProgress:
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.lock = threading.Lock()
        self.products: Dict[str, Tuple[Any, Dict[str, Any]]] = {}
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
                        f"invalid export progress JSON at line {line_number}: {error}"
                    ) from error
                product_id = str(event.get("product_id") or "")
                product = event.get("product")
                if product_id and isinstance(product, dict):
                    self.products[product_id] = (
                        event.get("source_updated_at"),
                        product,
                    )

    def get(self, source_product: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        product_id = str(source_product.get("product_id") or "")
        cached = self.products.get(product_id)
        if not cached:
            return None
        cached_updated_at, cached_product = cached
        current_updated_at = source_product.get("updated_at")
        if current_updated_at is None or cached_updated_at is None:
            return None
        if cached_updated_at != current_updated_at:
            return None
        return cached_product

    def append(
        self,
        source_product: Dict[str, Any],
        normalized_product: Dict[str, Any],
    ) -> None:
        product_id = str(source_product.get("product_id") or "")
        event = {
            "product_id": product_id,
            "source_updated_at": source_product.get("updated_at"),
            "product": normalized_product,
        }
        serialized = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(serialized + "\n")
                file.flush()
                os.fsync(file.fileno())
            self.products[product_id] = (
                source_product.get("updated_at"),
                normalized_product,
            )


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


def _fetch_product_page(
    access_token: str,
    *,
    source_shop_id: str,
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    client = create_migration_client(
        base_url=SOURCE_BASE_URL,
        access_token=access_token,
        allowed_methods={"GET"},
    )
    try:
        response = ProductAPI(client=client).get_products(
            shop_id=source_shop_id,
            page=page,
            page_size=page_size,
            sort_by="updated_at",
            sort_order="asc",
        )
        _require_success(response, f"fetch source product page {page}")
        return response
    finally:
        client.close()


def fetch_source_products(
    access_token: str,
    *,
    source_shop_id: str,
    page_size: int,
    workers: int,
    limit: Optional[int],
) -> Tuple[List[Dict[str, Any]], int]:
    first_page = _fetch_product_page(
        access_token,
        source_shop_id=source_shop_id,
        page=1,
        page_size=page_size,
    )
    result = first_page.get("result", {})
    total = int(result.get("total", len(first_page.get("products", []))))
    desired_count = min(total, limit) if limit is not None else total
    page_count = max(1, math.ceil(desired_count / page_size)) if desired_count else 0
    pages: Dict[int, List[Dict[str, Any]]] = {1: first_page.get("products", [])}

    remaining_pages = list(range(2, page_count + 1))
    if remaining_pages:
        max_workers = min(max(1, workers), len(remaining_pages))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _fetch_product_page,
                    access_token,
                    source_shop_id=source_shop_id,
                    page=page,
                    page_size=page_size,
                ): page
                for page in remaining_pages
            }
            for future in as_completed(futures):
                page = futures[future]
                pages[page] = future.result().get("products", [])

    products: List[Dict[str, Any]] = []
    for page in range(1, page_count + 1):
        products.extend(pages.get(page, []))
    if limit is not None:
        products = products[:limit]

    seen_ids = set()
    for product in products:
        product_id = str(product.get("product_id") or "")
        if product_id in seen_ids:
            raise MigrationError(
                f"duplicate source product_id found during export: {product_id}"
            )
        seen_ids.add(product_id)
        if not product.get("_id"):
            raise MigrationError(
                f"source product missing internal _id: {product_id or '<unknown>'}"
            )
    return products, total


def _fetch_all_attributes(
    attribute_api: ProductAttributeAPI,
    *,
    source_shop_id: str,
    product_internal_id: str,
    page_size: int,
) -> List[Dict[str, Any]]:
    page = 1
    attributes: List[Dict[str, Any]] = []
    while True:
        response = attribute_api.get_product_attributes(
            product_internal_id=product_internal_id,
            shop_id=source_shop_id,
            page=page,
            page_size=page_size,
        )
        _require_success(
            response, f"fetch attributes for product {product_internal_id} page {page}"
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


def export_product_attributes(
    access_token: str,
    source_products: Sequence[Dict[str, Any]],
    *,
    source_shop_id: str,
    page_size: int,
    workers: int,
    export_progress: ExportProgress,
) -> List[Dict[str, Any]]:
    indexed_products = list(enumerate(source_products))
    normalized_products: List[Optional[Dict[str, Any]]] = [None] * len(indexed_products)
    pending_products: List[Tuple[int, Dict[str, Any]]] = []
    reused_count = 0
    for index, source_product in indexed_products:
        cached_product = export_progress.get(source_product)
        if cached_product is None:
            pending_products.append((index, source_product))
            continue
        normalized_products[index] = cached_product
        reused_count += 1

    progress_lock = threading.Lock()
    progress = {"completed": reused_count}
    if reused_count:
        print(
            f"EXPORT_RESUME reused={reused_count} pending={len(pending_products)}",
            flush=True,
        )

    def process_partition(
        partition: Sequence[Tuple[int, Dict[str, Any]]]
    ) -> List[Tuple[int, Dict[str, Any]]]:
        client = create_migration_client(
            base_url=SOURCE_BASE_URL,
            access_token=access_token,
            allowed_methods={"GET"},
        )
        attribute_api = ProductAttributeAPI(client=client)
        partition_results: List[Tuple[int, Dict[str, Any]]] = []
        try:
            for index, source_product in partition:
                attributes = _fetch_all_attributes(
                    attribute_api,
                    source_shop_id=source_shop_id,
                    product_internal_id=str(source_product["_id"]),
                    page_size=page_size,
                )
                normalized_product, errors = normalize_source_product(
                    source_product, attributes
                )
                if errors:
                    raise MigrationError("; ".join(errors))
                export_progress.append(source_product, normalized_product)
                partition_results.append((index, normalized_product))
                with progress_lock:
                    progress["completed"] += 1
                    completed = progress["completed"]
                    if completed % 50 == 0 or completed == len(source_products):
                        print(
                            f"EXPORT_PROGRESS completed={completed} total={len(source_products)}",
                            flush=True,
                        )
            return partition_results
        finally:
            client.close()

    partitions = partition_round_robin(pending_products, workers)
    if pending_products:
        with ThreadPoolExecutor(max_workers=len(partitions)) as executor:
            futures = [
                executor.submit(process_partition, partition)
                for partition in partitions
                if partition
            ]
            for future in as_completed(futures):
                for index, normalized_product in future.result():
                    normalized_products[index] = normalized_product

    if any(product is None for product in normalized_products):
        raise MigrationError("attribute export completed with missing products")
    return [product for product in normalized_products if product is not None]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only export of shop products and attributes from console to YAML shards."
    )
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--source-shop-id", default=SOURCE_SHOP_ID)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--attribute-page-size", type=int, default=100)
    parser.add_argument("--part-size", type=int, default=500)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.page_size <= 0 or args.part_size <= 0 or args.attribute_page_size <= 0:
        raise MigrationError(
            "page-size, attribute-page-size and part-size must be positive"
        )
    if args.attribute_page_size > 100:
        raise MigrationError("attribute-page-size must not exceed 100")
    if args.workers <= 0 or args.workers > 32:
        raise MigrationError("workers must be between 1 and 32")
    if args.limit is not None and args.limit <= 0:
        raise MigrationError("limit must be positive")
    source_shop_id = str(args.source_shop_id or "").strip()
    if not source_shop_id.isdigit():
        raise MigrationError("source-shop-id must contain digits only")

    output_dir = args.output_dir or default_snapshot_dir()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(
        f"EXPORT_START source={SOURCE_BASE_URL} shop_id={source_shop_id} "
        f"read_only=true workers={args.workers} limit={args.limit} "
        f"output_dir={output_dir}",
        flush=True,
    )
    export_progress = ExportProgress(output_dir / "export-progress.jsonl")
    token = login_for_environment(SOURCE_ENV, SOURCE_BASE_URL)
    source_products, source_total_before = fetch_source_products(
        token,
        source_shop_id=source_shop_id,
        page_size=args.page_size,
        workers=args.workers,
        limit=args.limit,
    )
    print(
        f"EXPORT_PRODUCTS selected={len(source_products)} source_total={source_total_before}",
        flush=True,
    )
    normalized_products = export_product_attributes(
        token,
        source_products,
        source_shop_id=source_shop_id,
        page_size=args.attribute_page_size,
        workers=args.workers,
        export_progress=export_progress,
    )
    source_total_after = int(
        _fetch_product_page(
            token,
            source_shop_id=source_shop_id,
            page=1,
            page_size=1,
        )
        .get("result", {})
        .get("total", 0)
    )
    if source_total_before != source_total_after:
        raise MigrationError(
            f"source product total changed during export: before={source_total_before} after={source_total_after}"
        )
    manifest_path = write_snapshot(
        normalized_products,
        output_dir,
        source_shop_id=source_shop_id,
        source_total_before=source_total_before,
        source_total_after=source_total_after,
        page_size=args.page_size,
        attribute_page_size=args.attribute_page_size,
        workers=args.workers,
        limit=args.limit,
        part_size=args.part_size,
    )
    attribute_count = sum(len(product["attributes"]) for product in normalized_products)
    print(
        f"EXPORT_COMPLETE products={len(normalized_products)} attributes={attribute_count} "
        f"manifest={manifest_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as error:
        print(f"EXPORT_FAILED error={error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
