"""为 data/test_chat 下用例补充 test_CJ 同款商品上下文（可重复执行）。"""

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from testcases.common.case_product import DEFAULT_PRODUCT_URL, ensure_case_product_context
TEST_CHAT_DIR = ROOT / "data" / "test_chat"


def _yaml_quote(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _format_turn(turn: object, indent: str) -> list[str]:
    if isinstance(turn, str):
        return [f'{indent}- question: "{_yaml_quote(turn)}"']
    if not isinstance(turn, dict):
        return []
    question = str(turn.get("question") or turn.get("message") or "").strip()
    lines = [f'{indent}- question: "{_yaml_quote(question)}"']
    expect = turn.get("expect")
    if expect is not None and str(expect).strip():
        lines.append(f'{indent}  expect: "{_yaml_quote(str(expect))}"')
    return lines


def _format_case(case: dict) -> list[str]:
    case = ensure_case_product_context(case)
    name = case.get("name", "")
    name_text = f'"{_yaml_quote(str(name))}"' if name is not None else '""'
    lines = [f'  - name: {name_text}', "    context_messages:"]
    for message in case.get("context_messages") or []:
        role = _yaml_quote(str(message.get("role", "user")))
        content = _yaml_quote(str(message.get("content", "")))
        lines.append(f'      - role: "{role}"')
        lines.append(f'        content: "{content}"')
    lines.append("    turns:")
    for turn in case.get("turns") or []:
        lines.extend(_format_turn(turn, "      "))
    return lines


def _rewrite_file(path: Path) -> int:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases")
    if not isinstance(cases, list):
        return 0

    updated_cases: list = []
    case_count = 0
    for item in cases:
        if isinstance(item, dict) and item.get("name") is not None:
            updated_cases.append(ensure_case_product_context(item))
            case_count += 1
        else:
            updated_cases.append(item)

    lines = [f'target_env: "{data.get("target_env", "dev")}"', "", "cases:"]
    for case in updated_cases:
        if isinstance(case, dict) and case.get("name") is not None:
            lines.extend(_format_case(case))
        else:
            continue

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return case_count


def main() -> None:
    total = 0
    for path in sorted(TEST_CHAT_DIR.glob("*.yaml")):
        count = _rewrite_file(path)
        print(f"{path.name}: {count} cases")
        total += count
    print(f"done, total cases={total}, product_url={DEFAULT_PRODUCT_URL}")


if __name__ == "__main__":
    main()
