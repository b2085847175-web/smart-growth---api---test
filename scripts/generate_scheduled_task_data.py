"""Generate the dedicated scheduled-task data pack under data/scheduled/.

Sources (existing data/answer YAMLs only, no fabricated questions):
- data/answer/kb_scene_categories/*.yaml  -> scene-phrased single questions
- data/answer/regression/multiturn_cases.yaml        -> multi-question sequences
- data/answer/regression/scene_multiturn_cases.yaml  -> product-URL context + one question
- data/answer/core/shop_585_merged_cases.yaml     -> conversation_record one-shot dialogs,
                                             standalone questions for single pool
- data/answer/smoke/basic_reply_smoke_cases.yaml -> standalone questions for single pool

Rules:
- One question string is used at most once across the whole generated pack.
- Deterministic: source order decides selection, no randomness.
- All suites ship with assertions/quality disabled (scheduled smoke pack).
"""

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ANSWER_DATA_DIR = PROJECT_ROOT / "data" / "answer"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "scheduled"

SCENE_CATEGORY_FILES = [
    "after_sales.yaml",
    "chat.yaml",
    "logistics.yaml",
    "pre_sales.yaml",
    "product_info.yaml",
    "promotion.yaml",
    "purchase.yaml",
    "special_message.yaml",
]

SOURCE_FILES = {
    "scene_categories": [ANSWER_DATA_DIR / "kb_scene_categories" / name for name in SCENE_CATEGORY_FILES],
    "multi_turn": ANSWER_DATA_DIR / "multiturn_cases.yaml",
    "product_context": ANSWER_DATA_DIR / "scene_multiturn_cases.yaml",
    "merged_585": ANSWER_DATA_DIR / "core" / "shop_585_merged_cases.yaml",
    "basic_reply": ANSWER_DATA_DIR / "basic_reply_smoke_cases.yaml",
}

# target case count per generated file
PLAN: Dict[str, int] = {
    "01_scene_questions": 120,
    "02_product_context": 100,
    "03_conversation_history": 60,
    "04_single_question": 120,
    "05_multi_question": 100,
}

SUITE_NAME_BY_FILE = {
    "01_scene_questions": "scheduled_scene_questions",
    "02_product_context": "scheduled_product_context",
    "03_conversation_history": "scheduled_conversation_history",
    "04_single_question": "scheduled_single_question",
    "05_multi_question": "scheduled_multi_question",
}

SUITE_BLOCK = {
    "mode": "sequential",
    "quality": False,
    "match_score": False,
    "final_reply_equals_chat": False,
    "assertions": False,
    "turn_interval_seconds": 1,
}


def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def _clean_question(value: Any) -> str:
    return str(value or "").replace("\n", " ").strip()


def _clean_name(value: Any) -> str:
    return str(value or "").replace("::", "_").strip()


def _turn_questions(case: Dict[str, Any]) -> List[str]:
    questions: List[str] = []
    for turn in case.get("turns") or []:
        if isinstance(turn, str):
            questions.append(_clean_question(turn))
        elif isinstance(turn, dict):
            questions.append(_clean_question(turn.get("question") or turn.get("message")))
    for question in case.get("questions") or []:
        questions.append(_clean_question(question))
    return [question for question in questions if question]


def _alloc_quota(total: int, pool_sizes: Dict[str, int]) -> Dict[str, int]:
    """Largest-remainder proportional split with at least 1 per category."""
    grand_total = sum(pool_sizes.values())
    raw = {key: total * size / grand_total for key, size in pool_sizes.items()}
    quota = {key: max(1, int(value)) for key, value in raw.items()}
    remaining = total - sum(quota.values())
    by_fraction = sorted(raw, key=lambda key: raw[key] - int(raw[key]), reverse=True)
    index = 0
    while remaining > 0:
        quota[by_fraction[index % len(by_fraction)]] += 1
        remaining -= 1
        index += 1
    while remaining < 0:
        over = [key for key in quota if quota[key] > 1 and quota[key] > raw[key]]
        if not over:
            break
        target = max(over, key=lambda key: quota[key] - raw[key])
        quota[target] -= 1
        remaining += 1
    return quota


