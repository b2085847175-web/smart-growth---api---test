# AI 客服 answer 接口自动化

这个仓库使用 `pytest` 测试 `/chat/answer`、商品和属性接口。核心原则：

`Python 负责统一执行逻辑，config 负责入口/分类映射，data 负责具体用例数据。`

核心链路：

`登录 -> 调用 /chat/answer -> 可选查询质检 -> 匹配质检记录 -> 断言回复 / 知识 / 动作 / 匹配度 / 稳定性`

## 目录结构

```text
project_root/
├── api_object/                  # 接口对象
├── common/
│   ├── answer_runner.py         # answer 用例执行引擎
│   ├── case_order.py
│   ├── case_product.py
│   ├── http_client.py
│   └── paths.py
├── config/
│   ├── answer_entries.py        # answer 只保留 daily / regression 两个入口
│   ├── context_runtime.py
│   ├── env.yaml
│   ├── project_env.py
│   └── settings.py
├── data/
│   ├── answer/
│   │   ├── core/                # 历史基础数据；默认不再执行
│   │   ├── daily/               # 日常高频验证数据
│   │   ├── regression/          # 回归补充场景
│   │   ├── smoke/               # 归 regression 执行的冒烟数据
│   │   └── kb_scene_categories/ # KB 场景生成数据
│   ├── ai_quality_inspection/   # AI 质检记录快照（records/）
│   ├── kb/scenes/               # KB 场景导出数据
└── scheduled/                 # 历史定时数据；默认不再执行
├── scripts/                     # 数据生成、导出、导入和迁移工具
├── testcases/
│   ├── answer/
│   │   ├── test_answer_yaml.py  # 回归入口 + answer 单元测试
│   │   └── test_daily_usage.py  # 日常执行入口
│   └── product/
│       └── test_product.py
├── run_tests.py
└── requirements.txt
```

## 安装与运行

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 日常执行

```powershell
$env:ANSWER_ENTRY = "daily"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_daily_usage.py" -v
```

### 回归执行

```powershell
$env:ANSWER_ENTRY = "regression"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

### 只做收集校验

```powershell
$env:ANSWER_ENTRY = "regression"
.\.venv\Scripts\python.exe run_tests.py --pattern "test_answer_yaml.py" --collect-only -q
```

### 只执行部分 suite

```powershell
$env:ANSWER_SUITES = "main_flow,context,multiturn"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

### AI 质检聊天记录 → 上下文用例

按「AI 质检有问题的记录 → 对应用户 → 完整聊天记录」拉数据，再生成 `/chat/answer` 的上下文用例：

```powershell
# 1. 导出质检有问题用户的聊天记录（自动翻页取全量）
.\.venv\Scripts\python.exe scripts\ai_quality\export_chat_records.py

# 2. 用真实聊天记录生成 / 重写日常用例 YAML
.\.venv\Scripts\python.exe scripts\ai_quality\generate_context_cases.py
```

- 质检结果走 `POST /api/ai-quality-inspection/list`（`has_issues=true`），聊天记录走
  `GET /api/users/{user_id}/messages`；鉴权优先用 `.env` 里的 `ACCESS_TOKEN_CONSOLE`，
  没配 token 时回退到 `LOGIN_ACCOUNT_CONSOLE` / `LOGIN_PASSWORD_CONSOLE` 登录。
- 常用参数：`--env`、`--shop-ids`、`--start-time` / `--end-time`、`--limit`（调试）、
  `--output-dir`、`--print-records`；详细说明见 `scripts/ai_quality/README.md`。
- 中间快照默认落在 `outputs/ai_quality_chat/`（生成物，不进版本库），随时重跑即可重建。
- 生成脚本输出到 `data/answer/daily/ai_quality_tag_review_context_cases.yaml`（全量重写），
  从既有 YAML 继承人工复核结论 `tag_review`，由 daily 入口执行；用例 `assertions: false`，
  只执行接口观察回复。

## answer 入口

入口映射统一维护在 `config/answer_entries.py`，共四个入口：

| entry | 数据范围 | 收集到的用例数 | 说明 |
|---|---|---:|---|
| `key` | 精简回归集 | **218** | **Jenkins 每日任务默认**，199 条数据用例 + 19 个单测 |
| `daily` | `data/answer/daily` 里 1 个文件 | 135 | 116 条数据用例 + 19 个单测 |
| `regression` | regression + smoke | 1221 | 1202 条 + 19 个单测 |
| `all` | `data/answer` 下全部 dev 数据 | 2935 | 2916 条 + 19 个单测，需要时手动跑 |

