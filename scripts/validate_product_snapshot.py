from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.product_migration_common import (
    MigrationError,
    SOURCE_SHOP_ID,
    validate_snapshot,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a product migration YAML snapshot."
    )
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("--source-shop-id", default=SOURCE_SHOP_ID)
    parser.add_argument("--allow-partial-snapshot", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validation = validate_snapshot(
        args.snapshot_dir,
        allow_partial=args.allow_partial_snapshot,
        expected_source_shop_id=str(args.source_shop_id),
    )
    for warning in validation.warnings:
        print(f"SNAPSHOT_WARNING {warning}", flush=True)
    if validation.errors:
        for error in validation.errors[:50]:
            print(f"SNAPSHOT_ERROR {error}", file=sys.stderr, flush=True)
        if len(validation.errors) > 50:
            print(
                f"SNAPSHOT_ERROR omitted={len(validation.errors) - 50}",
                file=sys.stderr,
                flush=True,
            )
        return 1

    attribute_count = sum(len(product["attributes"]) for product in validation.products)
    print(
        f"SNAPSHOT_VALID products={len(validation.products)} attributes={attribute_count} "
        f"complete={validation.manifest.get('complete_export')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MigrationError as error:
        print(f"SNAPSHOT_FAILED error={error}", file=sys.stderr, flush=True)
        raise SystemExit(1)
