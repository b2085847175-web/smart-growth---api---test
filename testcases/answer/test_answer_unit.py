from typing import Any, Dict

import pytest

from api_object.chat_api import ChatAPI
from testcases.answer.test_answer_yaml import _run_answer_case


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