（收集数 = 数据用例 + `test_answer_yaml.py` 里那 19 个不发请求的单元测试）

### key —— 精简回归集

`all` 有 2916 条，日常跑不完。`key` 是从里面抽出来的约 200 条，选材口径：

- **人工复核过的用例优先**（`ai_quality_tag_review_context_cases.yaml` 里带
  `tag_review` 字段的 20 条全部纳入）。
- **官方质检点用得最多**（`official_quality_points_cases.yaml` 取 70 条），因为它
  断言的是二级质检点名称，是仓库里最硬的回归依据。
- **KB 场景分类按分类占比 + 场景轮转抽样**（60 条覆盖 60 个不同场景，各分类比例
  与原文件一致）。
- 真实质检问题会话、使用说明场景、冒烟用例各取少量。

由 `scripts/build_key_regression_cases.py` 生成，源数据更新后重跑即可：

```powershell
.\.venv\Scripts\python.exe scripts\build_key_regression_cases.py --dry-run   # 先看选材
.\.venv\Scripts\python.exe scripts\build_key_regression_cases.py             # 实际生成
```

生成的用例名带来源前缀（如 `official_quality_points::未发送欢迎语`），并保留
`_source_file` / `_source_case` 两个字段便于追溯。

**为什么分成 `key_assertions.yaml` 和 `key_smoke.yaml` 两个文件**：runner 里
`quality: true` 表示每条用例都必须匹配到质检记录，否则判失败。把原本
`quality: false` 的用例混进 `quality: true` 的 suite 会产生虚假失败，所以按
质检开关分开。

### all 的两条口径

- `kb_scene_categories/` 只注册 `all_categories.yaml`，它是其余 8 个分类文件的完整超集，
  一起注册会让同一批用例跑两遍。
- 声明 `target_env` 为 console / prod 的文件不在范围内，`data/scheduled/` 也不在。

切换入口：

```powershell
$env:ANSWER_ENTRY = "regression"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

### 本地跑全量

2916 条串行要 8 小时以上，必须开并行：

```powershell
$env:ANSWER_ENTRY = "all"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -n 8 -v
```

只做收集校验（不发请求）：

```powershell
$env:ANSWER_ENTRY = "all"
.\.venv\Scripts\python.exe run_tests.py --pattern "test_answer_yaml.py" --collect-only -q
```

## 新增 answer 用例

详细规则见 `data/answer/README.md`。简要流程：

1. 按用途把 YAML 放到 `daily/` 或 `regression/`。
2. 在 YAML 中定义唯一 `suite.name` 和具体 `cases`。
3. 把文件路径注册到 `config/answer_entries.py` 对应的数据列表。
4. 先执行 `--collect-only` 确认用例能被发现。

YAML 推荐写法：

```yaml
target_env: "dev"

cases:
  - name: "product_recommend_then_promotion"
    context_messages:
      - role: "user"
        content: "https://item.taobao.com/item.htm?id=764834167209"
    turns:
      - question: "推荐一款洗发水"
      - question: "这个适合油头吗"
      - question: "有活动吗"
        expect:
          quality:
            stats_contains:
              scene_knowledge:
                - "活动与促销规则"
