r"""日常执行入口，默认加载 ANSWER_ENTRIES 里的 daily 数据包。

    $env:ANSWER_ENTRY = "daily"
    use .venv\Scripts\python.exe run_tests.py --pattern "test_daily_usage.py" -v
"""

import os
from typing import Any, Dict, List

import pytest

from common import answer_runner as runner
from config.answer_entries import ANSWER_ENTRIES, DAILY_ENTRY


def _selected_entry() -> str:
    """读取要执行的入口，只允许 daily / regression。"""
    entry = os.getenv("ANSWER_ENTRY", DAILY_ENTRY).strip().lower()
    if entry not in ANSWER_ENTRIES:
        raise ValueError(
            f"unknown ANSWER_ENTRY={entry!r}; available={sorted(ANSWER_ENTRIES)}"
        )
    return entry


def _daily_answer_items() -> List[Any]:
    """Load pytest params for the selected daily entry."""
    entry = _selected_entry()
    original = runner.ANSWER_DATA_FILES
    runner.ANSWER_DATA_FILES = ANSWER_ENTRIES[entry]
    try:
        return runner._answer_items()
    finally:
        runner.ANSWER_DATA_FILES = original


def _run_answer_item(answer_item: Dict[str, Any]) -> None:
    if answer_item["kind"] == "parallel_suite":
        runner._run_parallel_suite(answer_item["suite"])
        return
    suite = answer_item["suite"]
    case_data = answer_item["case"]
    if suite["mode"] == "stability":
        runner._run_stability_case(suite, case_data)
        return
    runner._run_case_with_client(suite, case_data)


@pytest.mark.parametrize("answer_item", _daily_answer_items())
def test_daily_usage(answer_item: Dict[str, Any]) -> None:
    """执行日常 answer 用例。"""
    _run_answer_item(answer_item)



