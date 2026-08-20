from typing import Any, Dict, List

from common.http_client import http_client


class SceneAPI:
    def __init__(self, client=None) -> None:
        self.http_client = client or http_client

    @staticmethod
    def _extract_categories(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        payload = data.get("data") if isinstance(data, dict) else {}
        if isinstance(payload, dict) and isinstance(payload.get("categories"), list):
            return payload["categories"]
        return []

    @staticmethod
    def _extract_scene_items(data: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []

        candidates = []
        for key in ("data", "result", "records", "list", "items", "rows", "scenes"):
            value = data.get(key)
            if isinstance(value, list):
                candidates = value
                break
            if isinstance(value, dict):
                for nested_key in ("records", "list", "items", "rows", "scenes", "data"):
                    nested_value = value.get(nested_key)
                    if isinstance(nested_value, list):
                        candidates = nested_value
                        return candidates
        return candidates

    @staticmethod
    def _normalize_scene_item(
        item: Dict[str, Any],
        categories_by_id: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        kb_scene = item.get("kb_scene") if isinstance(item, dict) else {}
        if not isinstance(kb_scene, dict):
            kb_scene = {}
        kb_industry = item.get("kb_industry") if isinstance(item, dict) else []
        if not isinstance(kb_industry, list):
            kb_industry = [kb_industry]

        category_id = str(kb_scene.get("category_id") or item.get("category_id") or "").strip()
        category = categories_by_id.get(category_id, {})
        scene_name = str(kb_scene.get("name") or item.get("name") or "").strip()
        scene_description = str(kb_scene.get("description") or item.get("description") or "").strip()
        examples = kb_scene.get("example") or item.get("example") or []
        if not isinstance(examples, list):
            examples = [examples]
        question_examples = [str(example).strip() for example in examples if str(example).strip()]
        industry_names = []
        for industry in kb_industry:
            if not isinstance(industry, dict):
                continue
            industry_name = str(industry.get("name") or "").strip()
            if industry_name and industry_name not in industry_names:
                industry_names.append(industry_name)

        return {
            "category_id": category_id,
            "category_key": str(category.get("key") or "").strip(),
            "category_name": str(category.get("name") or "").strip(),
            "scene_name": scene_name,
            "scene_description": scene_description,
            "question_examples": question_examples,
            "industry_names": industry_names,
        }

    def get_scenes(self, shop_id: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "shop_id": shop_id,
        }

        response = self.http_client.request("GET", "/api/kb/scenes", params=params)
        data = response.json()
        if not isinstance(data, dict):
            data = {"data": data}

        scene_items = self._extract_scene_items(data)
        categories = self._extract_categories(data)
        categories_by_id = {
            str(category.get("_id") or category.get("id") or "").strip(): category
            for category in categories
            if isinstance(category, dict)
        }
        scene_summaries: List[Dict[str, Any]] = []
        for item in scene_items:
            if not isinstance(item, dict):
                continue
            normalized = self._normalize_scene_item(item, categories_by_id)
            if normalized["scene_name"] or normalized["question_examples"]:
                scene_summaries.append(normalized)

        return {
            "status_code": response.status_code,
            "params": params,
            "data": data,
            "code": data.get("code"),
            "msg": data.get("msg") or data.get("message"),
            "categories": categories,
            "scenes": scene_summaries,
        }


scene_api = SceneAPI()
