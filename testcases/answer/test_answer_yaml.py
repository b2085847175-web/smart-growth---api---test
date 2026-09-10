"""Config-driven /chat/answer test entrypoint (regression + unit tests).

One file replaces the previous per-purpose wrapper files. Each named "entry"
maps to data YAML files in ``data/answer``. The active entry is selected with
the ``ANSWER_ENTRY`` environment variable (default: regression).

Entries are centrally registered in ``config/answer_entries.py``.
"""

import os
from typing import Any, Dict, List

import pytest

from api_object.chat_api import ChatAPI
from common import answer_runner as runner
from config.answer_entries import ANSWER_ENTRIES, REGRESSION_ENTRY


def _selected_entry() -> str:
    entry = os.getenv("ANSWER_ENTRY", REGRESSION_ENTRY).strip().lower()
    if entry not in ANSWER_ENTRIES:
        raise ValueError(
            f"unknown ANSWER_ENTRY={entry!r}; available={sorted(ANSWER_ENTRIES)}"
        )
    return entry


def _answer_entry_items() -> List[Any]:
    """Load pytest params for the selected entry."""
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


@pytest.mark.parametrize("answer_item", _answer_entry_items())
def test_answer_yaml(answer_item: Dict[str, Any]) -> None:
    """Unified /chat/answer entrypoint driven by ANSWER_ENTRY env var."""
    _run_answer_item(answer_item)


# ---------------------------------------------------------------------------
# Unit tests for ChatAPI and answer assertion logic (no network required)
# ---------------------------------------------------------------------------
from common.answer_runner import _assert_quality_record, _run_answer_case


class _Response:
    status_code = 200

    @staticmethod
    def json() -> Dict[str, Any]:
        return {"code": 200, "data": {}}


class _RecordingClient:
    def __init__(self) -> None:
        self.payload: Dict[str, Any] = {}

    def post(self, endpoint: str, json: Dict[str, Any]) -> _Response:
        self.payload = json
        return _Response()


class _RejectedChatAPI:
    @staticmethod
    def chat_answer(**kwargs: Any) -> Dict[str, Any]:
        return {
            "status_code": 422,
            "data": {"detail": "invalid request"},
        }


def test_chat_answer_normalizes_message_timestamps_to_integers() -> None:
    client = _RecordingClient()
    api = ChatAPI(client=client)

    api.chat_answer(
        account="account",
        messages=[
            {"role": "user", "content": "first", "created_at": 123.75},
            {"role": "assistant", "content": "second"},
        ],
    )

    timestamps = [message["created_at"] for message in client.payload["messages"]]
    assert timestamps[0] == 123
    assert all(isinstance(timestamp, int) for timestamp in timestamps)


def test_forward_only_response_is_an_effective_response() -> None:
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "forward",
                    "payload": {"scene": "瀹㈣瘔"},
                }
            ]
        }
    }

    assert ChatAPI.extract_assistant_messages(response) == []
    assert ChatAPI.extract_action_types(response) == ["forward"]
    assert ChatAPI.has_effective_response(response)


def test_invalid_forward_is_not_effective() -> None:
    """Forward without scene in payload should not be effective."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "forward",
                    "payload": {},
                }
            ]
        }
    }

    assert not ChatAPI.has_effective_response(response)


@pytest.mark.parametrize("scene", ["", "   ", "\t\n"])
def test_forward_with_whitespace_scene_is_not_effective(scene: str) -> None:
    """Forward with only whitespace scene should not be effective."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "forward",
                    "payload": {"scene": scene},
                }
            ]
        }
    }

    assert not ChatAPI.has_effective_response(response)


def test_send_message_without_content_is_not_effective() -> None:
    """sendMessage without actual content should not be effective."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "sendMessage",
                    "payload": {},
                }
            ]
        }
    }

    assert not ChatAPI.has_effective_response(response)


@pytest.mark.parametrize("content", ["", "   ", "\t\n"])
def test_send_message_with_whitespace_only_is_not_effective(content: str) -> None:
    """sendMessage with only whitespace should not be effective."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "sendMessage",
                    "payload": {
                        "contentType": "text",
                        "content": content,
                    },
                }
            ]
        }
    }

    assert not ChatAPI.has_effective_response(response)


@pytest.mark.parametrize("payload", [None, "invalid", []])
def test_non_mapping_action_payload_is_not_effective(payload: Any) -> None:
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "sendMessage",
                    "payload": payload,
                }
            ]
        }
    }

    assert ChatAPI.extract_ai_reply(response) is None
    assert ChatAPI.extract_assistant_messages(response) == []
    assert not ChatAPI.has_effective_response(response)


def test_unknown_action_is_not_effective() -> None:
    """Unknown action types should not be considered effective."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "unknownActionType",
                    "payload": {"content": "unexpected"},
                }
            ]
        }
    }

    assert not ChatAPI.has_effective_response(response)


def test_send_message_with_content_is_effective() -> None:
    """sendMessage with text content is effective even if not in assistant_messages."""
    response = {
        "data": {
            "ai_actions": [
                {
                    "actionType": "sendMessage",
                    "payload": {
                        "contentType": "text",
                        "content": "浣犲ソ",
                    },
                }
            ]
        }
    }

    assert ChatAPI.has_effective_response(response)


def _quality_record(final_reply: Any = None) -> Dict[str, Any]:
    return {
        "final_reply": final_reply,
        "level": None,
        "categories": [],
        "stats_map": {},
        "details_map": {},
        "action_types": [],
        "forward_scenes": [],
    }


def _chat_response(action_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "data": {
            "ai_actions": [
                {
                    "actionType": action_type,
                    "payload": payload,
                }
            ]
        }
    }


def test_empty_replies_are_allowed_for_forward_response() -> None:
    _assert_quality_record(
        "forward-only",
        {},
        _quality_record(),
        "",
        True,
        _chat_response("forward", {"scene": "瀹㈣瘔"}),
    )


def test_empty_replies_are_rejected_without_forward_response() -> None:
    with pytest.raises(AssertionError, match="response is not a forward action"):
        _assert_quality_record(
            "empty-send-message",
            {},
            _quality_record(),
            "",
            True,
            _chat_response("sendMessage", {"contentType": "text", "content": ""}),
        )


def test_one_empty_reply_is_rejected() -> None:
    with pytest.raises(AssertionError, match="chat reply and quality final_reply mismatch"):
        _assert_quality_record(
            "reply-mismatch",
            {},
            _quality_record(),
            "chat reply",
            True,
            _chat_response("sendMessage", {"contentType": "text", "content": "chat reply"}),
        )


def test_transport_error_fails_when_business_assertions_are_disabled() -> None:
    suite = {
        "suite_name": "unit",
        "mode": "sequential",
        "quality": False,
        "assertions": False,
        "match_score": False,
        "final_reply_equals_chat": False,
        "turn_interval_seconds": 0,
    }
    case = {"name": "rejected", "turns": [{"question": "hello"}]}
    runtime = {
        "chat_account": "account",
        "platform": "tmall",
        "shop_id": "585",
        "shop_name": "shop_585",
        "is_test": True,
    }

    with pytest.raises(AssertionError, match="response status mismatch"):
        _run_answer_case(
            {
                "chat_api": _RejectedChatAPI(),
                "quality_inspection_api": object(),
                "runtime": runtime,
            },
            suite,
            case,
        )
