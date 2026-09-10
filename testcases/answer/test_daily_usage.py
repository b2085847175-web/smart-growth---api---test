"""Daily execution entrypoint for /chat/answer.

Runs the daily_usage entry by default (merged data + screenshot history +
shop347 + daily question + online feedback).  Override with ANSWER_ENTRY
to run a different pack.

    $env:ANSWER_ENTRY = "scheduled"
    use .venv/Scripts/python.exe run_tests.py --pattern "test_daily_usage.py" -v
"""

import os
from typing import Any, Dict, List

import pytest

from common import answer_runner as runner
from config.answer_entries import DAILY_DEFAULT_ENTRY, DAILY_ENTRIES


def _selected_entry() -> str:
    entry = os.getenv("ANSWER_ENTRY", DAILY_DEFAULT_ENTRY).strip().lower()
    if entry not in DAILY_ENTRIES:
        raise ValueError(
            f"unknown ANSWER_ENTRY={entry!r}; available={sorted(DAILY_ENTRIES)}"
        )
    return entry


def _daily_answer_items() -> List[Any]:
    """Load pytest params for the selected daily entry."""
    entry = _selected_entry()
    original = runner.ANSWER_DATA_FILES
    runner.ANSWER_DATA_FILES = DAILY_ENTRIES[entry]
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
    """Daily /chat/answer execution entrypoint (default: daily_usage)."""
    _run_answer_item(answer_item)
