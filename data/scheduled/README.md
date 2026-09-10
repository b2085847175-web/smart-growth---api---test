# 定时任务专用数据包

这个目录存放跑自动化定时任务的数据,与 `data/answer/` 下的日常调试数据相互独立。
全部数据从 `data/answer/` 现有文件提取,不包含编造的问法。

## 概况

- **总量**:500 个 case,对应每轮运行 500 个用户(引擎每条 case 每次运行生成唯一 `username`)。
- **问法**:857 个实际提问,全包内**一条问法最多使用一次**,无重复。
- **环境**:`target_env: dev`(店铺 585)。
- **断言**:全部关闭(`assertions: false`、`quality: false`),只验证接口返回有效回复,适合定时巡检。

## 文件分类

| 文件 | suite | case 数 | 提问数 | 说明 |
|------|-------|--------|--------|------|
| `01_scene_questions.yaml` | scheduled_scene_questions | 120 | 120 | KB 场景问法,每条一个独立问法;`category`/`scene_name` 仅为备注元数据,引擎不消费 |
| `02_product_context.yaml` | scheduled_product_context | 100 | 100 | 先注入商品链接上下文,再单条追问 |
| `03_conversation_history.yaml` | scheduled_conversation_history | 60 | 60 | 完整对话记录(`conversation_record` 一次性发送) |
| `04_single_question.yaml` | scheduled_single_question | 120 | 120 | 无上下文的单条独立问题 |
| `05_multi_question.yaml` | scheduled_multi_question | 100 | 457 | 同一会话内连问 3~5 条问题,累积上下文 |

`manifest.yaml` 是生成清单(各文件 case/提问数、场景分类分布),由脚本自动产出。

## 数据来源

| 生成文件 | 提取自 |
|----------|--------|
| 01 场景问法 | `data/answer/kb_scene_categories/` 8 个分类文件(按分类体量按比例抽取,8 个分类全覆盖) |
| 02 商品上下文 | `data/answer/regression/scene_multiturn_cases.yaml`(取每条的首问) |
| 03 对话记录 | `data/answer/core/shop_585_merged_cases.yaml` 的 `conversation_record` |
| 04 单问题 | `data/answer/regression/multiturn_cases.yaml` 剩余问法 + 585 独立问题 |
| 05 多问题 | `data/answer/regression/multiturn_cases.yaml` 的完整连问序列 |

### 03 对话记录的截取规则

源数据的 80 条记录中,77 条是官方质检点对话:第 2~4 轮用户消息是同一模板话术
("我还想确认一下…"、"请结合刚才的情况…" 等),只有首轮场景描述是唯一问法。
因此生成时:

- 模板记录**截取到第一轮 user/assistant 对话**,让实际提问落在唯一的场景描述上;
- 3 条各轮问法都唯一的真实对话**保留完整 4 轮记录**,优先入选;
- 末句提问与其它套件撞车的记录降级为截取版本,保证全包零重复。

## 重新生成

生成逻辑在 `scripts/generate_scheduled_task_data.py`,确定性抽取(按源文件顺序,无随机),可随时重跑覆盖:

```powershell
.\.venv\Scripts\python.exe scripts\generate_scheduled_task_data.py
```

修改每类数量时调整脚本顶部的 `PLAN` 字典。

## 定时任务接入

- 执行入口为 `testcases/answer/test_scheduled_answer_yaml.py`,只读取本目录的 5 个 YAML,
  不会混入 `data/answer/` 的日常调试数据。
- 5 个 suite 当前均保持 `mode: sequential`,每日按顺序执行,实际同时运行的 Case 为 1 个,
  用于控制接口压力;如需调整,应先经过试运行确认。
- 项目根目录的 `Jenkinsfile` 负责每天触发、锁定 `dev/585`、执行收集检查、运行任务和归档
  `reports/scheduled/` 下的 JUnit 报告。
- Jenkins 凭据 ID 默认写为 `zhiyan-dev-login`,需要在 Jenkins 中配置为 dev 环境测试账号密码。
- 若之后要开启场景命中校验:给 `01_scene_questions.yaml` 的 case 加回
  `expect.scene: <scene_name>` 并把该文件 suite 的 `assertions`/`quality` 改为 `true`。

### 手工验证命令

```powershell
.\.venv\Scripts\python.exe -m pytest testcases\answer\test_scheduled_answer_yaml.py --collect-only -q
```

正式执行应由 Jenkins 使用同一个测试文件,并通过 Jenkins 凭据提供
`LOGIN_ACCOUNT_DEV` / `LOGIN_PASSWORD_DEV`。
