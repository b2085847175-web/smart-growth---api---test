from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_object.scene_api import SceneAPI
from common.http_client import create_http_client
from config.settings import settings
from scripts.product_migration_common import TARGET_BASE_URL, TARGET_ENV, login_for_environment


def _default_output_path(shop_id: str) -> Path:
    return ROOT / "data" / "kb" / "scenes" / f"shop_{shop_id}_all.json"


def _collect_all_scenes(scene_api: SceneAPI, shop_id: str, page_size: int) -> Dict[str, Any]:
    all_scenes = []
    categories_by_id: Dict[str, Dict[str, Any]] = {}
    page = 1
    last_response: Dict[str, Any] = {}

    while True:
        response = scene_api.get_scenes(
            shop_id=shop_id,
            page=page,
            page_size=page_size,
        )
        last_response = response
        page_scenes = response["scenes"]
        all_scenes.extend(page_scenes)
        for category in response.get("categories", []):
            if not isinstance(category, dict):
                continue
            category_id = str(category.get("_id") or category.get("id") or "").strip()
            if category_id:
                categories_by_id[category_id] = category
        if not page_scenes or len(page_scenes) < page_size:
            break
        page += 1

    return {
        "last_response": last_response,
        "categories": list(categories_by_id.values()),
        "scenes": all_scenes,
        "pages_fetched": page,
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Export KB scenes to JSON.")
    parser.add_argument("--shop-id", default="585")
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    access_token = login_for_environment(TARGET_ENV, TARGET_BASE_URL)
    client = create_http_client(
        base_url=TARGET_BASE_URL,
        default_headers=settings.get_headers(),
        retry_allowed_methods={"GET"},
    )
    client.set_header("Authorization", f"Bearer {access_token}")
    try:
        scene_api = SceneAPI(client=client)
        collected = _collect_all_scenes(
            scene_api,
            str(args.shop_id),
            int(args.page_size),
        )
    finally:
        client.close()

    output_path = args.output or _default_output_path(str(args.shop_id))
    output_path = output_path.resolve()

    exported = {
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "request": {
            "shop_id": str(args.shop_id),
            "page_size": int(args.page_size),
            "fetch_all": True,
        },
        "categories": collected["categories"],
        "scenes": collected["scenes"],
        "scene_count": len(collected["scenes"]),
        "pages_fetched": collected["pages_fetched"],
        "status_code": collected["last_response"].get("status_code"),
    }
    _write_json(output_path, exported)

    print(
        json.dumps(
            {
                "output": str(output_path),
                "scene_count": len(collected["scenes"]),
                "pages_fetched": collected["pages_fetched"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
