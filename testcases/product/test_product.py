"""Product API and product-migration tests (merged from three files)."""

import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from api_object.auth_api import AuthAPI
from api_object.product_api import ProductAPI
from api_object.product_attribute_api import ProductAttributeAPI
from common.http_client import create_http_client
from config.project_env import reload_project_env
from config.settings import settings
from scripts.build_prefixed_conflict_snapshot import _build_prefixed_products
from scripts.export_shop_products import ExportProgress
from scripts.import_shop_products_to_dev import (
    ImportState,
    _attribute_pair,
    _merge_attributes_by_name,
)
from scripts.product_migration_common import (
    MigrationError,
    RestrictedHttpClient,
    SOURCE_BASE_URL,
    validate_snapshot,
    write_snapshot,
)


# ---------------------------------------------------------------------------
# Shared product helpers
# ---------------------------------------------------------------------------
def _first_nonempty(*values: Any) -> str:
    for value in values:
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return ""


def _build_auth_header(access_token: str) -> str:
    normalized = str(access_token or "").strip()
    if not normalized:
        return normalized
    if normalized.lower().startswith("bearer "):
        return normalized
    return f"Bearer {normalized}"


def _product_runtime() -> Dict[str, str]:
    reload_project_env()
    target_env = os.getenv("PRODUCT_TARGET_ENV", "dev").strip().lower() or "dev"
    env_key = target_env.upper()
    default_base_url = (
        "https://console.zhiyan.chat"
        if target_env == "console"
        else "https://dev.zhiyan.chat"
    )
    return {
        "target_env": target_env,
        "base_url": _first_nonempty(
            os.getenv(f"AI_BASE_URL_{env_key}"), default_base_url
        ).rstrip("/"),
        "login_account": _first_nonempty(
            os.getenv(f"LOGIN_ACCOUNT_{env_key}"),
            os.getenv(f"CONTEXT_{env_key}_LOGIN_ACCOUNT"),
            os.getenv("LOGIN_ACCOUNT"),
        ),
        "login_password": _first_nonempty(
            os.getenv(f"LOGIN_PASSWORD_{env_key}"),
            os.getenv(f"CONTEXT_{env_key}_LOGIN_PASSWORD"),
            os.getenv("LOGIN_PASSWORD"),
        ),
        "shop_id": _first_nonempty(os.getenv("PRODUCT_SHOP_ID"), "585"),
    }


def _authorized_product_client(runtime: Dict[str, str]):
    client = create_http_client(
        base_url=runtime["base_url"],
        default_headers=settings.get_headers(),
    )
    auth_client = AuthAPI(client=client)
    login_response = auth_client.login(
        runtime["login_account"], runtime["login_password"]
    )
    login_data = login_response["data"]
    assert login_response["status_code"] == 200
    assert login_data.get("code") == 200
    access_token = login_response.get("access_token")
    assert access_token, "product list login succeeded but accessToken was empty"
    client.set_header("Authorization", _build_auth_header(access_token))
    return client


def test_get_shop_product_list_returns_products() -> None:
    runtime = _product_runtime()
    client = _authorized_product_client(runtime)
    try:
        product_client = ProductAPI(client=client)
        response = product_client.get_products(shop_id=runtime["shop_id"])
    finally:
        client.close()

    assert response["status_code"] == 200
    assert response["code"] == 200
    assert isinstance(response["products"], list)
    assert response[
        "products"
    ], "expected shop product list to contain at least one product"

    first_product = response["products"][0]
    assert str(first_product.get("shop_id")) == str(runtime["shop_id"])
    assert first_product.get("product_id")
    assert first_product.get("product_title") or first_product.get("name")


# ---------------------------------------------------------------------------
# Product create + attribute
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Product migration unit tests
# ---------------------------------------------------------------------------
class FakeResponse:
    status_code = 200

    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def json(self) -> Dict[str, Any]:
        return self._data


class FakeClient:
    def __init__(self, response_data: Dict[str, Any] | None = None) -> None:
        self.base_url = SOURCE_BASE_URL
        self.response_data = response_data or {"code": 200}
        self.requests = []

    def request(self, method: str, endpoint: str, **kwargs) -> FakeResponse:
        self.requests.append((method, endpoint, kwargs))
        return FakeResponse(self.response_data)

    def close(self) -> None:
        return None


def _sample_product(product_id: str) -> Dict[str, Any]:
    return {
        "product_id": product_id,
        "product_title": f"商品-{product_id}",
        "name": f"商品-{product_id}",
        "enabled": True,
        "recommend": False,
        "attributes": [
            {
                "attribute_name": "规格",
                "attribute_value": "30支",
            }
        ],
    }


