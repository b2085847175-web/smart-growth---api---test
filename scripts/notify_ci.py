"""把 Jenkins 构建结果推到企业微信 / 钉钉群机器人。

输入是 pytest 生成的 JUnit XML，输出是一条 markdown 消息：

    answer 定时回归 失败
    > 入口 all · 用例 2915 · 失败 12 · 耗时 1h23m
    • ai_quality_tag_review_context_cases::qc_001_917_4291d1
      turn=1 question=你是机器人吗 error=response status mismatch ...
    ...

用法：

    .\\.venv\\Scripts\\python.exe scripts\\notify_ci.py `
        --junit reports\\jenkins\\junit.xml `
        --entry all --status FAILURE --build-number 128 `
        --build-url http://jenkins/job/answer-daily/128/ `
        --wecom-webhook $env:WECOM_WEBHOOK `
        --dingtalk-webhook $env:DINGTALK_WEBHOOK `
        --dingtalk-secret $env:DINGTALK_SECRET

设计约定：

- webhook / secret 只从参数或环境变量读，不写进仓库，也不打进日志。
- 通知失败不影响构建结论：脚本恒定返回 0，只在日志里报错。
- 没配 webhook 的渠道会被静默跳过，方便本地调试。
- JUnit 文件不存在（构建在收集阶段就挂了）时仍然发通知，只报"未产生测试结果"。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

ROOT = Path(__file__).resolve().parent.parent

# 群里只列这么多条明细，其余指向 Jenkins。
MAX_FAILURES_SHOWN = 10
MAX_REASON_CHARS = 160
# 企微 markdown 正文上限 4096 字节，超了整条消息会被拒收。按字节数卡，
# 因为中文一个字 3 字节，光按条数限制兜不住。
WECOM_CONTENT_LIMIT = 3800
REQUEST_TIMEOUT = 10


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Push Jenkins build result to WeCom / DingTalk.")
    parser.add_argument("--junit", default="reports/jenkins/junit.xml", help="pytest 生成的 JUnit XML 路径。")
    parser.add_argument("--entry", default="all", help="执行的 answer 入口，进消息标题。")
    parser.add_argument("--status", default="FAILURE", help="构建结论：SUCCESS / FAILURE / ABORTED。")
    parser.add_argument("--duration-seconds", type=float, default=0.0, help="构建耗时，单位秒。")
    parser.add_argument("--build-number", default="", help="Jenkins 构建号。")
    parser.add_argument("--build-url", default="", help="Jenkins 构建地址。")
    parser.add_argument("--wecom-webhook", default=os.getenv("WECOM_WEBHOOK", ""), help="企业微信群机器人 webhook。")
    parser.add_argument("--dingtalk-webhook", default=os.getenv("DINGTALK_WEBHOOK", ""), help="钉钉群机器人 webhook。")
    parser.add_argument("--dingtalk-secret", default=os.getenv("DINGTALK_SECRET", ""), help="钉钉机器人加签密钥。")
    parser.add_argument("--only-on-failure", action="store_true", help="只有失败时才推送，成功静默。")
    parser.add_argument("--dry-run", action="store_true", help="只打印消息内容，不真的推送。")
    return parser.parse_args()


def _as_int(value: Optional[str]) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _display_name(raw: str) -> str:
    """把 pytest 的 `test_answer_yaml[xxx]` 参数化 id 还原成用例名。"""
    name = str(raw or "").strip()
    if name.endswith("]") and "[" in name:
        name = name[name.index("[") + 1 : -1]
    return name


def _short_reason(element: ET.Element) -> str:
    """从 failure/error 节点里取一句人能看懂的失败原因。

    pytest 把断言消息放在 message 属性里，多行，末尾会附一行 `assert not [...]`
    复述断言表达式，没有信息量，去掉。
    """
    text = (element.get("message") or "").strip() or (element.text or "").strip()

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("assert ")
    ]
    text = " ".join(lines)

    # 每次运行生成的 username 对排查没意义；`]` 不能被 \S+ 吃掉，否则下面的
    # [label] 前缀就匹配不上了。
    text = re.sub(r"\|username=[^\s\]]+", "", text)
    text = re.sub(r"^AssertionError:\s*", "", text)
    # 用例名已经在消息里单独占一行，这里的 [suite::file::case] 前缀是重复的。
    text = re.sub(r"^\[[^\]]*\]\s*", "", text)

    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_REASON_CHARS:
        text = text[: MAX_REASON_CHARS - 1] + "…"
    return text or "no message"


def _parse_junit(path: Path) -> Dict[str, Any]:
    """读 JUnit XML。文件不存在时返回一份空结果，让通知照常发出去。"""
    if not path.exists():
        return {"missing": True, "total": 0, "failures": 0, "errors": 0, "skipped": 0, "cases": []}

    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))

    total = failures = errors = skipped = 0
    failed_cases: List[Tuple[str, str]] = []

    for suite in suites:
        total += _as_int(suite.get("tests"))
        failures += _as_int(suite.get("failures"))
        errors += _as_int(suite.get("errors"))
        skipped += _as_int(suite.get("skipped"))
        for case in suite.findall("testcase"):
            problem = case.find("failure")
            if problem is None:
                problem = case.find("error")
            if problem is not None:
                failed_cases.append((_display_name(case.get("name", "")), _short_reason(problem)))

    return {
        "missing": False,
        "total": total,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "cases": failed_cases,
    }


def _format_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def _build_message(
    summary: Dict[str, Any],
    entry: str,
    status: str,
    display_duration: str,
    build_url: str,
    build_number: str,
) -> str:
    failed = summary["failures"] + summary["errors"]
    passed_build = status.upper() == "SUCCESS" and failed == 0
    headline = "通过" if passed_build else "失败"

    header: List[str] = [f"**answer 定时回归 {headline}**"]
    if summary["missing"]:
        header.append("> 未产生测试结果（构建可能在收集阶段就中断了）")
    else:
        header.append(
            f"> 入口 {entry} · 用例 {summary['total']} · "
            f"失败 {failed} · 跳过 {summary['skipped']} · 耗时 {display_duration}"
        )

    footer: List[str] = []
    if build_url:
        label = f"#{build_number}" if build_number else "构建详情"
        footer = ["", f"[{label}]({build_url})"]

    if not failed:
        return "\n".join(header + footer)

    budget = WECOM_CONTENT_LIMIT - _byte_len("\n".join(header + footer))
    # 先给"其余 N 条"这行留位置，否则截断提示会被明细挤掉。
    reserve = _byte_len(f"- …… 其余 {len(summary['cases'])} 条见 Jenkins 构建页")

    body: List[str] = [""]
    shown = 0
    for name, reason in summary["cases"]:
        block = f"- **{name}**\n  {reason}"
        cost = _byte_len(block) + 1
        if shown >= MAX_FAILURES_SHOWN or cost > budget - reserve:
            break
        body.append(block)
        budget -= cost
        shown += 1

    remaining = len(summary["cases"]) - shown
    if remaining > 0:
        body.append(f"- …… 其余 {remaining} 条见 Jenkins 构建页")

    return "\n".join(header + body + footer)


def _send(url: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
    """群机器人成功时返回 HTTP 200 + errcode=0。"""
    try:
        response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        return False, f"请求失败: {exc}"

    if response.status_code != 200:
        return False, f"HTTP {response.status_code}: {response.text[:200]}"

    try:
        body = response.json()
    except ValueError:
        return False, f"响应不是 JSON: {response.text[:200]}"

    errcode = body.get("errcode")
    if errcode not in (0, None):
        return False, f"errcode={errcode} errmsg={body.get('errmsg')}"
    return True, "ok"


def _dingtalk_url(webhook: str, secret: str) -> str:
    """开了加签的钉钉机器人要把 timestamp + sign 拼到 URL 上。"""
    if not secret:
        return webhook
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    joiner = "&" if "?" in webhook else "?"
    return f"{webhook}{joiner}timestamp={timestamp}&sign={sign}"


def _mask(url: str) -> str:
    """日志里只留 webhook 的 key 前缀，避免把完整地址打进构建日志。"""
    if not url:
        return "<未配置>"
    match = re.search(r"key=([0-9a-zA-Z\-]{4})", url)
    return f"...key={match.group(1)}***" if match else "<已配置>"


def main() -> int:
    args = _parse_args()

    summary = _parse_junit(Path(args.junit))
    failed = summary["failures"] + summary["errors"]
    passed_build = args.status.upper() == "SUCCESS" and failed == 0

    if args.only_on_failure and passed_build:
        print(f"NOTIFY skipped: build passed and --only-on-failure is set")
        return 0

    content = _build_message(
        summary=summary,
        entry=args.entry,
        status=args.status,
        display_duration=_format_duration(args.duration_seconds),
        build_url=args.build_url,
        build_number=args.build_number,
    )

    print("=" * 60)
    print(content)
    print("=" * 60)

    if args.dry_run:
        print("NOTIFY dry-run: 未实际发送")
        return 0

    title = f"answer 定时回归 {'通过' if passed_build else '失败'}"
    channels: List[Tuple[str, str, Dict[str, Any]]] = []

    if args.wecom_webhook:
        channels.append(
            (
                "企业微信",
                args.wecom_webhook,
                {"msgtype": "markdown", "markdown": {"content": content}},
            )
        )
    if args.dingtalk_webhook:
        channels.append(
            (
                "钉钉",
                _dingtalk_url(args.dingtalk_webhook, args.dingtalk_secret),
                {"msgtype": "markdown", "markdown": {"title": title, "text": content}},
            )
        )

    if not channels:
        print("NOTIFY no channel configured (WECOM_WEBHOOK / DINGTALK_WEBHOOK 都没配)")
        return 0

    delivered = 0
    for name, url, payload in channels:
        ok, detail = _send(url, payload)
        print(f"NOTIFY {name} {_mask(url)} -> {'OK' if ok else 'FAILED'} ({detail})")
        if ok:
            delivered += 1

    if delivered == 0:
        # 通知全挂了也不改构建结论，只提示。
        print("NOTIFY 所有渠道都推送失败，检查 webhook 配置", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
