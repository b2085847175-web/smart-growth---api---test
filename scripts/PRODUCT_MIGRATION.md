# 店铺商品迁移

固定迁移方向：

- 来源：`https://console.zhiyan.chat`，店铺 `576`
- 目标：`https://dev.zhiyan.chat`，店铺 `585`

迁移只保留商品创建必填字段、商品名称和全部商品属性。来源商品内部 `_id` 只在导出进程中用于读取属性，不会写入 YAML，也不会发送到 dev。

## 1. 配置凭证

`.env` 中需要配置：

```text
LOGIN_ACCOUNT_CONSOLE=
LOGIN_PASSWORD_CONSOLE=
LOGIN_ACCOUNT_DEV=
LOGIN_PASSWORD_DEV=
```

console 账号必须拥有店铺 `576`，dev 账号必须拥有店铺 `585`。

## 2. 导出线上快照

```powershell
.\.venv\Scripts\python.exe scripts\export_shop_products.py `
  --page-size 500 `
  --attribute-page-size 100 `
  --part-size 500 `
  --workers 12
```

导出客户端除登录外只允许 `GET`。输出目录位于 `artifacts/product_migration/`，包含 `manifest.yaml` 和多个商品分片。

导出过程会同步写入 `export-progress.jsonl`。如果属性读取中断，使用原来的 `--output-dir` 重新执行；`updated_at` 未变化的商品会直接复用检查点，只重新读取未完成或已变化的商品。

小范围读取验证：

```powershell
.\.venv\Scripts\python.exe scripts\export_shop_products.py `
  --limit 3 `
  --page-size 10 `
  --attribute-page-size 100 `
  --part-size 2 `
  --workers 3
```

带 `--limit` 的快照会标记为部分数据，默认禁止正式导入。

## 3. 校验快照

```powershell
.\.venv\Scripts\python.exe scripts\validate_product_snapshot.py `
  artifacts\product_migration\export-YYYYMMDD_HHMMSS
```

校验内容包括环境、店铺、分片哈希、商品数量、属性数量、必填字段、重复商品 ID，以及是否混入来源 `_id` 或 `shop_id`。

## 4. dev 导入预检查

不带 `--execute` 时只读取店铺 `585` 并生成导入计划：

```powershell
.\.venv\Scripts\python.exe scripts\import_shop_products_to_dev.py `
  artifacts\product_migration\export-YYYYMMDD_HHMMSS `
  --workers 6
```

dev 已存在相同 `product_id` 的商品会整条跳过，不更新商品，也不补充属性。

## 5. 正式导入

```powershell
.\.venv\Scripts\python.exe scripts\import_shop_products_to_dev.py `
  artifacts\product_migration\export-YYYYMMDD_HHMMSS `
  --workers 6 `
  --execute
```

每个商品的处理顺序：

1. 创建 dev 商品。
2. 使用响应中的 dev 商品内部 ID 创建属性。
3. 重新读取属性并核对数量。
4. 核对成功后记录 `product_complete`。

导入状态保存在 `import-progress.jsonl`。中断后用相同命令续跑；已完成商品不会重复创建，部分属性会按“属性名 + 属性值 + 出现次数”恢复。

正式导入的 POST 不会自动重试。响应不确定时会先查询 dev，确认不存在后才允许再次创建。