```

`turns` 只有一条就是单轮，多条就是多轮；`context_messages` 会先注入历史上下文。用户连续发送两条消息时，在 `context_messages` 中按顺序写多条 user 消息。

## 执行策略

suite 策略写在 YAML 文件头部：

| mode | 说明 |
|---|---|
| `sequential` | 每条 case 单独执行 |
| `parallel` | 一个 suite 内的 case 按 `workers` 并发 |
| `stability` | 同一 case 按 `repeat` 重复执行，按 `min_pass_rate` 判断 |

常用开关：

- `quality: true` 查询并校验质检记录。
- `assertions: true` 启用回复/知识/动作断言。
- `match_score: true` 校验匹配度。
- `final_reply_equals_chat: true` 校验最终回复与 chat 返回一致。
- `turn_interval_seconds` 控制 turn 间隔。
- `run_interval_seconds` 控制 case 间隔。

## 环境规则

- 优先读取 data YAML 顶部的 `target_env`。
- `.env` 中的 `ENV` 只在 YAML 未写 `target_env` 时兜底。
- `prod` 会被归一成 `console`。
- 店铺、账号、密码统一放在 `.env`，推荐使用 `*_DEV` / `*_CONSOLE` 后缀。
- **`.env` 不入库**（已加进 `.gitignore`）。里面是真实密码和 console 的 access token，
  仓库又是公开的。新环境照 `.env.example` 建一份本地的即可。
- **注意**：本地 `.env` 里当前是 `ENV=console`。任何新写的、不声明 `target_env`
  的 YAML 都会落到生产环境。新增数据文件时务必显式写上 `target_env`。

没有 `.env` 时也能跑：`AI_BASE_URL_DEV`、`CHAT_PLATFORM` 这类在 `config/env.yaml`
里都有默认值，账号密码由 Jenkins 凭据注入。已实测验证。

## Jenkins 定时回归

`Jenkinsfile` 是声明式流水线，每天 02:30（Jenkins 服务器时区）跑 `all` 入口的
2916 条用例，用 `pytest-xdist` 并行。

### 节点要求

- Windows 节点（脚本按 `.venv\Scripts\python.exe` 的布局写），agent label 为 `windows`。
- 节点上 `python` 要在 `PATH` 里，用于首次创建 venv。
- venv 建在 workspace 之外（`C:\jenkins-tools\venv-answer-test`），构建之间复用，
  只有 `requirements.txt` 的 SHA256 变化时才重装依赖。换路径改 Jenkinsfile 里的
  `VENV_DIR`。

### 需要的凭据

在 Jenkins 里建好这几条，否则对应环节会失败（通知环节失败不影响构建结论）：

| 凭据 ID | 类型 | 用途 |
|---|---|---|
| `zhiyan-dev-login` | Username/Password | 注入 `LOGIN_ACCOUNT_DEV` / `LOGIN_PASSWORD_DEV` |
| `answer-wecom-webhook` | Secret text | 企业微信群机器人 webhook |
| `answer-dingtalk-webhook` | Secret text | 钉钉群机器人 webhook |
| `answer-dingtalk-secret` | Secret text | 钉钉机器人加签密钥（没开加签就留空） |

凭据注入的环境变量**优先于**仓库里的 `.env`，这是 `config/project_env.py` 里
`reload_project_env` 的既定行为，不需要改代码。钉钉机器人如果开了安全设置，
记得把自定义关键词设成消息里出现的词（例如 `answer`）。

### 构建参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `ENTRY` | `all` | 执行入口，可选 all / daily / regression |
| `WORKERS` | `8` | pytest-xdist 并发进程数 |
| `COLLECT_ONLY` | false | 只做收集校验，不发请求，改数据文件后可以先跑这个 |
| `NOTIFY_ON_SUCCESS` | true | 关掉则只在失败时推送 |

### 流水线阶段

```
Workspace → Prepare python env → Validate data → Unit tests → Run answer cases
```

- `Validate data` 会把收集到的用例数和该入口的下限比对，低于下限直接失败 ——
  防止"数据文件没注册进 `config/answer_entries.py`，跑绿了但实际没跑"。
- `Unit tests` 跑 19 个不发请求的纯逻辑单测，先于接口用例。它挂了说明是环境或代码
  问题，不必再花两小时打接口，也能避免把基础设施故障误读成 AI 回复不稳定。
- 两个测试文件（`test_answer_yaml.py` / `test_daily_usage.py`）都读 `ANSWER_ENTRY`，
  必须分开调用，否则同一份数据会被加载两遍。

### 通知

`scripts/notify_ci.py` 解析 JUnit XML，把失败摘要推到企微和钉钉：

```powershell
.\.venv\Scripts\python.exe scripts\notify_ci.py `
    --junit reports\jenkins\junit.xml --entry all --status FAILURE `
    --duration-seconds 4920 --build-number 128 --build-url http://jenkins/job/answer-daily/128/ `
    --wecom-webhook $env:WECOM_WEBHOOK --dingtalk-webhook $env:DINGTALK_WEBHOOK `
    --dingtalk-secret $env:DINGTALK_SECRET
```

加 `--dry-run` 只打印消息不发送，本地调试用。消息会按企业微信 4096 字节的上限
自动截断失败明细，超出部分指向 Jenkins 构建页。

