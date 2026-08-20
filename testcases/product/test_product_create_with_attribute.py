import time
from typing import Optional

from api_object.product_api import ProductAPI
from api_object.product_attribute_api import ProductAttributeAPI
from testcases.product.test_product_list import (
    _authorized_product_client,
    _product_runtime,
)


def test_create_product_then_create_product_attribute() -> None:
    runtime = _product_runtime()
    client = _authorized_product_client(runtime)
    product_client = ProductAPI(client=client)
    attribute_client = ProductAttributeAPI(client=client)
    unique_suffix = str(time.time_ns() // 1_000_000)
    product_id = unique_suffix[-13:]
    product_internal_id: Optional[str] = None

    try:
        product_response = product_client.create_product(
            enabled=True,
            name=f"API测试商品-{unique_suffix}",
            product_id=product_id,
            product_title="水杨酸祛痘次抛精华液接口自动化测试商品",
            recommend=True,
            shop_id=runtime["shop_id"],
        )

        assert product_response["status_code"] == 200
        assert product_response["code"] == 200
        assert product_response["payload"] == {
            "enabled": True,
            "name": f"API测试商品-{unique_suffix}",
            "product_id": product_id,
            "product_title": "水杨酸祛痘次抛精华液接口自动化测试商品",
            "recommend": True,
            "shop_id": str(runtime["shop_id"]),
        }
        product_internal_id = product_response["product_internal_id"]
        assert product_internal_id

        attribute_response = attribute_client.create_product_attribute(
            attribute_name="API测试属性",
            attribute_value=unique_suffix,
            product_internal_id=product_internal_id,
            shop_id=runtime["shop_id"],
        )

        assert attribute_response["status_code"] == 200
        assert attribute_response["code"] == 200
        assert attribute_response["payload"] == {
            "attributeName": "API测试属性",
            "attributeValue": unique_suffix,
            "productId": product_internal_id,
            "shop_id": str(runtime["shop_id"]),
        }
        assert attribute_response["attribute"] == {
            "name": "API测试属性",
            "value": unique_suffix,
        }
        listed_attributes = attribute_client.get_product_attributes(
            product_internal_id=product_internal_id,
            shop_id=runtime["shop_id"],
        )
        assert listed_attributes["status_code"] == 200
        assert listed_attributes["code"] == 200
        assert any(
            attribute.get("attributeName") == "API测试属性"
            and attribute.get("attributeValue") == unique_suffix
            for attribute in listed_attributes["attributes"]
        )
    finally:
        if product_internal_id:
            client.request("DELETE", f"/api/products/{product_internal_id}")
        client.close()
