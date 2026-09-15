"""`/chat/answer` 执行入口：key / daily / regression / all。

- key：精简回归集，约 200 条。**Jenkins 每日任务默认用这个**，覆盖人工复核过的
  用例、官方质检点、各分类场景的代表性样本。
- daily：日常执行，只放少量高频验证数据。
- regression：回归执行，回归专项和基础冒烟数据都在这里。
- all：全量，覆盖 data/answer 下所有 dev 环境数据（2916 条），偶尔手动跑。
- 新增文件时，直接把 YAML 路径加到下面的列表里。
"""

from typing import Dict, List

# 日常执行数据；保持 1-2 个文件，方便每天快速跑。
DAILY_FILES: List[str] = [
    # AI质检记录（console 10 个店铺 116 条）对应的真实会话上下文用例，其中 20 条带人工复核结论。
    "data/answer/daily/ai_quality_tag_review_context_cases.yaml",
]

# 回归执行数据；和日常完全分开。
REGRESSION_FILES: List[str] = [
    "data/answer/regression/multiturn_cases.yaml",
    "data/answer/regression/official_quality_points_cases.yaml",
    "data/answer/regression/scene_multiturn_cases.yaml",
    "data/answer/regression/usage_instruction_scene_cases.yaml",
    "data/answer/smoke/basic_reply_smoke_cases.yaml",
    "data/answer/smoke/random_account_cases.yaml",
]

# 全量入口：每天定时回归跑 data/answer 下所有 dev 环境数据（2916 条用例）。
#
# 三条口径：
# - kb_scene_categories 只注册 all_categories.yaml。它是 product_info / chat /
#   after_sales / promotion / logistics / purchase / special_message / pre_sales
#   这 8 个分类文件的完整超集（1369 条逐条比对过），一起注册会让同一批用例跑两遍。
# - target_env 为 console / prod 的文件不在定时任务范围内，这里不注册：
#   daily_context_follow_up_cases.yaml、received_goods_image_context.yaml、
#   shop_347_formal_usage_steps.yaml。
# - data/scheduled/ 也不在范围内（README 已声明废弃）。core/ 虽然 README 写了
#   "不再作为基础包强制执行"，但属于"所有 data 下的数据"，仍然计入。
ALL_FILES: List[str] = [
    # daily：日常高频验证与线上反馈复现
    "data/answer/daily/ai_quality_tag_review_context_cases.yaml",
    "data/answer/daily/ai_core_intent_qc_context_76.yaml",
    "data/answer/daily/yusu_20260907_qc_context_cases.yaml",
    "data/answer/daily/daily_product_link_comparison.yaml",
    "data/answer/daily/online_feedback_shop585_blackhead_recommendation.yaml",
    # KB 场景分类（超集，只加这一个文件）
    "data/answer/kb_scene_categories/all_categories.yaml",
    # 回归专项
    "data/answer/regression/multiturn_cases.yaml",
    "data/answer/regression/scene_multiturn_cases.yaml",
    "data/answer/regression/official_quality_points_cases.yaml",
    "data/answer/regression/usage_instruction_scene_cases.yaml",
    # 基础冒烟
    "data/answer/smoke/basic_reply_smoke_cases.yaml",
    "data/answer/smoke/random_account_cases.yaml",
    # 历史基础数据（README 标了不再强制执行，但按"全部数据"口径仍然纳入）
    "data/answer/core/shop_585_merged_cases.yaml",
    "data/answer/core/screenshot_history_cases.yaml",
]

# 精简回归集：约 200 条，Jenkins 每日任务默认用这个。
#
# 由 scripts/build_key_regression_cases.py 从上面这些来源抽样生成，源数据更新后
# 重跑那个脚本即可。分两个文件是因为 quality 开关不能混：
# 见文件里 key_assertions / key_smoke 的说明。
KEY_FILES: List[str] = [
    "data/answer/regression/key_assertions.yaml",
    "data/answer/regression/key_smoke.yaml",
]

# 对外暴露四个入口。
ANSWER_ENTRIES: Dict[str, List[str]] = {
    "key": KEY_FILES,
    "daily": DAILY_FILES,
    "regression": REGRESSION_FILES,
    "all": ALL_FILES,
}

KEY_ENTRY = "key"
DAILY_ENTRY = "daily"
REGRESSION_ENTRY = "regression"
ALL_ENTRY = "all"
