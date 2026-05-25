"""多轮对话 YAML 测试：仅配置用户问题，历史由真实接口响应累积。"""

import os
import random
import re
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List

import yaml

from api_object.auth_api import AuthAPI
from api_object.chat_api import ChatAPI
from common.http_client import create_http_client
from config.context_runtime import load_context_runtime
from config.project_env import resolve_suite_target_env
from testcases.case_product import extract_product_from_content, normalize_inquiry_product
from testcases.unittest_helpers import bind_case_tests

_DEFAULT_MULTITURN_CASES_FILE = "chat_record_data_xmind.yaml"


def _data_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "chat_record"


def _resolve_multiturn_cases_file() -> Path:
    cases_file = os.getenv("CHAT_MULTITURN_CASES_FILE", _DEFAULT_MULTITURN_CASES_FILE).strip()
    if not cases_file:
        cases_file = _DEFAULT_MULTITURN_CASES_FILE
    path = Path(cases_file)
    if not path.is_absolute():
        path = _data_dir() / path
    return path


def _load_multiturn_suite() -> Dict[str, Any]:
    data_path = _resolve_multiturn_cases_file()
    if not data_path.is_file():
        raise FileNotFoundError(f"multiturn suite file not found: {data_path}")

    with open(data_path, "r", encoding="utf-8") as file:
        suite = yaml.safe_load(file) or {}

    target_env = resolve_suite_target_env(suite)
    if target_env not in {"dev", "console"}:
        raise ValueError(
            f"multiturn suite target_env must be dev or console, got: {suite.get('target_env')} in {data_path}"
        )

    cases = suite.get("cases") or []
    if not isinstance(cases, list):
        raise ValueError(f"multiturn suite cases must be a list in {data_path}")

    return {
        "target_env": target_env,
        "cases_file": str(data_path),
        "cases": cases,
    }


def _safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("gbk", errors="backslashreplace").decode("gbk"))


