from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _slug(value: str, fallback: str = "category") -> str:
    normalized = re.sub(r"[^0-9A-Za-z]+", "_", str(value or "").lower()).strip("_")
    return normalized or fallback


def _read_yaml_or_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(payload, file, allow_unicode=True, sort_keys=False)


def _category_groups(export_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    scene_count = int(export_data.get("scene_count") or len(export_data.get("scenes") or []))
    groups = [
        {
            "key": "ALL",
            "name": "全部分类",
            "count": scene_count,
        }
    ]
    for category in export_data.get("categories") or []:
        if not isinstance(category, dict):
            continue
        groups.append(
            {
                "id": str(category.get("_id") or category.get("id") or ""),
                "key": str(category.get("key") or ""),
                "name": str(category.get("name") or ""),
                "count": int(category.get("count") or 0),
            }
        )
    return groups


def _case_question_examples(scene: Dict[str, Any]) -> List[str]:
    examples = scene.get("question_examples") or []
    normalized = [str(example).strip() for example in examples if str(example).strip()]
    if normalized:
        return normalized
    scene_name = str(scene.get("scene_name") or "").strip()
    return [f"[{scene_name}]"] if scene_name else []


def _build_case(
    scene: Dict[str, Any],
    *,
    scene_index: int,
    example_index: int,
    question: str,
) -> Dict[str, Any]:
    category_name = str(scene.get("category_name") or "").strip()
    category_key = str(scene.get("category_key") or "").strip()
    scene_name = str(scene.get("scene_name") or "").strip()
    return {
        "name": f"{category_key or 'UNKNOWN'}_{scene_index:03d}_{scene_name}_{example_index:02d}",
        "category": category_name,
        "category_key": category_key,
        "scene_name": scene_name,
        "scene_description": str(scene.get("scene_description") or "").strip(),
        "question_example": question,
        "turns": [
            {
                "question": question,
                "expect": {
                    "scene": scene_name,
                },
            }
        ],
    }


def _suite_payload(suite_name: str, category_groups: List[Dict[str, Any]], cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "suite": {
            "name": suite_name,
            "mode": "sequential",
            "quality": True,
            "match_score": False,
            "final_reply_equals_chat": False,
            "turn_interval_seconds": 1,
            "quality_retries": 10,
            "quality_retry_interval_seconds": 2,
            "assertions": True,
        },
        "target_env": "dev",
        "category_groups": category_groups,
        "cases": cases,
    }


def build_cases(export_data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    cases_by_category: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    all_cases: List[Dict[str, Any]] = []
    for scene_index, scene in enumerate(export_data.get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        category_key = str(scene.get("category_key") or "UNKNOWN").strip() or "UNKNOWN"
        for example_index, question in enumerate(_case_question_examples(scene), start=1):
            case = _build_case(
                scene,
                scene_index=scene_index,
                example_index=example_index,
                question=question,
            )
            all_cases.append(case)
            cases_by_category[category_key].append(case)
    cases_by_category["ALL"] = all_cases
    return cases_by_category


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate KB scene category answer YAML files.")
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "data" / "kb" / "scenes" / "shop_585_all.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "answer" / "kb_scene_categories",
    )
    args = parser.parse_args()

    export_data = _read_yaml_or_json(args.source)
    groups = _category_groups(export_data)
    cases_by_category = build_cases(export_data)
    output_dir = args.output_dir.resolve()

    written: List[Path] = []
    all_payload = _suite_payload(
        "kb_scene_category_all",
        groups,
        cases_by_category.get("ALL", []),
    )
    all_path = output_dir / "all_categories.yaml"
    _write_yaml(all_path, all_payload)
    written.append(all_path)

    group_by_key = {group["key"]: group for group in groups if group.get("key") and group["key"] != "ALL"}
    for category_key, category in group_by_key.items():
        file_name = f"{_slug(category_key)}.yaml"
        category_payload = _suite_payload(
            f"kb_scene_category_{_slug(category_key)}",
            [
                groups[0],
                category,
            ],
            cases_by_category.get(category_key, []),
        )
        path = output_dir / file_name
        _write_yaml(path, category_payload)
        written.append(path)

    for path in written:
        data = _read_yaml_or_json(path)
        print(f"GENERATED path={path} cases={len(data.get('cases') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
