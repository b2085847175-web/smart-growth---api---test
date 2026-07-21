"""Data-driven pytest coverage for the /chat/answer interface.

Python owns the unified execution flow. Each data YAML owns its suite
classification, execution strategy, and concrete cases.
"""

import concurrent.futures
import os
import random
import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
import yaml

from api_object.auth_api import AuthAPI
from api_object.chat_api import ChatAPI
from api_object.quality_inspection_api import QualityInspectionAPI
from common.http_client import create_http_client
from config.context_runtime import load_context_runtime
from config.project_env import resolve_effective_env, resolve_suite_target_env
from testcases.common.case_order import normalize_last_order_info, resolve_last_order_time
from testcases.common.case_product import extract_product_from_content, normalize_inquiry_product
from testcases.common.paths import project_root


_PROJECT_ROOT = project_root()
_RUNTIME_CACHE: Dict[str, Dict[str, Any]] = {}
_TOKEN_CACHE: Dict[str, str] = {}

# testcase 负责指定 /chat/answer 默认参与执行的数据分类文件。
# 每个 YAML 文件名就是测试分类；执行策略仍由该文件顶部的 suite 配置管理。
ANSWER_DATA_FILES = [
    "data/answer/scene_multiturn_cases.yaml",
    "data/answer/multiturn_cases.yaml",
]

DEFAULT_SUITE_OPTIONS: Dict[str, Any] = {
    "name": "",
    "mode": "sequential",
    "quality": True,
    "assertions": True,
    "match_score": False,
    "final_reply_equals_chat": True,
    "repeat": 1,
    "workers": 1,
    "min_pass_rate": 0.8,
    "continue_on_failure": True,
    "turn_interval_seconds": 1,
    "run_interval_seconds": 1,
    "quality_retries": 10,
    "quality_retry_interval_seconds": 2,
}


def _safe_print(message: str) -> None:
    try:
        print(message)
    except UnicodeEncodeError:
        print(message.encode("gbk", errors="backslashreplace").decode("gbk"))


def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_int(value: Any, default: int, *, min_value: Optional[int] = None) -> int:
    parsed = default if value is None else int(value)
    if min_value is not None and parsed < min_value:
        raise ValueError(f"expected int >= {min_value}, got {parsed}")
    return parsed


def _as_float(value: Any, default: float, *, min_value: Optional[float] = None) -> float:
    parsed = default if value is None else float(value)
    if min_value is not None and parsed < min_value:
        raise ValueError(f"expected float >= {min_value}, got {parsed}")
    return parsed


def _pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def _slug(value: str, fallback: str = "case") -> str:
    slug = re.sub(r"[^0-9A-Za-z]+", "_", str(value or "").lower()).strip("_")
    if not slug:
        slug = fallback
    if slug[0].isdigit():
        slug = f"{fallback}_{slug}"
    return slug[:80]


def _case_param_id(suite_name: str, case_name: str, index: int, mode: str) -> str:
    return f"{_slug(suite_name, 'suite')}_{mode}_{index:03d}_{_slug(case_name)}"


def _build_auth_header(access_token: str) -> str:
    normalized = str(access_token or "").strip()
    if not normalized:
        return normalized
    if normalized.lower().startswith("bearer "):
        return normalized
    return f"Bearer {normalized}"


def _runtime_username(suite_name: str, case_name: str, run_index: Optional[int] = None) -> str:
    short_name = _slug(f"{suite_name}_{case_name}", "case")[:14]
    timestamp_ms = int(time.time() * 1000)
    rand4 = f"{random.getrandbits(16):04x}"
    run_part = f"_r{run_index}" if run_index is not None else ""
    return f"ans_{short_name}{run_part}_{timestamp_ms}_{rand4}"[:40]


