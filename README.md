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

## answer 入口

入口映射统一维护在 `config/answer_entries.py`，只保留两个入口：

| entry | 默认测试文件 | 数据范围 |
|---|---|---|
| `daily` | `test_daily_usage.py` | daily（默认 1-2 个文件） |
| `regression` | `test_answer_yaml.py` | regression + smoke |

切换入口：

```powershell
$env:ANSWER_ENTRY = "regression"
.\.venv\Scripts\python.exe run_tests.py --env dev --pattern "test_answer_yaml.py" -v
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

