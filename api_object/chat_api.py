import time
import uuid
from typing import Any, Dict, List, Optional

from common.http_client import http_client
from config.settings import settings


class ChatAPI:
    def __init__(self, client=None) -> None:
        self.http_client = client or http_client
        self.base_url = settings.get_api_base_url()

    def chat_answer(
        self,
        account: str,
        messages: List[Dict[str, Any]],
        inquiry_product: Optional[Dict[str, Any]] = None,
        is_test: bool = True,
        last_order_info: Optional[Dict[str, Any]] = None,
        last_order_time: Optional[int] = None,
        platform: str = "tmall",
        request_id: Optional[str] = None,
        shop_id: str = "585",
        shop_name: str = "儒意化妆品旗舰店",
        username: str = "tb_xxx",
        **kwargs,
    ) -> Dict[str, Any]:
        prepared_messages = []
        for message in messages:
            current = dict(message)
            current["created_at"] = int(current.get("created_at", time.time()))
            prepared_messages.append(current)

        payload = {
            "account": account,
            "inquiry_product": inquiry_product or {},
            "is_test": is_test,
            "last_order_info": last_order_info,
            "last_order_time": last_order_time or int(time.time()),
            "messages": prepared_messages,
            "platform": platform,
            "request_id": request_id or str(uuid.uuid4()),
            "shop_id": shop_id,
            "shop_name": shop_name,
            "username": username,
            **kwargs,
        }

        response = self.http_client.post("/chat/answer", json=payload)
        return {
            "status_code": response.status_code,
            "payload": payload,
            "data": response.json(),
        }

    @staticmethod
    def _extract_ai_actions(response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        actions = response_data.get("data", {}).get("data", {}).get("ai_actions", [])
        if not actions:
            actions = response_data.get("data", {}).get("ai_actions", [])
        return actions

    @classmethod
    def extract_action_types(cls, response_data: Dict[str, Any]) -> List[str]:
        """Extract action types from response.

        Returns a list of unique action types in the order they appear.
        Handles cases where response has actions but no assistant text.
        """
        action_types: List[str] = []
        for action in cls._extract_ai_actions(response_data):
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("actionType") or "").strip()
            if action_type and action_type not in action_types:
                action_types.append(action_type)
        return action_types

    @classmethod
    def has_effective_response(cls, response_data: Dict[str, Any]) -> bool:
        """Check if response contains valid actions even without assistant text.

        A response is considered effective if it contains at least one valid action:
        - sendMessage: must have contentType="text" and non-empty content
        - forward: must have a non-empty scene

        Returns:
            True if the response has at least one valid action, False otherwise.
        """
        actions = cls._extract_ai_actions(response_data)
        for action in actions:
            if not isinstance(action, dict):
                continue
            action_type = str(action.get("actionType") or "").strip()
            payload = action.get("payload")
            if not isinstance(payload, dict):
                continue

            # sendMessage must have non-empty text content
            if action_type == "sendMessage":
                content = payload.get("content")
                if (payload.get("contentType") == "text"
                    and content and str(content).strip()):
                    return True
            # forward must have a valid payload with scene
            elif action_type == "forward":
                scene = payload.get("scene")
                if scene and str(scene).strip():
                    return True

        return False

    @classmethod
    def extract_ai_reply(cls, response_data: Dict[str, Any]) -> Optional[str]:
        actions = cls._extract_ai_actions(response_data)
        for action in actions:
            if action.get("actionType") != "sendMessage":
                continue
            payload = action.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("contentType") == "text":
                return payload.get("content")
        return None

    @classmethod
    def extract_assistant_messages(
        cls,
        response_data: Dict[str, Any],
        response_received_at: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        actions = cls._extract_ai_actions(response_data)
        assistant_created_at = float(response_received_at or time.time())

        messages: List[Dict[str, Any]] = []
        for action in actions:
            if action.get("actionType") != "sendMessage":
                continue
            payload = action.get("payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("contentType") != "text":
                continue
            content = payload.get("content")
            if not content:
                continue
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "created_at": assistant_created_at,
                }
            )
        return messages


chat_api = ChatAPI()
