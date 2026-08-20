"""Random-account coverage for the /chat/answer interface.

This file keeps the existing /chat/answer request style and only changes the
account parameter to a time-based value for each test case.
"""

import os
import time
import uuid
from typing import Any, Dict

import pytest

from config.project_env import resolve_effective_env
from testcases.answer.test_answer_yaml import (
    _client_apis,
    _run_answer_case,
    _runtime_for,
    _safe_print,
    _token_for,
)


RANDOM_ACCOUNT_SUITE: Dict[str, Any] = {
    "suite_name": "random_account_smoke",
    "mode": "sequential",
    "target_env": resolve_effective_env(os.getenv("ENV", "dev")),
    "quality": False,
    "assertions": False,
    "match_score": False,
    "final_reply_equals_chat": False,
    "turn_interval_seconds": 1,
}

RANDOM_ACCOUNT_CASES = [
    {
        "name": "random_account_recommend_shampoo",
        "questions": ["推荐一款洗发水"],
    },
    {
        "name": "random_account_recommend_body_wash",
        "questions": ["推荐一款沐浴露"],
    },
    {
        "name": "random_account_product_follow_up",
        "questions": [
            "推荐一款洗发水",
            "这款适合油性头发吗",
            "有没有优惠活动",
        ],
    },
    {
        "name": "random_account_simple_greeting",
        "questions": ["你好，在吗"],
    },
    {
        "name": "random_account_recommend_oily_shampoo",
        "questions": ["我是油性头发，推荐一款洗发水"],
    },
    {
        "name": "random_account_recommend_dandruff_shampoo",
        "questions": ["头皮屑比较多，有没有合适的洗发水"],
    },
    {
        "name": "random_account_recommend_dry_hair_shampoo",
        "questions": ["头发比较干枯，推荐一款洗发水"],
    },
    {
        "name": "random_account_recommend_body_wash_moisturizing",
        "questions": ["推荐一款保湿一点的沐浴露"],
    },
    {
        "name": "random_account_recommend_body_wash_fragrance",
        "questions": ["想要留香久一点的沐浴露，推荐一款"],
    },
    {
        "name": "random_account_recommend_sensitive_skin_body_wash",
        "questions": ["敏感肌可以用哪款沐浴露"],
    },
    {
        "name": "random_account_shampoo_price_follow_up",
        "questions": [
            "推荐一款洗发水",
            "多少钱",
        ],
    },
    {
        "name": "random_account_body_wash_price_follow_up",
        "questions": [
            "推荐一款沐浴露",
            "这款多少钱",
        ],
    },
    {
        "name": "random_account_shampoo_usage_follow_up",
        "questions": [
            "推荐一款控油洗发水",
            "怎么使用效果更好",
        ],
    },
    {
        "name": "random_account_body_wash_usage_follow_up",
        "questions": [
            "推荐一款沐浴露",
            "每天都可以用吗",
        ],
    },
    {
        "name": "random_account_shipping_question",
        "questions": ["买完一般多久发货"],
    },
    {
        "name": "random_account_return_question",
        "questions": ["收到后不合适可以退货吗"],
    },
    {
        "name": "random_account_order_status_question",
        "questions": ["订单状态在哪里查看"],
    },
    {
        "name": "random_account_invoice_question",
        "questions": ["购买后可以开发票吗"],
    },
    {
        "name": "random_account_promotion_question",
        "questions": ["现在店铺有什么优惠活动吗"],
    },
    {
        "name": "random_account_compare_shampoo_body_wash",
        "questions": [
            "推荐一款洗发水",
            "你们家沐浴露也推荐一款",
        ],
    },
]


def _random_account(base_account: str) -> str:
    timestamp_ms = int(time.time() * 1000)
    suffix = uuid.uuid4().hex[:6]
    base = str(base_account or "account").strip() or "account"
    return f"{base}_{timestamp_ms}_{suffix}"


def _run_random_account_case(case_data: Dict[str, Any]) -> None:
    runtime = _runtime_for(RANDOM_ACCOUNT_SUITE["target_env"])
    random_account = _random_account(runtime["chat_account"])
    runtime_with_random_account = {
        **runtime,
        "chat_account": random_account,
    }
    access_token = _token_for(runtime)
    client, apis = _client_apis(runtime_with_random_account, access_token)
    try:
        _safe_print(
            "RANDOM_ACCOUNT_CASE "
            f"case_name={case_data['name']} account={random_account}"
        )
        _run_answer_case(apis, RANDOM_ACCOUNT_SUITE, case_data)
    finally:
        client.close()


@pytest.mark.parametrize(
    "case_data",
    [
        pytest.param(case_data, id=case_data["name"])
        for case_data in RANDOM_ACCOUNT_CASES
    ],
)
def test_answer_random_account(case_data: Dict[str, Any]) -> None:
    """Verify /chat/answer can reply normally when account changes per case."""
    _run_random_account_case(case_data)