def test_snapshot_round_trip_and_validate(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "snapshot"
    products = [_sample_product("10001"), _sample_product("10002")]

    write_snapshot(
        products,
        snapshot_dir,
        source_shop_id="576",
        source_total_before=2,
        source_total_after=2,
        page_size=500,
        attribute_page_size=100,
        workers=4,
        limit=None,
        part_size=1,
    )
    validation = validate_snapshot(snapshot_dir)

    assert validation.ok
    assert validation.manifest["complete_export"] is True
    assert [product["product_id"] for product in validation.products] == [
        "10001",
        "10002",
    ]


def test_partial_snapshot_requires_explicit_allow(tmp_path: Path) -> None:
    snapshot_dir = tmp_path / "partial"
    write_snapshot(
        [_sample_product("10001")],
        snapshot_dir,
        source_shop_id="576",
        source_total_before=10,
        source_total_after=10,
        page_size=10,
        attribute_page_size=100,
        workers=2,
        limit=1,
        part_size=10,
    )

    blocked = validate_snapshot(snapshot_dir)
    allowed = validate_snapshot(snapshot_dir, allow_partial=True)

    assert not blocked.ok
    assert any("partial" in error for error in blocked.errors)
    assert allowed.ok
    assert any("partial" in warning for warning in allowed.warnings)


def test_source_client_blocks_writes_and_cross_environment_requests() -> None:
    fake_client = FakeClient()
    client = RestrictedHttpClient(
        fake_client,
        expected_base_url=SOURCE_BASE_URL,
        allowed_methods={"GET"},
    )

    client.request("GET", "/api/products/")

    with pytest.raises(MigrationError, match="method POST is blocked"):
        client.request("POST", "/api/products")
    with pytest.raises(MigrationError, match="cross-environment request blocked"):
        client.request("GET", "https://dev.zhiyan.chat/api/products/")
    assert len(fake_client.requests) == 1


def test_get_product_attributes_parses_paginated_response() -> None:
    fake_client = FakeClient(
        {
            "code": 200,
            "data": {
                "list": [{"name": "规格", "value": "30支"}],
                "total": 1,
                "page": 1,
                "pageSize": 500,
            },
        }
    )

    response = ProductAttributeAPI(client=fake_client).get_product_attributes(
        product_internal_id="source-internal-id",
        shop_id="576",
    )

    assert response["status_code"] == 200
    assert response["code"] == 200
    assert response["attributes"] == [{"name": "规格", "value": "30支"}]
    assert fake_client.requests[0][2]["params"]["productId"] == "source-internal-id"


def test_update_product_attribute_uses_put_payload() -> None:
    fake_client = FakeClient({"code": 200, "message": "success"})

    response = ProductAttributeAPI(client=fake_client).update_product_attribute(
        attribute_id="attribute-id",
        attribute_name="颜色分类",
        attribute_value="红色\n蓝色",
        product_internal_id="product-internal-id",
        shop_id="585",
    )

    assert response["status_code"] == 200
    assert response["code"] == 200
    assert fake_client.requests == [
        (
            "PUT",
            "/api/product-attributes/attribute-id",
            {
                "json": {
                    "attributeName": "颜色分类",
                    "attributeValue": "红色\n蓝色",
                    "productId": "product-internal-id",
                    "shop_id": "585",
                }
            },
        )
    ]


def test_attribute_pair_accepts_yaml_and_api_field_names() -> None:
    assert _attribute_pair({"attribute_name": "规格", "attribute_value": "30支"}) == (
        "规格",
        "30支",
    )


def test_merge_attributes_by_name_preserves_order_and_values() -> None:
    attributes = [
        {"attribute_name": "品牌", "attribute_value": "测试品牌"},
        {"attribute_name": "颜色分类", "attribute_value": "红色"},
        {"attribute_name": "颜色分类", "attribute_value": "蓝色"},
        {"attribute_name": "颜色分类", "attribute_value": "红色"},
    ]

    assert _merge_attributes_by_name(attributes) == [
        ("品牌", "测试品牌"),
        ("颜色分类", "红色\n蓝色"),
    ]


def test_import_state_recognizes_legacy_api_existing_failure(tmp_path: Path) -> None:
    state_path = tmp_path / "import-progress.jsonl"
    state_path.write_text(
        '{"event":"product_failed","product_id":"10001",'
        '"error":"create target product 10001 failed: status=200 code=400 '
        'message=商品ID已存在"}\n',
        encoding="utf-8",
    )

    state = ImportState(state_path, "unused-for-this-event")

    assert state.api_existing == {"10001"}


def test_import_state_counts_cumulative_attribute_writes(tmp_path: Path) -> None:
    state_path = tmp_path / "import-progress.jsonl"
    state_path.write_text(
        '\n'.join(
            [
                '{"event":"attribute_created","product_id":"10001"}',
                '{"event":"attribute_created_recovered","product_id":"10001"}',
                '{"event":"attribute_updated","product_id":"10001"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    state = ImportState(state_path, "unused-for-these-events")

    assert state.attributes_created == 2
    assert state.attributes_updated == 1


def test_build_prefixed_products_changes_only_conflicting_product_ids() -> None:
    products = [_sample_product("10001"), _sample_product("10002")]

    transformed, mapping = _build_prefixed_products(products, {"10002"}, "2026")

    assert [product["product_id"] for product in transformed] == ["202610002"]
    assert transformed[0]["name"] == products[1]["name"]
    assert transformed[0]["attributes"] == products[1]["attributes"]
    assert transformed[0]["attributes"] is not products[1]["attributes"]
    assert mapping == [
        {
            "original_product_id": "10002",
            "transformed_product_id": "202610002",
        }
    ]
    assert _attribute_pair({"attributeName": "规格", "attributeValue": "30支"}) == (
        "规格",
        "30支",
    )


def test_export_progress_reuses_only_unchanged_products(tmp_path: Path) -> None:
    progress_path = tmp_path / "export-progress.jsonl"
    progress = ExportProgress(progress_path)
    normalized_product = _sample_product("10001")

    progress.append(
        {"product_id": "10001", "updated_at": 100},
        normalized_product,
    )
    reloaded = ExportProgress(progress_path)

    assert (
        reloaded.get({"product_id": "10001", "updated_at": 100}) == normalized_product
    )
    assert reloaded.get({"product_id": "10001", "updated_at": 101}) is None

