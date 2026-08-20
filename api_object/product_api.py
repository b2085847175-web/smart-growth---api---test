from typing import Any, Dict

from common.http_client import http_client


class ProductAPI:
    def __init__(self, client=None) -> None:
        self.http_client = client or http_client

    def get_products(
        self,
        shop_id: str,
        page: int = 1,
        page_size: int = 10,
        search: str = "",
        stock_code: str = "",
        sort_by: str = "updated_at",
        sort_order: str = "asc",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "page": page,
            "pageSize": page_size,
            "search": search,
            "stock_code": stock_code,
            "sortBy": sort_by,
            "sortOrder": sort_order,
            "shop_id": shop_id,
        }

        response = self.http_client.request("GET", "/api/products/", params=params)
        data = response.json()
        result = data.get("result", {}) if isinstance(data, dict) else {}
        products = result.get("data", []) if isinstance(result, dict) else []
        if not isinstance(products, list):
            products = []

        return {
            "status_code": response.status_code,
            "params": params,
            "data": data,
            "code": data.get("code") if isinstance(data, dict) else None,
            "result": result,
            "products": products,
        }

    def create_product(
        self,
        *,
        enabled: bool,
        name: str,
        product_id: str,
        product_title: str,
        recommend: bool,
        shop_id: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "enabled": enabled,
            "name": name,
            "product_id": str(product_id),
            "product_title": product_title,
            "recommend": recommend,
            "shop_id": str(shop_id),
        }

        response = self.http_client.request("POST", "/api/products", json=payload)
        data = response.json()
        result = data.get("data") if isinstance(data, dict) else None
        if isinstance(result, dict):
            product_internal_id = result.get("_id") or result.get("id")
        else:
            product_internal_id = result

        return {
            "status_code": response.status_code,
            "payload": payload,
            "data": data,
            "code": data.get("code") if isinstance(data, dict) else None,
            "result": result,
            "product_internal_id": str(product_internal_id)
            if product_internal_id
            else "",
        }


product_api = ProductAPI()
