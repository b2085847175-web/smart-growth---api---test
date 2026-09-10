# data/answer 数据目录说明

这个目录只存放 `/chat/answer` 的测试数据。测试分类和入口映射统一在
`config/answer_entries.py` 中维护；YAML 文件本身继续维护 `suite` 与 `cases`。

## 目录约定

| 目录 | 用途 | 什么时候加文件 |
|---|---|---|
| `core/` | 日常和回归共用的基础数据包 | 主链路、核心回复、稳定复用的高价值用例 |
| `daily/` | 只给日常执行使用的补充场景 | 日常问题、线上反馈、指定店铺复现 |
| `regression/` | 回归专用的场景包 | 大批量多轮、场景、质检点、使用说明回归 |
| `smoke/` | 快速冒烟 / 指定账号复现 | 基础链路检查、随机账号冒烟 |
| `kb_scene_categories/` | KB 场景生成的分类数据 | 一般由脚本生成，不建议手工改 |

`data/scheduled/` 是 Jenkins 定时任务专用数据包，不在 `data/answer` 内重复存放。

## 当前文件

| 文件 | 说明 |
|---|---|
| `core/shop_585_merged_cases.yaml` | shop 585 合并后的主回归数据 |
| `core/screenshot_history_cases.yaml` | 截图和历史消息上下文用例 |
| `daily/shop_347_formal_usage_steps.yaml` | shop 347 正式使用步骤复现 |
| `daily/daily_product_link_comparison.yaml` | 日常问题：商品链接同款判断 |
| `daily/online_feedback_shop585_blackhead_recommendation.yaml` | shop 585 在线反馈复现 |
| `regression/multiturn_cases.yaml` | 多轮对话回归 |
| `regression/official_quality_points_cases.yaml` | 官方质检点回归 |
| `regression/scene_multiturn_cases.yaml` | 商品 URL / 场景上下文多轮回归 |
| `regression/usage_instruction_scene_cases.yaml` | 使用说明场景回归 |
| `smoke/basic_reply_smoke_cases.yaml` | 基础回复冒烟 |
| `smoke/random_account_cases.yaml` | 随机账号冒烟 |
| `kb_scene_categories/*.yaml` | KB 场景分类生成数据；`all_categories.yaml` 是聚合包 |

## 新增用例

1. 先选择目录：
   - 日常要跑：放 `daily/`
   - 回归要跑：放 `regression/`
   - 两边都要跑：放 `core/`
   - 只是快速验证：放 `smoke/`
2. 新建一个主题明确的 YAML，例如 `regression/product_price_cases.yaml`。
3. 文件头部声明一个唯一的 suite：
   ```yaml
   suite:
     name: product_price_cases
     mode: sequential
     quality: true
     assertions: true
     match_score: false
     final_reply_equals_chat: true
     turn_interval_seconds: 1
   target_env: dev

   cases:
     - name: product_price_single_turn
       turns:
         - question: "这个商品现在多少钱"
           expect:
             reply_contains:
               - "价格"
   ```
4. 把文件路径加入 `config/answer_entries.py` 对应 entry：
   - 只加入 `regression`
   - 或同时加入 `daily_usage` / `regression`
   - 只需要临时验证时先加入 `smoke`
5. 执行收集校验：
   ```powershell
   $env:ANSWER_ENTRY = "regression"
   .\.venv\Scripts\python.exe -m pytest testcases\answer\test_answer_yaml.py --collect-only -q
   ```

## 建议命名

- 文件名使用英文小写 + 下划线。
- 一个文件聚合同一业务主题，不要一个 case 建一个文件。
- suite name 和文件名保持一致，方便日志和质检记录排查。
- 需要复现的问题文件名加来源，例如 `online_feedback_*`、`daily_*`。