def build_runtime_username(case_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", case_name.lower()).strip("_")
    short_name = normalized[:12] or "case"
    timestamp_ms = int(time.time() * 1000)
    rand4 = f"{random.getrandbits(16):04x}"
    return f"tb_{short_name}_{timestamp_ms}_{rand4}"[:40]


def _normalize_expect(expect_data: Any) -> Dict[str, Any]:
    if not isinstance(expect_data, dict):
        return {}
    return dict(expect_data)


def _normalize_multiturn_case(case_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(case_data, dict):
        raise ValueError(f"multiturn case must be a dict, got: {type(case_data).__name__}")

    case_name = str(case_data.get("name", "")).strip()
    if not case_name:
        raise ValueError("multiturn case name is required")

    raw_questions = case_data.get("questions") or []
    if not isinstance(raw_questions, list):
        raise ValueError(f"[{case_name}] questions must be a list")

    turns: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_questions, start=1):
        if isinstance(item, str):
            question = item.strip()
            expect: Dict[str, Any] = {}
        elif isinstance(item, dict):
            question = str(item.get("question") or item.get("message") or "").strip()
            expect = _normalize_expect(item.get("expect", {}))
        else:
            raise ValueError(f"[{case_name}] questions[{index}] must be a string or dict")

        if not question:
            raise ValueError(f"[{case_name}] questions[{index}] question cannot be empty")
        turns.append({"question": question, "expect": expect})

    if not turns:
        raise ValueError(f"[{case_name}] questions cannot be empty")

    inquiry_product = case_data.get("inquiry_product")
    product_url = str(case_data.get("product_url", "")).strip()
    if product_url and not inquiry_product:
        extracted = extract_product_from_content(product_url)
        if extracted:
            inquiry_product = extracted

    return {
        "name": case_name,
        "turns": turns,
        "inquiry_product": inquiry_product,
        "product_url": product_url,
    }


def _assert_reply(case_label: str, expected: Dict[str, Any], chat_reply: str) -> None:
    for expected_text in expected.get("reply_contains", []):
        assert expected_text in chat_reply, (
            f"[{case_label}] chat reply missing expected text: {expected_text} | actual: {chat_reply}"
        )


def _build_auth_header(access_token: str) -> str:
    normalized = str(access_token or "").strip()
    if not normalized:
        return normalized
    if normalized.lower().startswith("bearer "):
        return normalized
    return f"Bearer {normalized}"


def _run_multiturn_flow(authenticated_apis: Dict[str, Any], case_data: Dict[str, Any]) -> None:
    chat_client: ChatAPI = authenticated_apis["chat_api"]
    runtime = authenticated_apis["runtime"]
    normalized_case = _normalize_multiturn_case(case_data)
    case_name = normalized_case["name"]
    turns = normalized_case["turns"]
    platform = runtime["platform"]
    shop_id = runtime["shop_id"]
    shop_name = runtime["shop_name"]
    account = runtime["chat_account"]
    is_test = runtime["is_test"]

    runtime_username = build_runtime_username(case_name)
    case_label = f"{case_name}|username={runtime_username}"
    inquiry_product = normalize_inquiry_product(
        normalized_case.get("inquiry_product"),
        [],
        platform,
    )

    _safe_print(
        f"MULTITURN_CASE case_name={case_name} runtime_username={runtime_username} "
        f"shop_id={shop_id} turns_count={len(turns)} inquiry_product={inquiry_product}"
    )

    conversation_messages: List[Dict[str, Any]] = []
    turn_failures: List[str] = []

    for turn_index, turn in enumerate(turns, start=1):
        question = turn["question"]
        turn_expect = turn.get("expect", {})
        turn_send_at = time.time()
        user_created_at = turn_send_at
        try:
            conversation_messages.append(
                {
                    "role": "user",
                    "content": question,
                    "created_at": user_created_at,
                }
            )
            _safe_print(
                f"TURN_MULTITURN case_name={case_name} runtime_username={runtime_username} "
                f"turn_index={turn_index} question={question} "
                f"request_messages_count={len(conversation_messages)}"
            )

            chat_response = chat_client.chat_answer(
                account=account,
                messages=list(conversation_messages),
                inquiry_product=inquiry_product,
                platform=platform,
                shop_id=shop_id,
                shop_name=shop_name,
                username=runtime_username,
                is_test=is_test,
                last_order_time=user_created_at,
            )
            turn_response_at = time.time()

            _safe_print(f"CHAT_REQUEST payload={chat_response['payload']}")
            _safe_print(f"CHAT_RESPONSE status={chat_response['status_code']} body={chat_response['data']}")

            assert chat_response["status_code"] == 200
            assert chat_response["data"].get("code") == 200

            assistant_messages = chat_client.extract_assistant_messages(
                chat_response,
                response_received_at=turn_response_at,
            )
            _safe_print(
                f"TURN_RESULT case_name={case_name} runtime_username={runtime_username} "
                f"turn_index={turn_index} assistant_messages_count={len(assistant_messages)}"
            )
            assert assistant_messages, f"[{case_label}] turn {turn_index} returned no assistant messages"
            conversation_messages.extend(assistant_messages)
            chat_reply = chat_client.extract_ai_reply(chat_response) or assistant_messages[-1]["content"]

            if turn_expect:
                _assert_reply(case_label, turn_expect, chat_reply)
                _safe_print(
                    f"TURN_ASSERT_RESULT case_name={case_name} turn_index={turn_index} asserted=true result=PASS"
                )
            else:
                _safe_print(
                    f"TURN_ASSERT_RESULT case_name={case_name} turn_index={turn_index} asserted=false result=SKIP"
                )
        except AssertionError as exc:
            failure = f"turn={turn_index} question={question} error={exc}"
            turn_failures.append(failure)
            _safe_print(f"TURN_ASSERT_RESULT case_name={case_name} turn_index={turn_index} result=FAIL detail={failure}")
        except Exception as exc:  # pragma: no cover
            failure = f"turn={turn_index} question={question} exception={type(exc).__name__}: {exc}"
            turn_failures.append(failure)
            _safe_print(f"TURN_ASSERT_RESULT case_name={case_name} turn_index={turn_index} result=ERROR detail={failure}")

        if turn_index < len(turns):
            _safe_print(
                f"TURN_WAIT case_name={case_name} runtime_username={runtime_username} "
                f"turn_index={turn_index} sleep_seconds=1"
            )
            time.sleep(1)

    assert not turn_failures, (
        f"[{case_label}] multiturn assertions failed, total {len(turn_failures)} turns\n"
        + "\n".join(turn_failures)
    )


_MULTITURN_SUITE = _load_multiturn_suite()
_MULTITURN_CASE_IDS = [
    case.get("name", f"multiturn_case_{index}")
    if isinstance(case, dict)
    else f"multiturn_case_{index}"
    for index, case in enumerate(_MULTITURN_SUITE["cases"], start=1)
]
_MULTITURN_CASES = _MULTITURN_SUITE["cases"]


def _create_multiturn_access_token(multiturn_runtime: Dict[str, Any]) -> str:
    if multiturn_runtime["auth_mode"] == "token":
        return multiturn_runtime["access_token"]

    auth_client_http = create_http_client(
        base_url=multiturn_runtime["api_base_url"],
        default_headers=multiturn_runtime["headers"],
    )
    auth_client = AuthAPI(client=auth_client_http)
    try:
        _safe_print(
            f"MULTITURN_LOGIN_REQUEST env={multiturn_runtime['target_env']} "
            f"account={multiturn_runtime['login_account']}"
        )
        login_response = auth_client.login(
            multiturn_runtime["login_account"],
            multiturn_runtime["login_password"],
        )
        _safe_print(
            f"MULTITURN_LOGIN_RESPONSE env={multiturn_runtime['target_env']} "
            f"status={login_response['status_code']} body={login_response['data']}"
        )
        assert login_response["status_code"] == 200
        assert login_response["data"].get("code") == 200
        access_token = login_response.get("access_token")
        assert access_token, "multiturn login succeeded but accessToken was empty"
        return access_token
    finally:
        auth_client_http.close()


class ChatMultiturnFlowScenario:
    def __init__(self, authenticated_apis: Dict[str, Any]) -> None:
        self.authenticated_apis = authenticated_apis

    def run_case(self, case_data: Dict[str, Any]) -> None:
        _run_multiturn_flow(self.authenticated_apis, case_data)


class TestChatMultiturnYamlFlow(unittest.TestCase):
    """多轮对话 YAML 测试套件：仅用户问题列表，同一 username 累积 messages。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.multiturn_runtime = load_context_runtime(_MULTITURN_SUITE["target_env"])
        _safe_print(
            f"MULTITURN_RUNTIME target_env={cls.multiturn_runtime['target_env']} "
            f"auth_mode={cls.multiturn_runtime['auth_mode']} "
            f"base_url={cls.multiturn_runtime['api_base_url']} "
            f"cases_file={_MULTITURN_SUITE['cases_file']} "
            f"cases_count={len(_MULTITURN_SUITE['cases'])}"
        )
        cls.multiturn_access_token = _create_multiturn_access_token(cls.multiturn_runtime)

    def setUp(self) -> None:
        self.client = create_http_client(
            base_url=self.multiturn_runtime["api_base_url"],
            default_headers=self.multiturn_runtime["headers"],
        )
        self.client.set_header("Authorization", _build_auth_header(self.multiturn_access_token))
        self.multiturn_authenticated_apis = {
            "chat_api": ChatAPI(client=self.client),
            "runtime": self.multiturn_runtime,
            "access_token": self.multiturn_access_token,
        }

    def tearDown(self) -> None:
        self.client.close()

    def _run_multiturn_case(self, case_data: Dict[str, Any]) -> None:
        ChatMultiturnFlowScenario(self.multiturn_authenticated_apis).run_case(case_data)


bind_case_tests(
    TestChatMultiturnYamlFlow,
    _MULTITURN_CASES,
    _MULTITURN_CASE_IDS,
    "_run_multiturn_case",
    "multiturn",
)


if __name__ == "__main__":
    unittest.main(verbosity=2)
