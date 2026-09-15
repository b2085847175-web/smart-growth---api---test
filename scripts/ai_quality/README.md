# AI 质检聊天记录脚本

从正式/测试环境把「AI 质检判定有问题的记录」对应的**完整聊天记录**拉下来，再整理成
`/chat/answer` 的上下文用例 YAML。数据不用入库，随时跑随时有。

## 两条命令

```powershell
# 1. 拉数据：质检有问题记录 -> 用户 -> 聊天记录
.\.venv\Scripts\python.exe scripts\ai_quality\export_chat_records.py

# 2. 生成用例：真实会话 -> data/answer/daily/ai_quality_tag_review_context_cases.yaml（全量重写）
.\.venv\Scripts\python.exe scripts\ai_quality\generate_context_cases.py
```

## 参数

`export_chat_records.py`

| 参数 | 说明 |
| --- | --- |
| `--env` | 目标环境，`console`（默认）/ `dev` |
| `--shop-ids` | 逗号分隔店铺，默认 `888,917,924,925,926,927,928,929,934,941` |
| `--start-time` / `--end-time` | 秒级时间戳，默认 2026-09-13 全天 |
| `--limit` | 只处理前 N 条，调试用 |
| `--output-dir` | 快照输出目录，默认 `outputs/ai_quality_chat` |

`generate_context_cases.py`

| 参数 | 说明 |
| --- | --- |
| `--transcripts` | 聊天记录快照；默认取 `outputs/ai_quality_chat` 下最新的 `*_latest.json` |
| `--output` | 目标 YAML，默认 `data/answer/daily/ai_quality_tag_review_context_cases.yaml` |
| `--timestamp-mode` | `today`（默认，平移到运行当天）/ `keep`（保留原始时间戳） |
| `--reviews-from` | 继承人工复核结论的既有 YAML，默认就是 `--output` |
| `--tag-review-json` | 用复核快照 JSON 作为复核来源（可选，历史数据用） |

## 约定

- 鉴权优先用 `.env` 的 `ACCESS_TOKEN_<ENV>`（console 即 `ACCESS_TOKEN_CONSOLE`），
  没配 token 时回退到 `LOGIN_ACCOUNT_<ENV>` / `LOGIN_PASSWORD_<ENV>` 登录。
- 质检列表接口 `POST /api/ai-quality-inspection/list` 会自动翻页取全量；
  聊天记录接口 `GET /api/users/{user_id}/messages` 只认 `shop_id` / `startTimeStr` / `endTimeStr`，
  且店铺必须在 token 授权范围内，否则返回 404「店铺不存在」。
- 快照里 `transcript` 已裁掉最后一条 user 消息之后的客服回复（供 YAML 当触发语），
  `transcript_full` 是未裁剪的完整会话（人工复核用）。
- `outputs/` 是生成物目录，不进版本库。
