"""用户会话消息接口对象。

``GET /api/users/{user_id}/messages``：按用户 + 店铺 + 时间范围取完整聊天记录。

console 环境实测返回（``data`` 是消息数组，按 ``created_at`` 升序）：

    {
      "id": 70191220,
      "chat_id": 5778186,
      "role": "user",              # user / assistant
      "sender_id": 5045241,
      "receiver_id": 2389,
      "content": "……",
      "media_type": "text",
      "media_url": "",
      "created_at": 1789255692,
      "updated_at": 1789255692,
      "response_time": 0,
      "sender": "你是真der啊"
    }

注意：

- 该接口只认 ``shop_id`` / ``startTimeStr`` / ``endTimeStr`` 三个查询参数
  （缺 ``shop_id`` 报 400 ``shop_id无效``），时间用秒级时间戳。
- ``shop_id`` 必须在该 token 的授权店铺内，否则返回 404 ``店铺不存在``。
- 平台会在部分机器人消息前加 ``\\x07\\x08`` 之类的控制字符，这里统一清洗掉。
"""

from typing import Any, Dict, List, Union

from common.http_client import http_client

ENDPOINT_TEMPLATE = "/api/users/{user_id}/messages"

# 0x00-0x08 / 0x0b / 0x0c / 0x0e-0x1f 属于平台内部标记，不是聊天内容。
_CONTROL_CHARS = "".join(
    chr(code) for code in list(range(0x00, 0x09)) + [0x0B, 0x0C] + list(range(0x0E, 0x20))
)


class UserMessagesAPI:
    """``/api/users/{user_id}/messages`` 接口封装。"""

    def __init__(self, client=None) -> None:
        self.http_client = client or http_client

    def get_messages(
        self,
        *,
        user_id: Union[str, int],
        shop_id: Union[str, int],
        start_time: Union[str, int],
        end_time: Union[str, int],
    ) -> Dict[str, Any]:
        """取某个用户在某店铺、某时间范围内的完整聊天记录。"""
        params = {
            "shop_id": str(shop_id),
            "startTimeStr": str(start_time),
            "endTimeStr": str(end_time),
        }
        response = self.http_client.request(
            "GET", ENDPOINT_TEMPLATE.format(user_id=user_id), params=params
        )
        body: Dict[str, Any] = {}
        try:
            response.encoding = "utf-8"
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}

        data = body.get("data")
        messages = data if isinstance(data, list) else []
        return {
            "status_code": response.status_code,
            "params": params,
            "code": body.get("code"),
            "message": body.get("message") or body.get("msg"),
            "ok": body.get("code") == 200 and bool(messages),
            "messages": messages,
        }

    # ------------------------------------------------------------------
    # 清洗
    # ------------------------------------------------------------------
    @staticmethod
    def clean_content(content: Any) -> str:
        text = str(content or "")
        for char in _CONTROL_CHARS:
            text = text.replace(char, "")
        return text.strip()

    @classmethod
    def normalize_messages(cls, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把接口返回的消息整理成按时间升序、可直接消费的结构。"""
        normalized: List[Dict[str, Any]] = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue

            role = str(item.get("role", "")).strip().lower()
            if role not in {"user", "assistant"}:
                continue

            message: Dict[str, Any] = {
                "message_id": item.get("id"),
                "role": role,
                "content": cls.clean_content(item.get("content")),
                "media_type": str(item.get("media_type", "text")).strip().lower() or "text",
                "media_url": str(item.get("media_url", "") or "").strip(),
            }
            if item.get("sender"):
                message["sender"] = item.get("sender")
            if item.get("created_at") is not None:
                try:
                    message["created_at"] = int(float(item["created_at"]))
                except (TypeError, ValueError):
                    pass

            normalized.append(message)

        normalized.sort(key=lambda message: message.get("created_at") or 0)
        return normalized


user_messages_api = UserMessagesAPI()
