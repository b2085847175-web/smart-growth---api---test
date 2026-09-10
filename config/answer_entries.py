"""`/chat/answer` 只保留两个执行入口：daily / regression。

- daily：日常执行，只放少量高频验证数据。
- regression：回归执行，回归专项和基础冒烟数据都在这里。
- 新增文件时，直接把 YAML 路径加到下面的列表里。
"""

from typing import Dict, List

# 日常执行数据；保持 1-2 个文件，方便每天快速跑。
DAILY_FILES: List[str] = [
    "data/answer/daily/daily_context_follow_up_cases.yaml",
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

# 对外只暴露两个入口。
ANSWER_ENTRIES: Dict[str, List[str]] = {
    "daily": DAILY_FILES,
    "regression": REGRESSION_FILES,
}

DAILY_ENTRY = "daily"
REGRESSION_ENTRY = "regression"
