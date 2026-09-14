"""项目根 conftest.py：修掉 Windows 中文路径下 pytest-xdist 起不来的问题。

现象（不修的话）：项目路径里含中文（例如 `...\new-test-api - 副本`）时，直接跑

    pytest .\testcases\answer\test_daily_usage.py -n 5

会在建 worker 时报错并 INTERNALERROR：

    UnicodeEncodeError: 'utf-8' codec can't encode character '\udcaf' ... surrogates not allowed
    INTERNALERROR> ... execnet ... EOFError: expected 1 bytes, got 0

原因：xdist 的 worker 是 execnet 拉起的子进程，子进程按系统默认代码页（GBK/cp936）
读取 execnet 的 bootstrap，中文路径被解码成代理字符，再按 UTF-8 编码就失败。

处理：在 pytest 会话开始前给进程环境写上 `PYTHONUTF8=1`。worker 子进程会继承这个
变量并以 UTF-8 模式启动，问题消失。这样 `pytest -n N`、`run_tests.py ... -n N`
都不需要再手工 `$env:PYTHONUTF8 = "1"`。

只影响 pytest 进程及其派生的 worker，不影响业务代码读写文件的编码
（项目里读文件本来就是显式 `encoding="utf-8"`）。
"""

import os

# setdefault：用户显式设置过的值优先，不覆盖。
os.environ.setdefault("PYTHONUTF8", "1")
