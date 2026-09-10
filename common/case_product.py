"""从 YAML context_messages 或显式配置解析 inquiry_product。"""

import re
from typing import Any, Dict, List, Optional

_PRODUCT_ID_PATTERNS = (
    re.compile(r"[?&]id=(\d+)", re.I),
    re.compile(r"[?&]goods_id=(\d+)", re.I),
    re.compile(r"item\.jd\.com/(\d+)", re.I),
)

DEFAULT_PRODUCT_URL = "https://item.taobao.com/item.htm?id=764834167209"
DEFAULT_PRODUCT_ID = "764834167209"


def extract_product_id_from_url(url: str) -> Optional[str]:
    for pattern in _PRODUCT_ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def extract_product_from_content(content: str) -> Optional[Dict[str, str]]:
    text = str(content or "").strip()
    if not text.startswith("http"):
        return None
    product_id = extract_product_id_from_url(text)
    if not product_id:
        return None
    return {"id": product_id, "url": text}


def normalize_inquiry_product(
    explicit_product: Any,
    context_messages: List[Dict[str, str]],
    platform: str,
) -> Dict[str, Any]:
    product: Dict[str, Any] = {}
    if isinstance(explicit_product, dict):
        product = {
            key: value
            for key, value in explicit_product.items()
            if value is not None and str(value).strip()
        }

    if not product.get("id"):
        for message in context_messages:
            if message.get("role") != "user":
                continue
            extracted = extract_product_from_content(message.get("content", ""))
            if extracted:
                product.setdefault("id", extracted["id"])
                product.setdefault("url", extracted["url"])
                break

    if not product:
        return {}

    product["id"] = str(product["id"])
    product.setdefault("url", product.get("url") or "")
    product.setdefault("platform", platform)
    return product


def default_product_context_message() -> Dict[str, str]:
    return {"role": "user", "content": DEFAULT_PRODUCT_URL}


def case_has_product_id(case: Dict[str, Any], product_id: str = DEFAULT_PRODUCT_ID) -> bool:
    for message in case.get("context_messages") or []:
        if not isinstance(message, dict):
            continue
        if product_id in str(message.get("content", "")):
            return True
    return False


def ensure_case_product_context(
    case: Dict[str, Any],
    product_id: str = DEFAULT_PRODUCT_ID,
    product_url: str = DEFAULT_PRODUCT_URL,
) -> Dict[str, Any]:
    case_copy = dict(case)
    context_messages = [dict(item) for item in (case_copy.get("context_messages") or []) if isinstance(item, dict)]
    if not case_has_product_id(case_copy, product_id):
        context_messages.insert(0, {"role": "user", "content": product_url})
    case_copy["context_messages"] = context_messages
    return case_copy
