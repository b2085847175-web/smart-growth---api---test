from typing import Any, Dict

from common.http_client import http_client


class ProductAttributeAPI:
    def __init__(self, client=None) -> None:
        self.http_client = client or http_client

    def create_product_attribute(
        self,
        *,
        attribute_name: str,
        attribute_value: str,
        product_internal_id: str,
        shop_id: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "attributeName": attribute_name,
            "attributeValue": attribute_value,
            "productId": str(product_internal_id),
            "shop_id": str(shop_id),
        }

        response = self.http_client.request(
            "POST", "/api/product-attributes", json=payload
        )
        data = response.json()
        result = data.get("data", {}) if isinstance(data, dict) else {}
        attribute = result if isinstance(result, dict) else {}

        return {
            "status_code": response.status_code,
            "payload": payload,
            "data": data,
            "code": data.get("code") if isinstance(data, dict) else None,
            "result": result,
            "attribute": attribute,
        }

    def update_product_attribute(
        self,
        *,
        attribute_id: str,
        attribute_name: str,
        attribute_value: str,
        product_internal_id: str,
        shop_id: str,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "attributeName": attribute_name,
            "attributeValue": attribute_value,
            "productId": str(product_internal_id),
            "shop_id": str(shop_id),
        }

        response = self.http_client.request(
            "PUT", f"/api/product-attributes/{attribute_id}", json=payload
        )
        data = response.json()

        return {
            "status_code": response.status_code,
            "payload": payload,
            "data": data,
            "code": data.get("code") if isinstance(data, dict) else None,
        }

    def get_product_attributes(
        self,
        *,
        product_internal_id: str,
        shop_id: str,
        page: int = 1,
        page_size: int = 100,
        search: str = "",
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "productId": str(product_internal_id),
            "page": page,
            "pageSize": page_size,
            "search": search,
            "shop_id": str(shop_id),
        }

        response = self.http_client.request(
            "GET", "/api/product-attributes", params=params
        )
        data = response.json()
        result = data.get("data", {}) if isinstance(data, dict) else {}
        attributes = result.get("list", []) if isinstance(result, dict) else []
        if not isinstance(attributes, list):
            attributes = []

        return {
            "status_code": response.status_code,
            "params": params,
            "data": data,
            "code": data.get("code") if isinstance(data, dict) else None,
            "result": result,
            "attributes": attributes,
        }


product_attribute_api = ProductAttributeAPI()
