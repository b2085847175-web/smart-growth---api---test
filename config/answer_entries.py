"""Central registry for /chat/answer data entries.

Data files stay under ``data/answer``.  This file only maps execution entries
to those files, so both regression and daily test entrypoints reuse one
configuration source.
"""

from typing import Dict, List

CORE_FILES: List[str] = [
    "data/answer/core/shop_585_merged_cases.yaml",
    "data/answer/core/screenshot_history_cases.yaml",
]

DAILY_EXTRA_FILES: List[str] = [
    "data/answer/daily/shop_347_formal_usage_steps.yaml",
    "data/answer/daily/daily_product_link_comparison.yaml",
    "data/answer/daily/online_feedback_shop585_blackhead_recommendation.yaml",
]

REGRESSION_EXTRA_FILES: List[str] = [
    "data/answer/regression/multiturn_cases.yaml",
    "data/answer/regression/official_quality_points_cases.yaml",
    "data/answer/regression/scene_multiturn_cases.yaml",
    "data/answer/regression/usage_instruction_scene_cases.yaml",
]

SMOKE_FILES: List[str] = [
    "data/answer/smoke/basic_reply_smoke_cases.yaml",
    "data/answer/smoke/random_account_cases.yaml",
]

SCHEDULED_FILES: List[str] = [
    "data/scheduled/01_scene_questions.yaml",
    "data/scheduled/02_product_context.yaml",
    "data/scheduled/03_conversation_history.yaml",
    "data/scheduled/04_single_question.yaml",
    "data/scheduled/05_multi_question.yaml",
]

KB_SCENE_FILES: List[str] = [
    "data/answer/kb_scene_categories/all_categories.yaml",
]

ANSWER_ENTRIES: Dict[str, List[str]] = {
    # Full regression baseline + regression-only scenarios.
    "regression": CORE_FILES + REGRESSION_EXTRA_FILES,
    # Daily execution baseline + daily-only scenarios.
    "daily_usage": CORE_FILES + DAILY_EXTRA_FILES,
    # Jenkins scheduled smoke pack; kept separate from regression.
    "scheduled": SCHEDULED_FILES,
    # Fast local/link checks.
    "smoke": SMOKE_FILES,
    # Smaller entries are useful for reproduction and targeted execution.
    "daily_question": ["data/answer/daily/daily_product_link_comparison.yaml"],
    "kb_scene": KB_SCENE_FILES,
    "online_feedback": [
        "data/answer/daily/online_feedback_shop585_blackhead_recommendation.yaml"
    ],
    "random_account": ["data/answer/smoke/random_account_cases.yaml"],
}

DAILY_ENTRIES: Dict[str, List[str]] = {
    "daily_usage": ANSWER_ENTRIES["daily_usage"],
    "scheduled": ANSWER_ENTRIES["scheduled"],
}

ANSWER_DEFAULT_ENTRY = "regression"
DAILY_DEFAULT_ENTRY = "daily_usage"
