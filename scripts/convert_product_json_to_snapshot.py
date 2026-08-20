from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.product_migration_common import (
    MigrationError,
    SOURCE_SHOP_ID,
    file_sha256,
    normalize_source_product,
    write_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a product collection JSON export into migration YAML shards."
    )
    parser.add_argument("input_json", type=Path)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--source-shop-id", default=SOURCE_SHOP_ID)
    parser.add_argument("--part-size", type=int, default=500)
    return parser.parse_args()


def _default_output_dir(source_shop_id: str) -> Path:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    return (
        ROOT
        / "artifacts"
        / "product_migration"
        / f"converted-shop{source_shop_id}-{timestamp}"
    )


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    with temporary_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(report, file, allow_unicode=True, sort_keys=False, width=120)
    temporary_path.replace(path)


def main() -> int:
    args = parse_args()
    input_path = args.input_json.resolve()
    if not input_path.is_file():
        raise MigrationError(f"input JSON not found: {input_path}")
    if args.part_size <= 0:
        raise MigrationError("part-size must be positive")
    source_shop_id = str(args.source_shop_id or "").strip()
    if not source_shop_id.isdigit():
        raise MigrationError("source-shop-id must contain digits only")

    with input_path.open("r", encoding="utf-8-sig") as file:
        source_products = json.load(file)
    if not isinstance(source_products, list):
        raise MigrationError("input JSON top level must be a list")

    normalized_products: List[Dict[str, Any]] = []
    errors: List[str] = []
    seen_product_ids = set()
    name_fallback_count = 0
    products_without_attributes = 0

    for index, source_product in enumerate(source_products, start=1):
        if not isinstance(source_product, dict):
            errors.append(f"product #{index} must be an object")
            continue
        row_shop_id = str(source_product.get("shop_id") or "")
        if row_shop_id != source_shop_id:
            errors.append(
                f"product #{index} shop_id mismatch: expected={source_shop_id} actual={row_shop_id}"
            )
            continue
        props = source_product.get("props")
        if props is None:
            props = []
        if not isinstance(props, list):
            errors.append(f"product #{index} props must be a list")
            continue
        if not str(source_product.get("name") or "").strip():
            name_fallback_count += 1
        if not props:
            products_without_attributes += 1

        normalized_product, product_errors = normalize_source_product(
            source_product,
            props,
        )
        if product_errors:
            errors.extend(f"product #{index}: {error}" for error in product_errors)
            continue
        product_id = normalized_product["product_id"]
        if product_id in seen_product_ids:
            errors.append(f"duplicate product_id: {product_id}")
            continue
        seen_product_ids.add(product_id)
        normalized_products.append(normalized_product)

    if errors:
        preview = "; ".join(errors[:20])
        raise MigrationError(
            f"conversion validation failed with {len(errors)} error(s): {preview}"
        )

    output_dir = (args.output_dir or _default_output_dir(source_shop_id)).resolve()
    manifest_path = write_snapshot(
        normalized_products,
        output_dir,
        source_shop_id=source_shop_id,
        source_total_before=len(source_products),
        source_total_after=len(source_products),
        page_size=500,
        attribute_page_size=100,
        workers=1,
        limit=None,
        part_size=args.part_size,
    )
    attribute_count = sum(len(product["attributes"]) for product in normalized_products)
    _write_report(
        output_dir / "conversion-report.yaml",
        {
            "source_file": str(input_path),
            "source_file_sha256": file_sha256(input_path),
            "source_shop_id": source_shop_id,
            "product_count": len(normalized_products),
            "attribute_count": attribute_count,
            "name_fallback_count": name_fallback_count,
            "products_without_attributes": products_without_attributes,
            "output_manifest": str(manifest_path),
        },
    )
    print(
        f"CONVERSION_COMPLETE products={len(normalized_products)} "
        f"attributes={attribute_count} name_fallbacks={name_fallback_count} "
        f"manifest={manifest_path}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MigrationError, json.JSONDecodeError) as error:
        print(f"CONVERSION_FAILED error={error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
