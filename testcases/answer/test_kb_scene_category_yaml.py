"""Dedicated /chat/answer coverage for KB scene category examples."""

import os
from typing import Any, Dict, List

import pytest

from testcases.answer import test_answer_yaml as answer_yaml


DEFAULT_KB_SCENE_DATA_FILES = [
    "data/answer/kb_scene_categories/all_categories.yaml",
]


def _selected_data_files() -> List[str]:
    configured = [
        item.strip()
        for item in os.getenv("KB_SCENE_DATA_FILES", "").split(",")
        if item.strip()
    ]
    return configured or DEFAULT_KB_SCENE_DATA_FILES


def _kb_scene_answer_items() -> List[Any]:
    original_data_files = answer_yaml.ANSWER_DATA_FILES
    answer_yaml.ANSWER_DATA_FILES = _selected_data_files()
    try:
        return answer_yaml._answer_items()
    finally:
        answer_yaml.ANSWER_DATA_FILES = original_data_files


@pytest.mark.parametrize("answer_item", _kb_scene_answer_items())
def test_kb_scene_category_yaml(answer_item: Dict[str, Any]) -> None:
    """Run generated KB scene category YAML without mixing with default answer suites."""
    if answer_item["kind"] == "parallel_suite":
        answer_yaml._run_parallel_suite(answer_item["suite"])
        return
    suite = answer_item["suite"]
    case_data = answer_item["case"]
    if suite["mode"] == "stability":
        answer_yaml._run_stability_case(suite, case_data)
        return
    answer_yaml._run_case_with_client(suite, case_data)
