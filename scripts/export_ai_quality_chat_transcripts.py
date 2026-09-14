"""导出 AI 质检有问题的用户的聊天记录（自包含，不依赖质检用例模块）。

链路：

1. ``POST /api/ai-quality-inspection/list``（``has_issues=true``）拿质检有问题的记录，自动翻页取全量；
2. 从每条记录里取 ``user.id`` / ``shop_id`` / ``start_time`` / ``end_time``；
3. ``GET /api/users/{user_id}/messages`` 取该用户在这段会话里的完整聊天记录；
4. 写成 JSON 快照（带时间戳一份 + ``*_latest.json`` 一份），供
   ``scripts/generate_ai_quality_tag_review_context_cases.py`` 生成用例 YAML。

鉴权：优先用 ``.env`` 里的 ``ACCESS_TOKEN_<ENV>``（console 就是 ``ACCESS_TOKEN_CONSOLE``），
没有配 token 时回退到 ``LOGIN_ACCOUNT_<ENV>`` / ``LOGIN_PASSWORD_<ENV>`` 登录拿 token。

示例：

    .\\.venv\\Scripts\\python.exe scripts\\export_ai_quality_chat_transcripts.py
    .\\.venv\\Scripts\\python.exe scripts\\export_ai_quality_chat_transcripts.py --limit 3 --print-records 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from api_object.auth_api import AuthAPI  # noqa: E402
from api_object.user_messages_api import UserMessagesAPI  # noqa: E402
from common.http_client import create_http_client  # noqa: E402
from config.project_env import reload_project_env  # noqa: E402

LIST_ENDPOINT = "/api/ai-quality-inspection/list"
DEFAULT_BASE_URLS = {
    "dev": "https://dev.zhiyan.chat",
    "console": "https://console.zhiyan.chat",
}

DEFAULT_SHOP_IDS = "888,917,924,925,926,927,928,929,934,941"
DEFAULT_START_TIME = 1789228800  # 2026-09-13 00:00:00
DEFAULT_END_TIME = 1789315199  # 2026-09-13 23:59:59
DEFAULT_OUTPUT_DIR = "data/ai_quality_inspection/records"

ShopIds = Union[str, int, Iterable[Union[str, int]], None]


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


def _format_ts(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value)))
    except (TypeError, ValueError, OSError):
        return str(value)


def normalize_shop_ids(shop_ids: ShopIds) -> str:
    """``585`` / ``"585,588"`` / ``[585, 588]`` 统一成逗号分隔字符串。"""
    if shop_ids is None:
        return ""
    if isinstance(shop_ids, str):
        raw_items = shop_ids.replace(";", ",").split(",")
    elif isinstance(shop_ids, (list, tuple, set)):
        raw_items = [str(item) for item in shop_ids]
    else:
        raw_items = [str(shop_ids)]

    normalized: List[str] = []
    for item in raw_items:
        value = str(item).strip()
        if value and value not in normalized:
            normalized.append(value)
    return ",".join(normalized)


# ---------------------------------------------------------------------------
# 鉴权与运行时
# ---------------------------------------------------------------------------
def resolve_runtime(target_env: str) -> Dict[str, Any]:
    reload_project_env()
    env_key = str(target_env or "dev").strip().upper()
    base_url = _first_nonempty(os.getenv(f"AI_BASE_URL_{env_key}")).rstrip("/")
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(env_key.lower(), DEFAULT_BASE_URLS["dev"])

    access_token = _first_nonempty(
        os.getenv(f"ACCESS_TOKEN_{env_key}"),
        os.getenv("CONTEXT_CONSOLE_ACCESS_TOKEN") if env_key == "CONSOLE" else "",
    )
    return {
        "target_env": env_key.lower(),
        "base_url": base_url,
        "access_token": access_token,
        "login_account": _first_nonempty(
            os.getenv(f"LOGIN_ACCOUNT_{env_key}"), os.getenv("LOGIN_ACCOUNT")
        ),
        "login_password": _first_nonempty(
            os.getenv(f"LOGIN_PASSWORD_{env_key}"), os.getenv("LOGIN_PASSWORD")
        ),
    }


def access_token_for(runtime: Dict[str, Any]) -> str:
    token = str(runtime.get("access_token") or "").strip()
    if token:
        print(f"TOKEN_SOURCE ACCESS_TOKEN_{runtime['target_env'].upper()}")
        return token

    auth_http = create_http_client(base_url=runtime["base_url"])
    try:
        login = AuthAPI(client=auth_http).login(runtime["login_account"], runtime["login_password"])
        print(
            f"LOGIN env={runtime['target_env']} account={runtime['login_account']} "
            f"status={login['status_code']} code={login['data'].get('code')}"
        )
        token = str(login.get("access_token") or "").strip()
    finally:
        auth_http.close()

    if not token:
        raise SystemExit(
            f"缺少 ACCESS_TOKEN_{runtime['target_env'].upper()}，"
            f"且用 LOGIN_ACCOUNT_{runtime['target_env'].upper()} 登录也没拿到 token"
        )
    return token


def build_client(runtime: Dict[str, Any], token: str):
    client = create_http_client(base_url=runtime["base_url"])
    client.set_header("Authorization", _build_auth_header(token))
    return client


# ---------------------------------------------------------------------------
# 质检列表
# ---------------------------------------------------------------------------
def list_quality_records(
    client,
    *,
    shop_ids: ShopIds,
    start_time: int,
    end_time: int,
    page_size: int = 50,
    has_issues: bool = True,
    max_pages: int = 200,
) -> Dict[str, Any]:
    """按页拉全量质检记录，直到取满 total、返回空页或达到 max_pages。"""
    normalized_shop_ids = normalize_shop_ids(shop_ids)
    records: List[Dict[str, Any]] = []
    total: Optional[int] = None
    code: Any = None
    message: Any = None
    page = 1

    while page <= max_pages:
        payload: Dict[str, Any] = {
            "start_time": int(start_time),
            "end_time": int(end_time),
            "page": page,
            "page_size": int(page_size),
            "has_issues": bool(has_issues),
        }
        if normalized_shop_ids:
            payload["shop_ids"] = normalized_shop_ids

        response = client.post(LIST_ENDPOINT, json=payload)
        try:
            response.encoding = "utf-8"
            body = response.json()
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        code = body.get("code")
        message = body.get("message") or body.get("msg")
        data = body.get("data")
        data = data if isinstance(data, dict) else {}

        if code != 200:
            break
        if total is None:
            total = data.get("total")

        page_records = data.get("list")
        page_records = page_records if isinstance(page_records, list) else []
        if not page_records:
            break

        before = len(records)
        records.extend(page_records)
        # 服务端忽略 page 参数时会一直返回同一页，用"本页没有新增"兜底退出。
        if len(records) == before or len(records) >= int(total or 0):
            break
        page += 1

    return {
        "code": code,
        "message": message,
        "total": total,
        "pages": page,
        "records": records,
    }


def extract_issue_names(record: Dict[str, Any]) -> List[str]:
    """汇总一条质检记录 tags 里的问题名称。"""
    names: List[str] = []
    for tag in record.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        for name in tag.get("names") or []:
            value = str(name).strip()
            if value and value not in names:
                names.append(value)
    return names


def extract_session_names(record: Dict[str, Any]) -> List[str]:
    """汇总一条质检记录 tags 里的会话场景名称。"""
    session_names: List[str] = []
    for tag in record.get("tags") or []:
        if not isinstance(tag, dict):
            continue
        for name in tag.get("session_names") or []:
            value = str(name).strip()
            if value and value not in session_names:
                session_names.append(value)
    return session_names


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------
def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")


def _file_tag(shop_ids: str) -> str:
    items = [item for item in normalize_shop_ids(shop_ids).split(",") if item]
    if not items:
        return ""
    if len(items) <= 3:
        return f"_{'-'.join(items)}"
    return f"_{items[0]}_{len(items)}shops"


def _build_entry(record: Dict[str, Any], messages_api: UserMessagesAPI) -> Dict[str, Any]:
    user = record.get("user") or {}
    worker = record.get("worker") or {}
    user_id = user.get("id")
    shop_id = record.get("shop_id")
    start_time = record.get("start_time")
    end_time = record.get("end_time")

    result: Dict[str, Any] = {"status_code": None, "code": None, "message": "", "messages": []}
    if user_id and shop_id and start_time and end_time:
        result = messages_api.get_messages(
            user_id=user_id, shop_id=shop_id, start_time=start_time, end_time=end_time
        )

    return {
        "record_id": record.get("_id"),
        "request_id": record.get("request_id"),
        "shop_id": shop_id,
        "shop_name": record.get("shop_name"),
        "chat_time": record.get("chat_time"),
        "start_time": start_time,
        "end_time": end_time,
        "issue_summary": record.get("summary"),
        "quality_tags": extract_issue_names(record),
        "session_names": extract_session_names(record),
        "user": {"id": user_id, "dnick": user.get("dnick")},
        "worker": {"id": worker.get("id"), "account": worker.get("account")},
        "transcript_status": {
            "status_code": result.get("status_code"),
            "code": result.get("code"),
            "message": result.get("message"),
        },
        "transcript": UserMessagesAPI.normalize_messages(result.get("messages") or []),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 AI 质检有问题用户的聊天记录。")
    parser.add_argument("--env", default="console", help="目标环境：dev / console")
    parser.add_argument("--shop-ids", default=DEFAULT_SHOP_IDS)
    parser.add_argument("--start-time", type=int, default=DEFAULT_START_TIME)
    parser.add_argument("--end-time", type=int, default=DEFAULT_END_TIME)
    parser.add_argument("--page-size", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用，0 表示全量）")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--print-records", type=int, default=0, help="控制台打印几条样本会话")
    args = parser.parse_args()

    runtime = resolve_runtime(args.env)
    client = build_client(runtime, access_token_for(runtime))
    messages_api = UserMessagesAPI(client=client)

    listed = list_quality_records(
        client,
        shop_ids=args.shop_ids,
        start_time=args.start_time,
        end_time=args.end_time,
        page_size=args.page_size,
    )
    if listed["code"] != 200:
        print(f"QUALITY_LIST_FAILED code={listed['code']} message={listed['message']}")
        return 1

    records: List[Dict[str, Any]] = listed["records"]
    if args.limit > 0:
        records = records[: args.limit]
    print(f"QUALITY_LIST env={runtime['target_env']} total={listed['total']} fetched={len(records)}")

    entries: List[Dict[str, Any]] = []
    ok_count = 0
    for index, record in enumerate(records, start=1):
        entry = _build_entry(record, messages_api)
        if entry["transcript"]:
            ok_count += 1
        entries.append(entry)
        if index % 10 == 0 or index == len(records):
            print(f"TRANSCRIPT_PROGRESS {index}/{len(records)} ok={ok_count}")

    now = time.localtime()
    directory = Path(args.output_dir)
    if not directory.is_absolute():
        directory = ROOT / directory

    payload = {
        "env": runtime["target_env"],
        "base_url": runtime["base_url"],
        "shop_ids": normalize_shop_ids(args.shop_ids),
        "has_issues": True,
        "start_time": args.start_time,
        "end_time": args.end_time,
        "start_time_text": _format_ts(args.start_time),
        "end_time_text": _format_ts(args.end_time),
        "exported_at": time.strftime("%Y-%m-%d %H:%M:%S", now),
        "total": listed["total"],
        "record_count": len(entries),
        "transcript_ok_count": ok_count,
        "records": entries,
    }

    stem = f"ai_quality_chat_transcripts_{runtime['target_env']}{_file_tag(args.shop_ids)}"
    stamp = time.strftime("%Y%m%d_%H%M%S", now)
    snapshot = directory / f"{stem}_{stamp}.json"
    _write_json(snapshot, payload)
    _write_json(directory / f"{stem}_latest.json", payload)

    print(f"EXPORT_OK records={len(entries)} ok={ok_count} file={snapshot}")

    for entry in entries[: max(args.print_records, 0)]:
        print("---")
        print(
            f"record={entry['record_id']} shop={entry['shop_id']} user={entry['user']['id']} "
            f"tags={entry['quality_tags']} messages={len(entry['transcript'])}"
        )
        for message in entry["transcript"]:
            print(f"  {message['role']}: {message['content']}")

    return 0 if ok_count else 1


if __name__ == "__main__":
    raise SystemExit(main())
