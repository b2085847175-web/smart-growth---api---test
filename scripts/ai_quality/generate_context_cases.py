"""把真实聊天记录整理成 ``/chat/answer`` 的上下文用例 YAML。

数据来源：

- ``outputs/ai_quality_chat/ai_quality_chat_transcripts_*_latest.json``
  （``scripts/ai_quality/export_chat_records.py`` 导出的"质检有问题记录 + 聊天记录"）
- 人工复核结论：默认从既有用例 YAML 按 record_id 继承 ``tag_review``；
  也可以 ``--tag-review-json <复核快照.json>`` 显式指定复核来源（可选）

输出：``data/answer/daily/ai_quality_tag_review_context_cases.yaml``（全量重写）。

口径：

- 一条质检记录 = 一条 case，``context_messages`` 用真实会话原文，最后一条 user 消息
  就是 ``/chat/answer`` 的触发语；真实会话里位于最后一条 user 消息之后的客服回复会裁掉。
- 平台内部的 ``\\x07\\x08`` 等控制字符已清洗；图片保留 ``media_type: image`` + ``media_url``，
  其他非文本消息降级为 text + 占位文案（``[图片]`` / ``[订单消息]`` …），保证 content 非空。
- ``created_at`` 默认平移到运行当天（保持时刻和相对间隔，日期换成今天），便于把会话
  当成"今天”发生的对话；需要真实时间戳时用 ``--timestamp-mode keep``。

示例：

    .\\.venv\\Scripts\\python.exe scripts\\ai_quality\\generate_context_cases.py
    .\\.venv\\Scripts\\python.exe scripts\\ai_quality\\generate_context_cases.py `
        --transcripts <快照.json> --output <目标.yaml>
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TRANSCRIPTS_DIR = ROOT / "outputs" / "ai_quality_chat"
DEFAULT_OUTPUT = ROOT / "data" / "answer" / "daily" / "ai_quality_tag_review_context_cases.yaml"

# 框架的 context_messages 只接受 text / image。
SUPPORTED_MEDIA_TYPES = {"text", "image"}
MEDIA_PLACEHOLDERS = {
    "image": "[图片]",
    "video": "[视频]",
    "audio": "[语音]",
    "file": "[文件]",
    "goods": "[商品链接]",
    "order": "[订单消息]",
}


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _load_yaml_cases(path: Path) -> List[Dict[str, Any]]:
    """读取既有用例 YAML 里的 cases，用于继承人工复核结论。"""
    import yaml

    with path.open("r", encoding="utf-8") as file:
        document = yaml.safe_load(file) or {}
    cases = document.get("cases")
    return cases if isinstance(cases, list) else []


def _latest_snapshot(pattern: str, directory: Path = TRANSCRIPTS_DIR) -> Optional[Path]:
    if not directory.exists():
        return None
    candidates = sorted(directory.glob(pattern), key=lambda item: item.stat().st_mtime)
    return candidates[-1] if candidates else None


def _display_path(path: Path) -> str:
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return str(path)


def _yaml_str(value: Any) -> str:
    """用 JSON 字符串作为 YAML 双引号标量，避免中文和特殊符号被转义。"""
    return json.dumps(str(value), ensure_ascii=False)


def _yaml_list(values: Any) -> str:
    items = [str(item) for item in (values or [])]
    return "[" + ", ".join(_yaml_str(item) for item in items) + "]"


def _format_ts(value: Any) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(value)))
    except (TypeError, ValueError, OSError):
        return str(value)


def _record_suffix(record_id: str) -> str:
    cleaned = str(record_id or "").replace("-", "")
    return cleaned[-6:] if cleaned else "unknown"


# ---------------------------------------------------------------------------
# 会话整理
# ---------------------------------------------------------------------------
def _normalize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """对齐框架 context_messages 的要求：media 取值、content 非空、时间升序。"""
    normalized: List[Dict[str, Any]] = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        if role not in {"user", "assistant"}:
            continue

        raw_media_type = str(item.get("media_type", "text")).strip().lower() or "text"
        content = str(item.get("content", "") or "").strip()
        media_url = str(item.get("media_url", "") or "").strip()

        media_type = raw_media_type if raw_media_type in SUPPORTED_MEDIA_TYPES else "text"
        if media_type == "image" and not media_url:
            media_type = "text"
        if not content:
            content = MEDIA_PLACEHOLDERS.get(raw_media_type, "[非文本消息]")
        if media_type != "image":
            media_url = ""

        message: Dict[str, Any] = {
            "role": role,
            "content": content,
            "media_type": media_type,
            "media_url": media_url,
        }
        created_at = item.get("created_at")
        if created_at is not None:
            try:
                message["created_at"] = int(float(created_at))
            except (TypeError, ValueError):
                pass
        normalized.append(message)

    normalized.sort(key=lambda message: message.get("created_at") or 0)
    return normalized


def _trim_trailing_assistant(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """裁掉最后一条 user 消息之后的客服回复，保证触发语是 user 消息。"""
    last_user_index: Optional[int] = None
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            last_user_index = index
    if last_user_index is None:
        return []
    return messages[: last_user_index + 1]


def _midnight_of(timestamp: Any) -> Optional[int]:
    try:
        local = time.localtime(int(float(timestamp)))
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


def _shift_to_today(
    messages: List[Dict[str, Any]], reference_ts: Optional[float]
) -> List[Dict[str, Any]]:
    """把消息时间戳整体挪到 reference_ts 当天：保持时刻与相对间隔，只换日期。"""
    if reference_ts is None:
        return messages
    reference_midnight = _midnight_of(reference_ts)
    if reference_midnight is None:
        return messages

    shifted: List[Dict[str, Any]] = []
    for message in messages:
        created_at = message.get("created_at")
        if created_at is None:
            shifted.append(message)
            continue
        message_midnight = _midnight_of(created_at)
        if message_midnight is None:
            shifted.append(message)
            continue
        day_delta = round((reference_midnight - message_midnight) / 86400)
        shifted.append({**message, "created_at": int(float(created_at)) + day_delta * 86400})
    return shifted


# ---------------------------------------------------------------------------
# 用例组装
# ---------------------------------------------------------------------------
def _worker_of(record: Dict[str, Any]) -> Dict[str, Any]:
    worker = record.get("worker")
    if isinstance(worker, dict):
        return worker
    return {"id": None, "account": worker}


def _build_case(
    record: Dict[str, Any],
    index: int,
    tag_review: Optional[Dict[str, Any]],
    *,
    reference_ts: Optional[float] = None,
) -> Dict[str, Any]:
    user = record.get("user") or {}
    worker = _worker_of(record)
    chat_time = record.get("chat_time")
    transcript = _trim_trailing_assistant(_normalize_messages(record.get("transcript") or []))
    transcript = _shift_to_today(transcript, reference_ts)
    return {
        "seq": index,
        "name": f"qc_{index:03d}_{record.get('shop_id')}_{_record_suffix(record.get('record_id'))}",
        "record_id": record.get("record_id"),
        "request_id": record.get("request_id"),
        "shop_id": record.get("shop_id"),
        "shop_name": record.get("shop_name"),
        "worker": worker.get("account"),
        "worker_id": worker.get("id"),
        "user_id": user.get("id"),
        "chat_time": chat_time,
        "chat_time_text": _format_ts(chat_time),
        "issue_summary": record.get("issue_summary") or record.get("summary") or "",
        "quality_tags": record.get("quality_tags") or [],
        "session_names": record.get("session_names") or [],
        "tag_review": tag_review,
        "context_messages": transcript,
    }


def _emit_case(case: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    tag_text = "/".join(case["quality_tags"]) or "无标签"
    review_text = f"｜复核：{case['tag_review']['verdict']}" if case["tag_review"] else ""
    lines.append(
        f"  # [{case['seq']:03d}] {case['shop_id']}｜{case['shop_name']}｜{case['worker']}"
        f"｜{case['chat_time_text']}｜质检标签：{tag_text}{review_text}"
    )
    lines.append(f"  - name: {_yaml_str(case['name'])}")
    lines.append(f"    record_id: {_yaml_str(case['record_id'])}")
    lines.append(f"    request_id: {_yaml_str(case['request_id'])}")
    lines.append(f"    shop_id: {_yaml_str(case['shop_id'])}")
    lines.append(f"    shop_name: {_yaml_str(case['shop_name'])}")
    lines.append(f"    worker: {_yaml_str(case['worker'])}")
    lines.append(f"    worker_id: {case['worker_id'] if case['worker_id'] is not None else 'null'}")
    lines.append(f"    user_id: {case['user_id'] if case['user_id'] is not None else 'null'}")
    lines.append(f"    chat_time: {case['chat_time']}")
    lines.append(f"    chat_time_text: {_yaml_str(case['chat_time_text'])}")
    lines.append(f"    issue_summary: {_yaml_str(case['issue_summary'])}")
    lines.append(f"    quality_tags: {_yaml_list(case['quality_tags'])}")
    lines.append(f"    session_names: {_yaml_list(case['session_names'])}")

    review = case["tag_review"]
    if review:
        lines.append("    tag_review:")
        lines.append(f"      verdict: {_yaml_str(review.get('verdict'))}")
        lines.append(f"      wrong_tags: {_yaml_list(review.get('wrong_tags'))}")
        lines.append(f"      reason: {_yaml_str(review.get('reason'))}")
        lines.append(f"      basis: {_yaml_str(review.get('basis'))}")

    lines.append("    context_messages:")
    for message in case["context_messages"]:
        lines.append(f"      - role: {_yaml_str(message.get('role'))}")
        lines.append(f"        content: {_yaml_str(message.get('content'))}")
        lines.append(f"        media_type: {_yaml_str(message.get('media_type') or 'text')}")
        lines.append(f"        media_url: {_yaml_str(message.get('media_url') or '')}")
        if message.get("created_at") is not None:
            lines.append(f"        created_at: {int(message['created_at'])}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="用真实聊天记录生成上下文用例 YAML。")
    parser.add_argument(
        "--transcripts",
        default="",
        help="聊天记录快照；不传时自动取 outputs/ai_quality_chat 下最新的 *_latest.json",
    )
    parser.add_argument(
        "--tag-review-json",
        default="",
        help="人工复核快照（可选）；不传时从 --reviews-from 指定的用例 YAML 继承 tag_review",
    )
    parser.add_argument(
        "--reviews-from",
        default="",
        help="既有用例 YAML，用于继承人工复核结论；默认取 --output 指向的文件",
    )
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--timestamp-mode",
        choices=["today", "keep"],
        default="today",
        help="context_messages.created_at：today=平移到运行当天（默认），keep=保留原始时间戳",
    )
    args = parser.parse_args()

    transcript_path = Path(args.transcripts) if args.transcripts else _latest_snapshot(
        "ai_quality_chat_transcripts_*_latest.json"
    )
    if transcript_path is None or not transcript_path.exists():
        print(f"TRANSCRIPTS_MISSING {transcript_path}")
        print("先跑：.\\\\.venv\\\\Scripts\\\\python.exe scripts\\\\ai_quality\\\\export_chat_records.py")
        return 1

    snapshot = _load_json(transcript_path)
    records: List[Dict[str, Any]] = snapshot.get("records") or []
    if not records:
        print(f"TRANSCRIPTS_EMPTY {transcript_path}")
        return 1

    reviews: Dict[str, Dict[str, Any]] = {}
    review_source = ""
    if args.tag_review_json:
        review_path = Path(args.tag_review_json)
        if review_path.exists():
            for item in _load_json(review_path).get("records") or []:
                review = item.get("tag_review")
                if review and item.get("_id"):
                    reviews[str(item["_id"])] = review
            review_source = _display_path(review_path)
    else:
        # 从既有用例 YAML 继承人工复核结论，避免复核结果随中间快照一起丢失。
        yaml_path = Path(args.reviews_from) if args.reviews_from else Path(args.output)
        if yaml_path.exists():
            for case in _load_yaml_cases(yaml_path):
                review = case.get("tag_review")
                if review and case.get("record_id"):
                    reviews[str(case["record_id"])] = review
            review_source = _display_path(yaml_path)

    ordered = sorted(
        records, key=lambda item: (str(item.get("shop_id")), int(item.get("chat_time") or 0))
    )
    reference_ts = time.time() if args.timestamp_mode == "today" else None
    cases = [
        _build_case(
            record,
            index,
            reviews.get(str(record.get("record_id"))),
            reference_ts=reference_ts,
        )
        for index, record in enumerate(ordered, start=1)
    ]

    message_total = sum(len(case["context_messages"]) for case in cases)
    reviewed_total = sum(1 for case in cases if case["tag_review"])
    empty_cases = [case["name"] for case in cases if not case["context_messages"]]
    shop_counter = ", ".join(
        f"{shop_id}×{count}" for shop_id, count in sorted(_shop_counts(ordered).items())
    )

    header = [
        "# 由 scripts/ai_quality/generate_context_cases.py 生成，请勿手工编辑。",
        "#       数据来源：scripts/ai_quality/export_chat_records.py（质检有问题记录 + 真实聊天记录）",
        f"# 来源：{_display_path(transcript_path)}",
        f"#       人工复核结论：{review_source or '未使用'}",
        "# 说明：context_messages 为真实会话原文（通过 /api/users/{id}/messages 拉取），",
        "#       最后一条 user 消息作为 /chat/answer 的触发语；位于其后的客服回复已裁掉；",
        "#       平台控制字符已清洗，图片保留 media_type/media_url，非文本消息降级为 text + 占位文案，",
        (
            "#       created_at 已平移到今天（保持时刻与相对间隔）。"
            if args.timestamp_mode == "today"
            else "#       created_at 为原始真实时间戳。"
        ),
        f"# 规模：{len(cases)} 条质检记录，会话消息合计 {message_total} 条，"
        f"其中 {reviewed_total} 条带人工复核结论。",
        f"# 店铺分布：{shop_counter}",
        "# 断言策略：quality/assertions 均为 false，只执行接口并观察回复。",
        "suite:",
        "  name: ai_quality_tag_review_context",
        "  mode: sequential",
        "  quality: true",
        "  assertions: false",
        "  match_score: false",
        "  final_reply_equals_chat: false",
        "  turn_interval_seconds: 1",
        "",
        "target_env: dev",
        "",
        "cases:",
    ]

    body: List[str] = []
    for case in cases:
        body.extend(_emit_case(case))
        body.append("")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(header + body).rstrip("\n") + "\n")

    print(
        f"GENERATE_OK cases={len(cases)} messages={message_total} reviewed={reviewed_total} "
        f"file={output_path}"
    )
    if empty_cases:
        print(f"WARN_EMPTY_CASES {len(empty_cases)} {empty_cases[:5]}")
    return 0


def _shop_counts(records: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        key = str(record.get("shop_id"))
        counts[key] = counts.get(key, 0) + 1
    return counts


if __name__ == "__main__":
    raise SystemExit(main())
