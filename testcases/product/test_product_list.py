import os
from typing import Any, Dict

from api_object.auth_api import AuthAPI
from api_object.product_api import ProductAPI
from common.http_client import create_http_client
from config.project_env import reload_project_env
from config.settings import settings


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