def _resolve_case_files(raw_files: Iterable[str]) -> List[Path]:
    resolved: List[Path] = []
    for item in raw_files:
        raw = str(item or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = _PROJECT_ROOT / path
        if any(char in str(path) for char in "*?["):
            matches = sorted(path.parent.glob(path.name))
            resolved.extend(match for match in matches if match.is_file())
            continue
        resolved.append(path)
    missing = [str(path) for path in resolved if not path.exists()]
    if missing:
        raise FileNotFoundError(f"answer suite file(s) not found: {', '.join(missing)}")
    return resolved


def _selected_suite_names() -> List[str]:
    return [
        item.strip()
        for item in os.getenv("ANSWER_SUITES", "").split(",")
        if item.strip()
    ]


def _normalize_suite_options(data_path: Path, raw_suite: Dict[str, Any]) -> Dict[str, Any]:
    merged = {**DEFAULT_SUITE_OPTIONS, **(raw_suite or {})}
    suite_name = str(merged.get("name") or data_path.stem).strip()
    mode = str(merged.get("mode", "sequential")).strip().lower()
    if mode not in {"sequential", "parallel", "stability"}:
        raise ValueError(f"[{suite_name}] mode must be sequential/parallel/stability, got: {mode}")
    return {
        "suite_name": suite_name,
        "mode": mode,
        "data_path": str(data_path),
        "quality": _as_bool(merged.get("quality"), True),
        "assertions": _as_bool(merged.get("assertions"), True),
        "match_score": _as_bool(merged.get("match_score"), False),
        "final_reply_equals_chat": _as_bool(merged.get("final_reply_equals_chat"), True),
        "repeat": _as_int(merged.get("repeat"), 1, min_value=1),
        "workers": _as_int(merged.get("workers"), 1, min_value=1),
        "min_pass_rate": _as_float(merged.get("min_pass_rate"), 0.8, min_value=0),
        "continue_on_failure": _as_bool(merged.get("continue_on_failure"), True),
        "turn_interval_seconds": _as_float(merged.get("turn_interval_seconds"), 1.0, min_value=0),
        "run_interval_seconds": _as_float(merged.get("run_interval_seconds"), 1.0, min_value=0),
        "quality_retries": _as_int(merged.get("quality_retries"), 10, min_value=1),
        "quality_retry_interval_seconds": _as_float(
            merged.get("quality_retry_interval_seconds"),
            2.0,
            min_value=0,
        ),
    }


def _load_suite_cases(data_path: Path) -> Dict[str, Any]:
    suite_data = _read_yaml(data_path)
    options = _normalize_suite_options(data_path, suite_data.get("suite") or {})
    cases: List[Dict[str, Any]] = []
    target_env = resolve_suite_target_env(suite_data)
    raw_cases = suite_data.get("cases") or []
    if not isinstance(raw_cases, list):
        raise ValueError(f"[{options['suite_name']}] cases must be a list in {data_path}")
    for index, case in enumerate(raw_cases, start=1):
        if isinstance(case, dict):
            case_copy = dict(case)
            raw_name = str(case_copy.get("name", "")).strip() or f"case_{index}"
            case_copy["name"] = f"{data_path.stem}::{raw_name}"
            case_copy["_suite_file"] = str(data_path)
            cases.append(case_copy)
    return {
        **options,
        "target_env": resolve_effective_env(target_env or os.getenv("ENV", "dev")),
        "case_files": [str(data_path)],
        "cases": cases,
    }


def _load_answer_suites() -> List[Dict[str, Any]]:
    data_files = _resolve_case_files(ANSWER_DATA_FILES)
    selected_names = _selected_suite_names()
    suites = [_load_suite_cases(path) for path in data_files]
    if not selected_names:
        return suites
    selected = [suite for suite in suites if suite["suite_name"] in selected_names]
    found_names = {suite["suite_name"] for suite in selected}
    unknown = [name for name in selected_names if name not in found_names]
    if unknown:
        available = [suite["suite_name"] for suite in suites]
        raise ValueError(f"unknown ANSWER_SUITES: {unknown}; available={available}")
    return selected


def _answer_items() -> List[Any]:
    params: List[Any] = []
    for suite in _load_answer_suites():
        mode = suite["mode"]
        cases = suite["cases"]
        if mode == "parallel":
            params.append(
                pytest.param(
                    {"kind": "parallel_suite", "suite": suite},
                    id=f"{_slug(suite['suite_name'], 'suite')}_parallel_all",
                )
            )
            continue
        for index, case in enumerate(cases, start=1):
            params.append(
                pytest.param(
                    {"kind": "case", "suite": suite, "case": case},
                    id=_case_param_id(suite["suite_name"], case.get("name", ""), index, mode),
                )
            )
    return params


def _normalize_context_messages(raw_messages: Any, case_name: str) -> List[Dict[str, str]]:
    if not raw_messages:
        return []
    if not isinstance(raw_messages, list):
        raise ValueError(f"[{case_name}] context_messages must be a list")
    context_messages: List[Dict[str, str]] = []
    for index, message in enumerate(raw_messages, start=1):
        if not isinstance(message, dict):
            raise ValueError(f"[{case_name}] context_messages[{index}] must be a dict")
        role = str(message.get("role", "")).strip().lower()
        content = str(message.get("content", "")).strip()
        if role not in {"user", "assistant"}:
            raise ValueError(f"[{case_name}] context_messages[{index}].role must be user or assistant")
        if not content:
            raise ValueError(f"[{case_name}] context_messages[{index}].content cannot be empty")
        context_messages.append({"role": role, "content": content})
    return context_messages


def _prepare_context_messages(messages: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    if not messages:
        return []
    start_at = time.time() - len(messages) - 5
    return [
        {
            "role": message["role"],
            "content": message["content"],
            "created_at": start_at + index,
        }
        for index, message in enumerate(messages)
    ]


def _normalize_expect(expect_data: Any) -> Dict[str, Any]:
    if isinstance(expect_data, str):
        return {
            "knowledge": {
                "stats_contains": {
                    "scene_knowledge": [expect_data],
                }
            }
        }
    if not isinstance(expect_data, dict):
        return {}
    normalized = dict(expect_data)
    scene_alias = normalized.get("scene")
    if scene_alias is not None:
        scene_values = scene_alias if isinstance(scene_alias, list) else [scene_alias]
        knowledge = normalized.setdefault("knowledge", {})
        stats_contains = knowledge.setdefault("stats_contains", {})
        stats_contains["scene_knowledge"] = [str(item) for item in scene_values]
    return normalized


def _normalize_turns(case_data: Dict[str, Any], case_name: str) -> List[Dict[str, Any]]:
    if "turns" in case_data:
        raw_turns = case_data.get("turns") or []
    elif "questions" in case_data:
        raw_turns = case_data.get("questions") or []
    elif "request" in case_data:
        request_data = case_data.get("request") or {}
        raw_turns = request_data.get("questions") or [request_data.get("message")]
    else:
        raw_turns = []
    if not isinstance(raw_turns, list):
        raise ValueError(f"[{case_name}] turns/questions must be a list")
    turns: List[Dict[str, Any]] = []
    case_expect = _normalize_expect(case_data.get("expect", {}))
    for index, item in enumerate(raw_turns, start=1):
        if isinstance(item, str):
            question = item.strip()
            expect: Dict[str, Any] = {}
            last_order_info = None
        elif isinstance(item, dict):
            question = str(item.get("question") or item.get("message") or "").strip()
            expect = _normalize_expect(item.get("expect", {}))
            last_order_info = item.get("last_order_info")
        else:
            raise ValueError(f"[{case_name}] turn {index} must be a string or dict")
        if not question:
            raise ValueError(f"[{case_name}] turn {index} question cannot be empty")
        turns.append({"question": question, "expect": expect, "last_order_info": last_order_info})
    if turns and case_expect:
        turns[-1]["expect"] = {**turns[-1].get("expect", {}), **case_expect}
    if not turns:
        raise ValueError(f"[{case_name}] turns cannot be empty")
    return turns


def _normalize_case(case_data: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(case_data, dict):
        raise ValueError(f"answer case must be a dict, got: {type(case_data).__name__}")
    case_name = str(case_data.get("name", "")).strip()
    if not case_name:
        raise ValueError("answer case name is required")
    context_messages = _normalize_context_messages(case_data.get("context_messages"), case_name)
    inquiry_product = case_data.get("inquiry_product")
    product_url = str(case_data.get("product_url", "")).strip()
    if product_url and not inquiry_product:
        extracted = extract_product_from_content(product_url)
        if extracted:
            inquiry_product = extracted
    return {
        "name": case_name,
        "context_messages": context_messages,
        "turns": _normalize_turns(case_data, case_name),
        "inquiry_product": inquiry_product,
        "product_url": product_url,
        "last_order_info": case_data.get("last_order_info"),
        "_suite_file": case_data.get("_suite_file"),
    }


def _create_access_token(runtime: Dict[str, Any]) -> str:
    if runtime["auth_mode"] == "token":
        return runtime["access_token"]
    auth_client_http = create_http_client(
        base_url=runtime["api_base_url"],
        default_headers=runtime["headers"],
    )
    auth_client = AuthAPI(client=auth_client_http)
    try:
        _safe_print(f"ANSWER_LOGIN_REQUEST env={runtime['target_env']} account={runtime['login_account']}")
        login_response = auth_client.login(runtime["login_account"], runtime["login_password"])
        login_data = login_response["data"]
        _safe_print(
            f"ANSWER_LOGIN_RESPONSE env={runtime['target_env']} "
            f"status={login_response['status_code']} code={login_data.get('code')} "
            f"message={login_data.get('message')} token_present={bool(login_response.get('access_token'))}"
        )
        assert login_response["status_code"] == 200
        assert login_data.get("code") == 200
        access_token = login_response.get("access_token")
        assert access_token, "answer login succeeded but accessToken was empty"
        return access_token
    finally:
        auth_client_http.close()


def _runtime_for(target_env: str) -> Dict[str, Any]:
    normalized_env = resolve_effective_env(target_env)
    if normalized_env not in _RUNTIME_CACHE:
        runtime = load_context_runtime(normalized_env)
        _safe_print(
            f"ANSWER_RUNTIME target_env={runtime['target_env']} "
            f"auth_mode={runtime['auth_mode']} base_url={runtime['api_base_url']}"
        )
        _RUNTIME_CACHE[normalized_env] = runtime
    return _RUNTIME_CACHE[normalized_env]


def _token_for(runtime: Dict[str, Any]) -> str:
    env_name = runtime["target_env"]
    if env_name not in _TOKEN_CACHE:
        _TOKEN_CACHE[env_name] = _create_access_token(runtime)
    return _TOKEN_CACHE[env_name]


def _client_apis(runtime: Dict[str, Any], access_token: str) -> Tuple[Any, Dict[str, Any]]:
    client = create_http_client(
        base_url=runtime["api_base_url"],
        default_headers=runtime["headers"],
    )
    client.set_header("Authorization", _build_auth_header(access_token))
    return client, {
        "chat_api": ChatAPI(client=client),
        "quality_inspection_api": QualityInspectionAPI(client=client),
        "runtime": runtime,
        "access_token": access_token,
    }


def _field_values(expected: Dict[str, Any], section: str, key: str) -> Any:
    values = []
    if isinstance(expected.get(section), dict):
        values.append(expected[section].get(key))
    if expected.get(key) is not None:
        values.append(expected.get(key))
    for value in values:
        if value is not None:
            return value
    return None


def _list_value(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _assert_contains_subset(case_label: str, field_name: str, expected_values: List[str], actual_values: List[str]) -> None:
    actual_list = list(actual_values or [])
    for expected in expected_values or []:
        assert expected in actual_list, (
            f"[{case_label}] {field_name} missing expected value: {expected} | actual: {actual_list}"
        )


def _scene_expected_hit(expected_scene: str, actual_scenes: List[str]) -> bool:
    expected_scene = str(expected_scene or "").strip()
    if not expected_scene:
        return True
    normalized_actuals = [str(scene or "").strip() for scene in actual_scenes if str(scene or "").strip()]
    return expected_scene in normalized_actuals


def _assert_response(case_label: str, expected: Dict[str, Any], chat_response: Dict[str, Any], chat_reply: str) -> None:
    response_expect = expected.get("response", {}) if isinstance(expected.get("response"), dict) else {}
    expected_status = response_expect.get("status_code")
    if expected_status is not None:
        assert chat_response["status_code"] == expected_status, (
            f"[{case_label}] response status mismatch | expected: {expected_status} | actual: {chat_response['status_code']}"
        )
    expected_code = response_expect.get("code")
    if expected_code is not None:
        actual_code = chat_response["data"].get("code")
        assert actual_code == expected_code, (
            f"[{case_label}] response code mismatch | expected: {expected_code} | actual: {actual_code}"
        )
    reply_contains = _list_value(response_expect.get("reply_contains")) + _list_value(expected.get("reply_contains"))
    for expected_text in reply_contains:
        assert str(expected_text) in chat_reply, (
            f"[{case_label}] chat reply missing expected text: {expected_text} | actual: {chat_reply}"
        )
    for unexpected_text in _list_value(response_expect.get("reply_not_contains")):
        assert str(unexpected_text) not in chat_reply, (
            f"[{case_label}] chat reply contains unexpected text: {unexpected_text} | actual: {chat_reply}"
        )


def _expect_requires_quality(expected: Dict[str, Any]) -> bool:
    return any(key in expected for key in ("quality", "knowledge", "actions"))


def _merge_map_expect(expected: Dict[str, Any], section: str, key: str) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    if isinstance(expected.get(section), dict) and isinstance(expected[section].get(key), dict):
        merged.update(expected[section][key])
    if section == "knowledge" and isinstance(expected.get("quality"), dict) and isinstance(expected["quality"].get(key), dict):
        merged.update(expected["quality"][key])
    return merged


def _assert_quality_record(
    case_label: str,
    expected: Dict[str, Any],
    normalized_record: Dict[str, Any],
    chat_reply: str,
    final_reply_equals_chat: bool,
) -> None:
    quality_expect = expected.get("quality", {}) if isinstance(expected.get("quality"), dict) else {}
    if final_reply_equals_chat or quality_expect.get("final_reply_equals_chat"):
        final_response_text = normalized_record["final_reply"]
        assert final_response_text, f"[{case_label}] quality final_response was empty"
        assert final_response_text == chat_reply, (
            f"[{case_label}] chat reply and quality final_reply mismatch | "
            f"chat: {chat_reply} | quality: {final_response_text}"
        )

    for expected_text in _list_value(quality_expect.get("reply_contains")):
        assert str(expected_text) in normalized_record["final_reply"], (
            f"[{case_label}] quality final_reply missing expected text: {expected_text} | "
            f"actual: {normalized_record['final_reply']}"
        )

    expected_level = quality_expect.get("level")
    if expected_level:
        assert normalized_record["level"] == expected_level, (
            f"[{case_label}] level mismatch | expected: {expected_level} | actual: {normalized_record['level']}"
        )
    _assert_contains_subset(
        case_label,
        "categories",
        _list_value(quality_expect.get("categories_contains")),
        normalized_record["categories"],
    )

    stats_expect = _merge_map_expect(expected, "knowledge", "stats_contains")
    for stat_key, expected_names in stats_expect.items():
        expected_list = _list_value(expected_names)
        actual_values = normalized_record["stats_map"].get(stat_key, [])
        if stat_key == "scene_knowledge":
            for expected_scene in expected_list:
                assert _scene_expected_hit(str(expected_scene), actual_values), (
                    f"[{case_label}] stats.scene_knowledge missing expected scene: {expected_scene} | "
                    f"actual: {actual_values}"
                )
            continue
        _assert_contains_subset(case_label, f"stats.{stat_key}", expected_list, actual_values)

    details_expect = _merge_map_expect(expected, "knowledge", "details_contains")
    for detail_key, expected_values in details_expect.items():
        actual_values = normalized_record["details_map"].get(detail_key, [])
        for expected_value in _list_value(expected_values):
            assert any(str(expected_value) in actual for actual in actual_values), (
                f"[{case_label}] details.{detail_key} missing expected text: {expected_value} | "
                f"actual: {actual_values}"
            )

    actions_expect = {}
    if isinstance(expected.get("actions"), dict):
        actions_expect.update(expected["actions"])
    if isinstance(quality_expect.get("actions"), dict):
        actions_expect.update(quality_expect["actions"])
    _assert_contains_subset(case_label, "actions.types", _list_value(actions_expect.get("types")), normalized_record["action_types"])
    _assert_contains_subset(
        case_label,
        "actions.forward_scenes",
        _list_value(actions_expect.get("forward_scenes")),
        normalized_record["forward_scenes"],
    )


def _assert_match_score(case_label: str, expected: Dict[str, Any], chat_response_data: Dict[str, Any]) -> None:
    match_score_expect = expected.get("match_score", {})
    if not isinstance(match_score_expect, dict) or not match_score_expect:
        return
    match_score = chat_response_data.get("match_score")
    if match_score_expect.get("exists"):
        assert match_score is not None, f"[{case_label}] match_score field is missing"
        assert isinstance(match_score, (int, float)), (
            f"[{case_label}] match_score must be numeric, got: {type(match_score).__name__}"
        )
    range_expect = match_score_expect.get("range")
    if range_expect and match_score is not None:
        assert len(range_expect) == 2, f"[{case_label}] match_score.range must be [min, max]"
        min_val, max_val = range_expect
        assert min_val <= match_score <= max_val, (
            f"[{case_label}] match_score out of range [{min_val}, {max_val}] | actual: {match_score}"
        )
    expected_equals = match_score_expect.get("equals")
    if expected_equals is not None and match_score is not None:
        assert match_score == expected_equals, (
            f"[{case_label}] match_score mismatch | expected: {expected_equals} | actual: {match_score}"
        )


def _query_turn_quality(
    quality_client: QualityInspectionAPI,
    suite: Dict[str, Any],
    runtime_username: str,
    shop_id: str,
    turn_send_at: float,
    turn_response_at: float,
    chat_reply: str,
    chat_response: Dict[str, Any],
    user_message: str,
    case_name: str,
    turn_index: int,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    selected_record = None
    quality_response = None
    for attempt in range(1, suite["quality_retries"] + 1):
        query_start = int(turn_send_at) - 5
        query_end = int(turn_response_at) + 30 + (attempt * 2)
        quality_response = quality_client.get_user_detail(
            username=runtime_username,
            shop_id=shop_id,
            start_time=query_start,
            end_time=query_end,
        )
        _safe_print(
            f"ANSWER_QUALITY_QUERY suite={suite['suite_name']} case_name={case_name} "
            f"turn_index={turn_index} attempt={attempt} status={quality_response['status_code']} "
            f"code={quality_response['code']} records={len(quality_response['records'])}"
        )
        assert quality_response["status_code"] == 200
        assert quality_response["code"] == 200
        selected_record = quality_client.find_best_matching_record(
            quality_response["records"],
            runtime_username,
            shop_id,
            turn_send_at,
            turn_response_at,
            chat_reply,
            chat_response=chat_response,
            user_message=user_message,
        )
        if selected_record:
            break
        if suite["quality_retry_interval_seconds"] > 0:
            time.sleep(suite["quality_retry_interval_seconds"])
    return quality_response, selected_record


def _run_answer_case(
    apis: Dict[str, Any],
    suite: Dict[str, Any],
    case_data: Dict[str, Any],
    *,
    run_index: Optional[int] = None,
) -> None:
    chat_client: ChatAPI = apis["chat_api"]
    quality_client: QualityInspectionAPI = apis["quality_inspection_api"]
    runtime = apis["runtime"]
    normalized_case = _normalize_case(case_data)
    case_name = normalized_case["name"]
    runtime_username = _runtime_username(suite["suite_name"], case_name, run_index)
    case_label = f"{suite['suite_name']}::{case_name}|username={runtime_username}"
    context_messages = normalized_case["context_messages"]
    conversation_messages = _prepare_context_messages(context_messages)
    case_last_order_info = normalize_last_order_info(normalized_case.get("last_order_info"))
    inquiry_product = normalize_inquiry_product(
        normalized_case.get("inquiry_product"),
        context_messages,
        runtime["platform"],
    )
    turn_failures: List[str] = []

    _safe_print(
        f"ANSWER_CASE suite={suite['suite_name']} mode={suite['mode']} case_name={case_name} "
        f"username={runtime_username} turns={len(normalized_case['turns'])} "
        f"quality={suite['quality']} assertions={suite['assertions']} inquiry_product={inquiry_product}"
    )

    for turn_index, turn in enumerate(normalized_case["turns"], start=1):
        question = turn["question"]
        expected = _normalize_expect(turn.get("expect", {}))
        assertions_enabled = suite["assertions"]
        turn_send_at = time.time()
        turn_last_order_info = normalize_last_order_info(turn.get("last_order_info")) or case_last_order_info
        last_order_time = resolve_last_order_time(turn_last_order_info, turn_send_at)
        try:
            conversation_messages.append(
                {
                    "role": "user",
                    "content": question,
                    "created_at": turn_send_at,
                }
            )
            chat_response = chat_client.chat_answer(
                account=runtime["chat_account"],
                messages=list(conversation_messages),
                inquiry_product=inquiry_product,
                platform=runtime["platform"],
                shop_id=runtime["shop_id"],
                shop_name=runtime["shop_name"],
                username=runtime_username,
                is_test=runtime["is_test"],
                last_order_info=turn_last_order_info,
                last_order_time=last_order_time,
            )
            turn_response_at = time.time()
            _safe_print(
                f"ANSWER_RESPONSE suite={suite['suite_name']} case_name={case_name} "
                f"turn_index={turn_index} status={chat_response['status_code']} body={chat_response['data']}"
            )
            assert chat_response["status_code"] == 200, (
                f"[{case_label}] turn {turn_index} response status mismatch | "
                f"expected: 200 | actual: {chat_response['status_code']} | body: {chat_response['data']}"
            )
            assert chat_response["data"].get("code") == 200, (
                f"[{case_label}] turn {turn_index} response code mismatch | "
                f"expected: 200 | actual: {chat_response['data'].get('code')} | body: {chat_response['data']}"
            )

            assistant_messages = chat_client.extract_assistant_messages(
                chat_response,
                response_received_at=turn_response_at,
            )
            assert assistant_messages, f"[{case_label}] turn {turn_index} returned no assistant messages"
            if assistant_messages:
                conversation_messages.extend(assistant_messages)
            chat_reply = chat_client.extract_ai_reply(chat_response) or (
                assistant_messages[-1]["content"] if assistant_messages else ""
            )

            if assertions_enabled and expected:
                _assert_response(case_label, expected, chat_response, chat_reply)
                if suite["match_score"] or expected.get("match_score"):
                    _assert_match_score(case_label, expected, chat_response["data"].get("data", {}))

            quality_enabled = suite["quality"] or (assertions_enabled and _expect_requires_quality(expected))
            if quality_enabled:
                quality_response, selected_record = _query_turn_quality(
                    quality_client=quality_client,
                    suite=suite,
                    runtime_username=runtime_username,
                    shop_id=runtime["shop_id"],
                    turn_send_at=turn_send_at,
                    turn_response_at=turn_response_at,
                    chat_reply=chat_reply,
                    chat_response=chat_response,
                    user_message=question,
                    case_name=case_name,
                    turn_index=turn_index,
                )
                assert quality_response is not None
                assert selected_record is not None, f"[{case_label}] turn {turn_index} did not match quality record"
                normalized_record = quality_client.normalize_quality_record(selected_record)
                if expected or suite["final_reply_equals_chat"]:
                    _assert_quality_record(
                        case_label,
                        expected,
                        normalized_record,
                        chat_reply,
                        suite["final_reply_equals_chat"],
                    )

            _safe_print(
                f"ANSWER_TURN_RESULT suite={suite['suite_name']} case_name={case_name} "
                f"turn_index={turn_index} result=PASS asserted={bool(expected)}"
            )
        except AssertionError as exc:
            failure = f"turn={turn_index} question={question} error={exc}"
            turn_failures.append(failure)
            _safe_print(f"ANSWER_TURN_RESULT suite={suite['suite_name']} case_name={case_name} result=FAIL {failure}")
        except Exception as exc:  # pragma: no cover
            failure = f"turn={turn_index} question={question} exception={type(exc).__name__}: {exc}"
            turn_failures.append(failure)
            _safe_print(f"ANSWER_TURN_RESULT suite={suite['suite_name']} case_name={case_name} result=ERROR {failure}")

        if turn_index < len(normalized_case["turns"]) and suite["turn_interval_seconds"] > 0:
            time.sleep(suite["turn_interval_seconds"])

    assert not turn_failures, (
        f"[{case_label}] answer case failed, total {len(turn_failures)} turns\n" + "\n".join(turn_failures)
    )


def _run_case_with_client(suite: Dict[str, Any], case_data: Dict[str, Any], *, run_index: Optional[int] = None) -> None:
    runtime = _runtime_for(suite["target_env"])
    access_token = _token_for(runtime)
    client, apis = _client_apis(runtime, access_token)
    try:
        _run_answer_case(apis, suite, case_data, run_index=run_index)
    finally:
        client.close()


def _normalize_failure_reason(error_text: str) -> str:
    reason = str(error_text or "").strip()
    if not reason:
        return "unknown_failure"
    if "] " in reason:
        reason = reason.split("] ", 1)[1]
    if " | " in reason:
        reason = reason.split(" | ", 1)[0]
    return reason[:160]


def _run_stability_case(suite: Dict[str, Any], case_data: Dict[str, Any]) -> None:
    case_name = str(case_data.get("name", "unknown"))
    hit_runs = 0
    failure_counter: Counter[str] = Counter()
    repeat = suite["repeat"]
    for run_index in range(1, repeat + 1):
        try:
            _run_case_with_client(suite, case_data, run_index=run_index)
            hit_runs += 1
            _safe_print(f"ANSWER_STABILITY_RUN suite={suite['suite_name']} case_name={case_name} run={run_index}/{repeat} hit=1")
        except AssertionError as exc:
            reason = _normalize_failure_reason(str(exc))
            failure_counter[reason] += 1
            _safe_print(
                f"ANSWER_STABILITY_RUN suite={suite['suite_name']} case_name={case_name} "
                f"run={run_index}/{repeat} hit=0 reason={reason}"
            )
            if not suite["continue_on_failure"]:
                break
        if run_index < repeat and suite["run_interval_seconds"] > 0:
            time.sleep(suite["run_interval_seconds"])

    hit_probability = hit_runs / repeat
    top_fail_reasons = [f"{reason}:{count}" for reason, count in failure_counter.most_common(5)]
    _safe_print(
        f"ANSWER_STABILITY_SUMMARY suite={suite['suite_name']} case_name={case_name} "
        f"total_runs={repeat} hit_runs={hit_runs} hit_probability={_pct(hit_probability)} "
        f"threshold={_pct(suite['min_pass_rate'])} top_fail_reasons={top_fail_reasons if top_fail_reasons else []}"
    )
    assert hit_probability >= suite["min_pass_rate"], (
        f"[{case_name}] stability below threshold | hit_probability={_pct(hit_probability)} "
        f"< threshold={_pct(suite['min_pass_rate'])} | top_fail_reasons={top_fail_reasons if top_fail_reasons else []}"
    )


def _run_parallel_suite(suite: Dict[str, Any]) -> None:
    cases = suite["cases"]
    if not cases:
        pytest.skip(f"[{suite['suite_name']}] no cases to run")
    failures: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=suite["workers"]) as executor:
        future_to_case = {
            executor.submit(_run_case_with_client, suite, case): case
            for case in cases
        }
        for future in concurrent.futures.as_completed(future_to_case):
            case = future_to_case[future]
            case_name = case.get("name", "unknown") if isinstance(case, dict) else "unknown"
            try:
                future.result()
            except Exception as exc:
                failures.append(f"[{case_name}] {type(exc).__name__}: {exc}")
    passed = len(cases) - len(failures)
    _safe_print(
        f"ANSWER_PARALLEL_SUMMARY suite={suite['suite_name']} total={len(cases)} "
        f"passed={passed} failed={len(failures)} workers={suite['workers']}"
    )
    if failures:
        detail = "\n".join(f"  FAILURE [{i}] {msg}" for i, msg in enumerate(failures, 1))
        pytest.fail(f"[{suite['suite_name']}] parallel failed {len(failures)}/{len(cases)} cases:\n{detail}")


@pytest.mark.parametrize("answer_item", _answer_items())
def test_answer_yaml(answer_item: Dict[str, Any]) -> None:
    """Unified /chat/answer test entrypoint driven by data YAML suite blocks."""
    if answer_item["kind"] == "parallel_suite":
        _run_parallel_suite(answer_item["suite"])
        return
    suite = answer_item["suite"]
    case_data = answer_item["case"]
    if suite["mode"] == "stability":
        _run_stability_case(suite, case_data)
        return
    _run_case_with_client(suite, case_data)
