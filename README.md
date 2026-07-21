# AI 客服 answer 接口自动化

这个仓库使用 `pytest` 测试 `/chat/answer` 接口。当前结构遵循：

`Python 负责统一执行逻辑，config 负责测试分类，data 负责具体用例。`

核心链路：

`登录 -> 调用 /chat/answer -> 可选查询质检 -> 匹配质检记录 -> 断言回复 / 知识 / 动作 / 匹配度 / 稳定性`

## 目录结构

```text
project_root/
├── api_object/
│   ├── auth_api.py
│   ├── chat_api.py
│   └── quality_inspection_api.py
├── common/
│   └── http_client.py
├── config/
│   ├── answer_test.yaml
│   ├── env.yaml
│   ├── settings.py
│   └── context_runtime.py
├── data/
├── testcases/
│   ├── answer/
│   │   └── test_answer_yaml.py
│   └── common/
│       ├── paths.py
│       ├── case_product.py
│       └── case_order.py
├── .env
├── .env.example
├── run_tests.py
└── requirements.txt
```

## 安装与运行

安装依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

运行全部 answer 测试：

```powershell
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

只运行某一类 suite：

```powershell
$env:ANSWER_SUITES="main_flow"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

运行多个 suite：

```powershell
$env:ANSWER_SUITES="main_flow,context,multiturn"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
```

只做收集验证，不请求接口：

```powershell
.\.venv\Scripts\python.exe run_tests.py --pattern "test_answer_yaml.py" --collect-only -q
```

## 测试分类

测试分类统一写在 `config/answer_test.yaml`：

| suite | mode | 说明 |
|------|------|------|
| `main_flow` | `sequential` | 主流程：单轮/多轮 + 质检断言 |
| `context` | `sequential` | 带历史消息上下文的对话 |
| `multiturn` | `sequential` | 多轮对话，仅验证 answer 回复链路 |
| `match_score` | `sequential` | 校验 answer 返回的 `match_score` |
| `stability` | `stability` | 重复运行 case，统计命中率 |
| `parallel` | `parallel` | 并发执行多条 case |

`mode` 支持：

- `sequential`：每条 case 单独执行。
- `parallel`：一个 suite 内的 case 按 `workers` 并发执行。
- `stability`：同一 case 按 `repeat` 重复执行，并用 `min_pass_rate` 判断是否达标。

## YAML 用例

data 文件只负责具体 case，支持三种写法：

- `turns`
- `questions`
- `request`

推荐写法：

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

规则：

- `turns` 只有一条就是单轮，多条就是多轮。
- 有 `context_messages` 就会先注入历史上下文。
- suite 配置 `quality: true` 时会查询质检记录。
- suite 配置 `match_score: true` 或 expect 中写了 `match_score` 时会校验匹配度。
- suite 配置 `mode: parallel` 时按并发策略执行。
- suite 配置 `mode: stability` 时按稳定性策略执行。

## 环境规则

- 优先读取 data YAML 顶部的 `target_env`。
- `.env` 中的 `ENV` 仅在 YAML 未写 `target_env` 时作为兜底。
- `prod` 会被归一成 `console`。
- 店铺、账号、密码统一放在 `.env`。
- 推荐使用环境专属键：`*_DEV` / `*_CONSOLE`。
