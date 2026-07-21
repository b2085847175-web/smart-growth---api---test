"""从 YAML 配置解析 last_order_info 订单传参。"""

from typing import Any, Dict, Optional


def normalize_last_order_info(order_info: Any) -> Optional[Dict[str, Any]]:
    """归一化 last_order_info，字段与 /chat/answer 接口一致：id、status、create_time。"""
    if not isinstance(order_info, dict):
        return None

    normalized: Dict[str, Any] = {}
    for key, value in order_info.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        normalized[key] = value

    if not normalized:
        return None

    if "id" in normalized:
        normalized["id"] = str(normalized["id"])
    if "status" in normalized:
        normalized["status"] = str(normalized["status"])
    if "create_time" in normalized:
        normalized["create_time"] = int(normalized["create_time"])

    return normalized


def resolve_last_order_time(
    last_order_info: Optional[Dict[str, Any]],
    fallback: float,
) -> int:
    """优先使用 last_order_info.create_time，否则回退到当前轮次时间戳。"""
    if last_order_info and last_order_info.get("create_time") is not None:
        return int(last_order_info["create_time"])
    return int(fallback)