def _load_scene_pool() -> List[Dict[str, Any]]:
    pool: List[Dict[str, Any]] = []
    for path in SOURCE_FILES["scene_categories"]:
        data = _read_yaml(path)
        for case in data.get("cases") or []:
            questions = _turn_questions(case if isinstance(case, dict) else {})
            if not questions:
                continue
            pool.append(
                {
                    "question": questions[0],
                    "category": str(case.get("category", "")).strip(),
                    "category_key": str(case.get("category_key", "")).strip(),
                    "scene_name": str(case.get("scene_name", "")).strip(),
                    "source": path.name,
                }
            )
    return pool


def _load_multi_pool() -> List[List[str]]:
    data = _read_yaml(SOURCE_FILES["multi_turn"])
    pool: List[List[str]] = []
    for case in data.get("cases") or []:
        questions = _turn_questions(case if isinstance(case, dict) else {})
        if len(questions) >= 2:
            pool.append(questions)
    return pool


def _load_context_pool() -> List[Dict[str, Any]]:
    data = _read_yaml(SOURCE_FILES["product_context"])
    pool: List[Dict[str, Any]] = []
    for case in data.get("cases") or []:
        if not isinstance(case, dict):
            continue
        context_messages = case.get("context_messages") or []
        questions = _turn_questions(case)
        if not context_messages or not questions:
            continue
        pool.append(
            {
                "context_messages": [
                    {"role": item.get("role"), "content": str(item.get("content", "")).strip()}
                    for item in context_messages
                    if isinstance(item, dict)
                ],
                "question": questions[0],
            }
        )
    return pool


def _load_history_pool() -> List[Dict[str, Any]]:
    data = _read_yaml(SOURCE_FILES["merged_585"])
    pool: List[Dict[str, Any]] = []
    for case in data.get("cases") or []:
        if not isinstance(case, dict):
            continue
        record = case.get("conversation_record")
        if not record:
            continue
        pool.append({"name": _clean_name(case.get("name")), "conversation_record": record})
    return pool


def _load_single_candidate_pools() -> Dict[str, List[str]]:
    """Ordered standalone-question pools; used top-down when filling the single file."""
    pools: Dict[str, List[str]] = {}

    leftover: List[str] = []
    data = _read_yaml(SOURCE_FILES["multi_turn"])
    for case in data.get("cases") or []:
        leftover.extend(_turn_questions(case if isinstance(case, dict) else {}))
    pools["multiturn_leftover"] = leftover

    standalone: List[str] = []
    data = _read_yaml(SOURCE_FILES["merged_585"])
    for case in data.get("cases") or []:
        if not isinstance(case, dict) or case.get("conversation_record"):
            continue
        if case.get("context_messages") or case.get("product_url") or case.get("inquiry_product"):
            continue
        standalone.extend(_turn_questions(case))
    pools["merged_585_standalone"] = standalone

    pools["basic_reply"] = _turn_questions_batch(SOURCE_FILES["basic_reply"])

    context_leftover: List[str] = []
    data = _read_yaml(SOURCE_FILES["product_context"])
    for case in data.get("cases") or []:
        questions = _turn_questions(case if isinstance(case, dict) else {})
        context_leftover.extend(questions)
    pools["scene_multiturn_leftover"] = context_leftover
    return pools


def _turn_questions_batch(path: Path) -> List[str]:
    data = _read_yaml(path)
    questions: List[str] = []
    for case in data.get("cases") or []:
        questions.extend(_turn_questions(case if isinstance(case, dict) else {}))
    return questions


def _pick_unique(questions: Iterable[str], used: set) -> List[str]:
    picked: List[str] = []
    for question in questions:
        if question and question not in used and question not in picked:
            picked.append(question)
            used.add(question)
    return picked


