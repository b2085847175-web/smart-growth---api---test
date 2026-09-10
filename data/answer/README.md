# data/answer 数据目录说明

这个目录只存放 `/chat/answer` 的测试数据。入口映射统一在
`config/answer_entries.py` 中维护，现在只保留 `daily` 和 `regression`
两个入口；YAML 文件本身继续维护 `suite` 与 `cases`。

## 目录约定

| 目录 | 执行归属 | 什么时候加文件 |
|---|---|---|
| `daily/` | daily | 日常高频验证、线上反馈复现、上下文补充场景 |
| `regression/` | regression | 大批量多轮、场景、质检点、使用说明回归 |
| `smoke/` | regression | 基础链路检查、随机账号冒烟 |
| `kb_scene_categories/` | 脚本生成数据 | 一般由脚本生成，不建议手工改 |

`core/` 和 `data/scheduled/` 已经不再作为基础包强制执行。

## 新增用例

1. 先选择目录：
   - 日常要跑：放 `daily/`，并保持日常默认只启用 1-2 个文件
   - 回归要跑：放 `regression/`
   - 基础冒烟：放 `smoke/`
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
4. 把文件路径加入 `config/answer_entries.py`：
   - 日常数据：加入 `DAILY_FILES`
   - 回归数据：加入 `REGRESSION_FILES`
5. 执行收集校验：
   ```powershell
   $env:ANSWER_ENTRY = "regression"
   .\.venv\Scripts\python.exe -m pytest testcases\answer\test_answer_yaml.py --collect-only -q
   ```

## 用户连续消息怎么写

用户连续发送多条消息时，把前面的消息按顺序写入 `context_messages`：

```yaml
cases:
  - name: order_and_image_context_follow_up
    context_messages:
      - role: "user"
        content: "[承诺评价返现]"
        media_type: "text"
        media_url: ""
        created_at: 1789013612
      - role: "user"
        content: "[图片消息]"
        media_type: "image"
        media_url: "https://chat-img.pddugc.com/chat-pic-mall-user-v1/2026-09-10/7b788296-8e89-4a8d-b1a4-71bc5c0e797f.jpeg"
        created_at: 1789013613
    turns:
      - question: "帮我查一下这个订单"
```

## 建议命名

- 文件名使用英文小写 + 下划线。
- 一个文件聚合同一业务主题，不要一个 case 建一个文件。
- suite name 和文件名保持一致，方便日志和质检记录排查。
- 需要复现的问题文件名加来源，例如 `online_feedback_*`、`daily_*`。
