from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.product_migration_common import (
    MigrationError,
    SOURCE_SHOP_ID,
    validate_snapshot,
    write_snapshot,
)


def _load_pending_conflict_ids(state_path: Path) -> Set[str]:
    conflicts: Set[str] = set()
    created: Set[str] = set()
    completed: Set[str] = set()
    with state_path.open("r", encoding="utf-8") as file:
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
            event_type = str(event.get("event") or "")
            product_id = str(event.get("product_id") or "")
            error_text = str(event.get("error") or "")
            if not product_id:
                continue
            if event_type in {"product_created", "product_created_recovered"}:
                created.add(product_id)
            elif event_type == "product_complete":
                completed.add(product_id)
            elif event_type == "product_create_conflict" or (
                event_type == "product_failed"
                and error_text.startswith("create target product")
                and "code=400" in error_text
                and "商品ID已存在" in error_text
            ):
                conflicts.add(product_id)
    return conflicts - created - completed


def _build_prefixed_products(
    products: Sequence[Dict[str, Any]],
    conflict_ids: Set[str],
    prefix: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    source_ids = {str(product.get("product_id") or "") for product in products}
    missing_ids = sorted(conflict_ids - source_ids)
    if missing_ids:
        raise MigrationError(
            f"conflict product IDs missing from snapshot: {missing_ids[:10]}"
        )

    transformed: List[Dict[str, Any]] = []
    mapping: List[Dict[str, str]] = []
    transformed_ids: Set[str] = set()
    for product in products:
        original_id = str(product.get("product_id") or "")
        if original_id not in conflict_ids:
            continue
        transformed_id = f"{prefix}{original_id}"
        if transformed_id in transformed_ids:
            raise MigrationError(f"duplicate transformed product ID: {transformed_id}")
        if transformed_id in source_ids:
            raise MigrationError(
                f"transformed product ID collides with source snapshot: {transformed_id}"
            )
        transformed_ids.add(transformed_id)
        transformed.append(
            {
                **product,
                "product_id": transformed_id,
                "attributes": [dict(attribute) for attribute in product["attributes"]],
            }
        )
        mapping.append(
            {
                "original_product_id": original_id,
                "transformed_product_id": transformed_id,
            }
        )
    return transformed, mapping


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a snapshot for product create conflicts using a prefixed product ID."
    )
    parser.add_argument("source_snapshot", type=Path)
    parser.add_argument("output_snapshot", type=Path)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--state-file", type=Path, default=None)
    parser.add_argument("--part-size", type=int, default=500)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.prefix:
        raise MigrationError("prefix must not be empty")
    if args.part_size <= 0:
        raise MigrationError("part-size must be positive")

    source_snapshot = args.source_snapshot.resolve()
    validation = validate_snapshot(source_snapshot)
    if validation.errors:
        raise MigrationError(
            "source snapshot validation failed: " + "; ".join(validation.errors[:10])
        )
    state_path = (
        args.state_file.resolve()
        if args.state_file
        else source_snapshot / "import-progress.jsonl"
    )
    conflict_ids = _load_pending_conflict_ids(state_path)
    products, mapping = _build_prefixed_products(
        validation.products, conflict_ids, args.prefix
    )
    if not products:
        raise MigrationError("no pending product conflicts found")

    output_snapshot = args.output_snapshot.resolve()
    write_snapshot(
        products,
        output_snapshot,
        source_shop_id=SOURCE_SHOP_ID,
        source_total_before=len(products),
        source_total_after=len(products),
        page_size=500,
        attribute_page_size=100,
        workers=1,
        limit=None,
        part_size=args.part_size,
    )
    mapping_path = output_snapshot / "product-id-mapping.yaml"
    with mapping_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(
            {
                "prefix": args.prefix,
                "product_count": len(mapping),
                "mapping": mapping,
            },
            file,
            allow_unicode=True,
            sort_keys=False,
            width=120,
        )
    print(
        f"PREFIXED_SNAPSHOT_COMPLETE products={len(products)} prefix={args.prefix} "
        f"output={output_snapshot}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as error:
        print(f"PREFIXED_SNAPSHOT_FAILED error={error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
