"""从 data/ 下全部用例里挑出一套精简回归集（约 200 条）。

背景：`all` 入口有 2916 条，日常回归跑不完也没必要。这个脚本按"价值 + 覆盖"
从各来源抽样，生成两个文件：

- ``data/answer/regression/key_assertions.yaml``  有业务断言、要查质检的
- ``data/answer/regression/key_smoke.yaml``       只跑接口不查质检的

为什么分两个文件：runner 里 ``quality: true`` 表示**每条用例都必须匹配到质检记录，
否则判失败**（见 common/answer_runner.py 的 _query_turn_quality 之后那段断言）。
把原本 quality=false 的用例混进 quality=true 的 suite 会产生虚假失败。

抽样口径：

- 有人工复核结论（``tag_review``）的用例优先，全要。
- ``all_categories.yaml`` 按分类占比 + 场景轮转抽样，优先铺开场景数而不是同一
  场景多问法。它是目前**唯一带有效业务断言的大块数据**（断言知识场景命中，
  实测 60 条只挂 1 条）。
- ``official_quality_points_cases.yaml`` 已移除全部 expect（原先断言二级质检点
  名称，实测 80 条 100% 失败，期望本身未经有效性验证），现在只跑接口不判内容，
  所以放在 smoke 组。
- 其余来源按配额均匀取样。

用法：

    .\\.venv\\Scripts\\python.exe scripts\\build_key_regression_cases.py
    .\\.venv\\Scripts\\python.exe scripts\\build_key_regression_cases.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_ASSERTIONS = ROOT / "data" / "answer" / "regression" / "key_assertions.yaml"
OUT_SMOKE = ROOT / "data" / "answer" / "regression" / "key_smoke.yaml"

# (源文件, 配额, 抽样方式)
#   review_first : 有人工复核结论的优先
#   scene_spread : 按场景轮转，优先铺开场景数
#   even         : 从头均匀取样
ASSERTION_SOURCES = [
    # KB 场景分类，按场景铺开
    ("data/answer/kb_scene_categories/all_categories.yaml", 60, "scene_spread"),
    # 真实质检问题会话：先取有人工复核的，再补
    ("data/answer/daily/ai_quality_tag_review_context_cases.yaml", 28, "review_first"),
    ("data/answer/daily/ai_core_intent_qc_context_76.yaml", 12, "even"),
    ("data/answer/regression/usage_instruction_scene_cases.yaml", 6, "even"),
    ("data/answer/daily/yusu_20260907_qc_context_cases.yaml", 4, "even"),
    # 线上反馈复现，本身带断言
    ("data/answer/daily/online_feedback_shop585_blackhead_recommendation.yaml", 1, "even"),
]

SMOKE_SOURCES = [
    # 官方质检点：原先是断言二级质检点名称的，实测 80 条 100% 失败，期望本身
    # 未经有效性验证。已全部移除 expect 并把 suite 的 assertions/quality 关掉，
    # 现在只跑接口不判内容，所以从断言组挪到这里。
    ("data/answer/regression/official_quality_points_cases.yaml", 70, "even"),
    ("data/answer/smoke/basic_reply_smoke_cases.yaml", 8, "even"),
    # 多轮能力要留一点覆盖，但只取少量
    ("data/answer/regression/scene_multiturn_cases.yaml", 5, "even"),
    ("data/answer/smoke/random_account_cases.yaml", 4, "even"),
    ("data/answer/daily/daily_product_link_comparison.yaml", 1, "even"),
]

ASSERTION_SUITE = {
    "name": "key_assertions",
    "mode": "sequential",
    "quality": True,
    "assertions": True,
    "match_score": False,
    "final_reply_equals_chat": False,
    "turn_interval_seconds": 1,
    "quality_retries": 10,
    "quality_retry_interval_seconds": 2,
}

SMOKE_SUITE = {
    "name": "key_smoke",
    "mode": "sequential",
    "quality": False,
    "assertions": False,
    "match_score": False,
    "final_reply_equals_chat": False,
    "turn_interval_seconds": 1,
}

HEADER = """# 由 scripts/build_key_regression_cases.py 生成，请勿手工编辑。
# 精简回归集，约 200 条。完整集见 `all` 入口（2916 条）。
#
# 选材原则：人工复核过的用例优先；官方质检点用得最多（断言二级质检点名称）；
# KB 场景分类按场景轮转，优先铺开场景覆盖而不是同一场景多问法。
"""


def _load_cases(path: Path) -> List[Dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases") or []
    return [c for c in cases if isinstance(c, dict) and c.get("name")]


def _tag(name: str) -> str:
    """给用例名加来源前缀，避免不同来源的同名用例在新文件里撞名。"""
    return name.split("/")[-1].replace(".yaml", "").replace("_cases", "")


def _pick_even(cases: List[Dict[str, Any]], quota: int) -> List[Dict[str, Any]]:
    if quota >= len(cases):
        return list(cases)
    step = len(cases) / quota
    return [cases[min(int(i * step), len(cases) - 1)] for i in range(quota)]


def _pick_review_first(cases: List[Dict[str, Any]], quota: int) -> List[Dict[str, Any]]:
    """带 tag_review 的（人工确认过的）先取满，不够再从头补。"""
    reviewed = [c for c in cases if c.get("tag_review")]
    picked = reviewed[:quota]
    if len(picked) < quota:
        rest = [c for c in cases if not c.get("tag_review")]
        picked.extend(_pick_even(rest, quota - len(picked)))
    return picked


def _spread(bucket_lists: List[List[Dict[str, Any]]], quota: int) -> List[Dict[str, Any]]:
    """从若干场景桶里轮转取 quota 条：每个场景先取一条，再取第二条……

    这样配额固定时能覆盖尽量多的场景，而不是把配额耗在同一个场景的多个问法上。
    """
    picked: List[Dict[str, Any]] = []
    round_index = 0
    while len(picked) < quota:
        added = False
        for bucket in bucket_lists:
            if round_index < len(bucket):
                picked.append(bucket[round_index])
                added = True
                if len(picked) >= quota:
                    return picked
        if not added:
            break
        round_index += 1
    return picked


def _pick_scene_spread(cases: List[Dict[str, Any]], quota: int) -> List[Dict[str, Any]]:
    """先按分类的用例数占比分配配额，再在每个分类内部按场景轮转。

    只按场景轮转是不够的：场景在文件里的顺序是按分类排的，直接取前 N 个场景
    会让排在最前面的分类吃掉几乎全部配额（实测 60 条里 58 条都是"商品咨询"）。
    先按占比分到每个分类，才能让各个分类都有代表。
    """
    by_cat: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for case in cases:
        cat = str(case.get("category") or "?")
        scene = str(case.get("scene_name") or cat)
        by_cat.setdefault(cat, {}).setdefault(scene, []).append(case)

    cat_sizes = {cat: sum(len(b) for b in scenes.values()) for cat, scenes in by_cat.items()}
    total = sum(cat_sizes.values()) or 1

    # 最大余数法分配配额，保证总和正好是 quota，且每个分类至少 1 条
    exact = {cat: quota * size / total for cat, size in cat_sizes.items()}
    alloc = {cat: max(int(v), 1) for cat, v in exact.items()}
    while sum(alloc.values()) < quota:
        alloc[max(exact, key=lambda c: exact[c] - alloc[c])] += 1
    while sum(alloc.values()) > quota:
        shrinkable = [c for c in alloc if alloc[c] > 1]
        if not shrinkable:
            break
        alloc[min(shrinkable, key=lambda c: exact[c] - alloc[c])] -= 1

    picked: List[Dict[str, Any]] = []
    for cat, scenes in by_cat.items():
        picked.extend(_spread(list(scenes.values()), alloc[cat]))
    return picked[:quota]


PICKERS = {
    "even": _pick_even,
    "review_first": _pick_review_first,
    "scene_spread": _pick_scene_spread,
}


def _build(sources, suite: Dict[str, Any], purpose: str) -> Dict[str, Any]:
    selected: List[Dict[str, Any]] = []
    summary: List[str] = []

    for rel, quota, mode in sources:
        path = ROOT / rel
        if not path.exists():
            summary.append(f"  !! 源文件不存在，跳过：{rel}")
            continue

        cases = _load_cases(path)
        picked = PICKERS[mode](cases, quota)

        tag = _tag(rel)
        for case in picked:
            copy = dict(case)
            # 名字加来源前缀，保证在新文件里唯一；原始名字留在 _source_case
            copy["_source_case"] = case.get("name")
            copy["name"] = f"{tag}::{case.get('name')}"
            copy["_source_file"] = rel
            selected.append(copy)

        summary.append(f"  {len(picked):>4}/{len(cases):<5} {rel}")

    # 包一层注释，说明这批是怎么来的
    lines = [HEADER, f"# 用途：{purpose}", "suite:"]
    lines += [f"  {k}: {str(v).lower() if isinstance(v, bool) else v}" for k, v in suite.items()]
    lines.append("target_env: dev")
    lines.append("")
    lines.append("cases:")
    body = yaml.safe_dump(selected, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)
    text = "\n".join(lines) + "\n" + "\n".join("  " + ln if ln.strip() else ln for ln in body.splitlines()) + "\n"

    return {"text": text, "count": len(selected), "summary": summary}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成精简回归集（约 200 条）。")
    parser.add_argument("--dry-run", action="store_true", help="只打印选材统计，不写文件。")
    args = parser.parse_args()

    a = _build(ASSERTION_SOURCES, ASSERTION_SUITE, "有业务断言、需查质检的用例")
    s = _build(SMOKE_SOURCES, SMOKE_SUITE, "只跑接口、不查质检的冒烟用例")

    print("=== key_assertions（断言 + 质检）===")
    print("\n".join(a["summary"]))
    print(f"  小计 {a['count']} 条")

    print("\n=== key_smoke（冒烟，无质检）===")
    print("\n".join(s["summary"]))
    print(f"  小计 {s['count']} 条")

    print(f"\n合计 {a['count'] + s['count']} 条")

    if args.dry_run:
        print("\n--dry-run：未写文件")
        return 0

    OUT_ASSERTIONS.parent.mkdir(parents=True, exist_ok=True)
    OUT_ASSERTIONS.write_text(a["text"], encoding="utf-8")
    OUT_SMOKE.write_text(s["text"], encoding="utf-8")
    print(f"\n已写入：\n  {OUT_ASSERTIONS.relative_to(ROOT)}\n  {OUT_SMOKE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