def _build_scene_cases(used: set, total: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    pool = _load_scene_pool()
    ordered_keys: List[str] = []
    for item in pool:
        key = item["category_key"] or item["category"] or "OTHER"
        if key not in ordered_keys:
            ordered_keys.append(key)
    pool_sizes = Counter(
        (item["category_key"] or item["category"] or "OTHER") for item in pool
    )
    quota = _alloc_quota(total, {key: pool_sizes[key] for key in ordered_keys})

    cases: List[Dict[str, Any]] = []
    per_category: Dict[str, int] = {key: 0 for key in ordered_keys}
    index = 1
    # first pass: quota per category, source order inside each category
    for key in ordered_keys:
        taken = 0
        for item in pool:
            if taken >= quota[key]:
                break
            item_key = item["category_key"] or item["category"] or "OTHER"
            if item_key != key or item["question"] in used:
                continue
            used.add(item["question"])
            taken += 1
            cases.append(
                {
                    "name": f"{key}_{index:03d}",
                    "category": item["category"],
                    "scene_name": item["scene_name"],
                    "questions": [item["question"]],
                }
            )
            index += 1
        per_category[key] = taken
    # second pass: top up from any category while quota is unmet overall
    if len(cases) < total:
        for item in pool:
            if len(cases) >= total:
                break
            if item["question"] in used:
                continue
            used.add(item["question"])
            key = item["category_key"] or item["category"] or "OTHER"
            per_category[key] = per_category.get(key, 0) + 1
            cases.append(
                {
                    "name": f"{key}_{index:03d}",
                    "category": item["category"],
                    "scene_name": item["scene_name"],
                    "questions": [item["question"]],
                }
            )
            index += 1
    return cases, per_category


def _build_multi_cases(used: set, total: int) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for questions in _load_multi_pool():
        if len(cases) >= total:
            break
        unique_questions = _pick_unique(questions, set())  # in-case dedupe first
        unique_questions = [question for question in unique_questions if question not in used]
        if len(unique_questions) < 2:
            continue
        used.update(unique_questions)
        cases.append({"name": f"multi_{len(cases) + 1:03d}", "questions": unique_questions})
    return cases


def _build_context_cases(used: set, total: int) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for item in _load_context_pool():
        if len(cases) >= total:
            break
        question = item["question"]
        if question in used:
            continue
        used.add(question)
        cases.append(
            {
                "name": f"context_{len(cases) + 1:03d}",
                "context_messages": item["context_messages"],
                "questions": [question],
            }
        )
    return cases


def _build_history_cases(used: set, total: int) -> List[Dict[str, Any]]:
    pool = _load_history_pool()
    if not pool:
        return []
    # 官方质检点记录的第 2~4 轮用户消息是同一模板话术,只有首轮场景描述是唯一问法。
    # 模板记录截取到第一轮 user/assistant,让实际提问落在唯一的场景描述上;
    # 真实对话(各轮问法都唯一)优先保留完整记录,末句撞车时才降级截取。
    last_message_counter: Counter = Counter()
    for item in pool:
        last_user = [m for m in item["conversation_record"] if m.get("role") == "user"][-1]
        last_message_counter[_clean_question(last_user.get("content"))] += 1

    candidates: List[Dict[str, Any]] = []
    for item in pool:
        record = item["conversation_record"]
        user_questions = [
            _clean_question(m.get("content")) for m in record if m.get("role") == "user"
        ]
        first_question = user_questions[0]
        is_real = last_message_counter[user_questions[-1]] == 1
        candidates.append(
            {
                "name": item["name"],
                "full_record": record,
                "trimmed_record": record[:2],
                "asked_full": user_questions[-1],
                "asked_trimmed": first_question,
                "is_real": is_real,
            }
        )
    candidates.sort(key=lambda item: not item["is_real"])  # real dialogs first, order stable

    cases: List[Dict[str, Any]] = []
    for item in candidates:
        if len(cases) >= total:
            break
        if item["asked_full"] and item["asked_full"] not in used:
            asked, record = item["asked_full"], item["full_record"]
        elif item["asked_trimmed"] and item["asked_trimmed"] not in used:
            asked, record = item["asked_trimmed"], item["trimmed_record"]
        else:
            continue
        used.add(asked)
        cases.append(
            {
                "name": item["name"] or f"history_{len(cases) + 1:03d}",
                "conversation_record": record,
            }
        )
    return cases


def _build_single_cases(used: set, total: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    cases: List[Dict[str, Any]] = []
    per_pool: Counter = Counter()
    for pool_name, questions in _load_single_candidate_pools().items():
        for question in questions:
            if len(cases) >= total:
                break
            if not question or question in used:
                continue
            used.add(question)
            cases.append({"name": f"single_{len(cases) + 1:03d}", "questions": [question]})
            per_pool[pool_name] += 1
        if len(cases) >= total:
            break
    return cases, dict(per_pool)


def _suite_block(suite_name: str) -> Dict[str, Any]:
    return {"name": suite_name, **SUITE_BLOCK}


def _case_asked_questions(case: Dict[str, Any]) -> List[str]:
    """Questions the engine will actually send: turns/questions, or the last
    user message of a conversation_record (one-shot mode)."""
    questions = _turn_questions(case)
    if questions or not isinstance(case, dict):
        return questions
    record = case.get("conversation_record") or []
    for message in reversed(record):
        if isinstance(message, dict) and message.get("role") == "user":
            question = _clean_question(message.get("content"))
            if question:
                return [question]
    return []


def _write_suite(path: Path, suite_name: str, header: str, cases: List[Dict[str, Any]]) -> None:
    payload = {"suite": _suite_block(suite_name), "target_env": "dev", "cases": cases}
    lines = [header, ""]
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
        yaml.safe_dump(
            payload,
            file,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=1000,
        )


def generate(output_dir: Path) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    used: set = set()

    scene_cases, scene_per_category = _build_scene_cases(used, PLAN["01_scene_questions"])
    multi_cases = _build_multi_cases(used, PLAN["05_multi_question"])
    context_cases = _build_context_cases(used, PLAN["02_product_context"])
    history_cases = _build_history_cases(used, PLAN["03_conversation_history"])
    single_cases, single_per_pool = _build_single_cases(used, PLAN["04_single_question"])

    outputs = [
        (
            "01_scene_questions.yaml",
            "scheduled_scene_questions",
            "# 定时任务数据:KB 场景问法(每条一个独立问法,带分类/场景元数据,不断言)\n"
            "# category/scene_name 仅为备注信息,引擎不消费;后续要开启场景命中校验时再加 expect.scene。",
            scene_cases,
        ),
        (
            "02_product_context.yaml",
            "scheduled_product_context",
            "# 定时任务数据:商品链接上下文 + 单条追问(先注入商品 URL,再提问)",
            context_cases,
        ),
        (
            "03_conversation_history.yaml",
            "scheduled_conversation_history",
            "# 定时任务数据:完整对话记录(conversation_record 一次性发送)\n"
            "# 官方质检点记录的后续追问为同一模板话术,已截取首轮场景对话,保证每条实际提问不重复。",
            history_cases,
        ),
        (
            "04_single_question.yaml",
            "scheduled_single_question",
            "# 定时任务数据:单条独立问题(无上下文,仅验证基础回复链路)",
            single_cases,
        ),
        (
            "05_multi_question.yaml",
            "scheduled_multi_question",
            "# 定时任务数据:多问题连问(同一会话内按顺序发送多条问题,累积上下文)",
            multi_cases,
        ),
    ]

    summary: Dict[str, Any] = {}
    pack_questions: set = set()
    for filename, suite_name, header, cases in outputs:
        path = output_dir / filename
        _write_suite(path, suite_name, header, cases)
        asked_questions = [
            question
            for case in cases
            for question in _case_asked_questions(case)
        ]
        pack_questions.update(asked_questions)
        summary[filename] = {
            "suite": suite_name,
            "cases": len(cases),
            "questions": len(asked_questions),
        }

    manifest = {
        "generated_by": "scripts/generate_scheduled_task_data.py",
        "target_env": "dev",
        "assertions": "disabled (suite.assertions=false, suite.quality=false)",
        "unique_questions_total": len(pack_questions),
        "dedupe_rule": "one question string used at most once across all files",
        "files": summary,
        "scene_cases_per_category": scene_per_category,
        "single_cases_per_pool": single_per_pool,
    }
    with open(output_dir / "manifest.yaml", "w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(manifest, file, allow_unicode=True, sort_keys=False)

    return {
        "summary": summary,
        "manifest": manifest,
        "total_cases": sum(item["cases"] for item in summary.values()),
        "unique_questions": len(pack_questions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate scheduled-task answer data pack.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir
    result = generate(output_dir)

    print(f"output_dir={output_dir}")
    for filename, info in result["summary"].items():
        print(f"{filename}: cases={info['cases']} questions={info['questions']}")
    print(f"total_cases={result['total_cases']} unique_questions={result['unique_questions']}")
    print(f"scene_per_category={result['manifest']['scene_cases_per_category']}")
    print(f"single_per_pool={result['manifest']['single_cases_per_pool']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
