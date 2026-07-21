import argparse
import fnmatch
import os
import sys
from pathlib import Path

import pytest


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    """解析 pytest 命令行参数，兼容原来按环境和文件筛选的执行习惯。"""
    parser = argparse.ArgumentParser(description="Run API tests with pytest.")
    parser.add_argument("--env", default=None, help="Test environment, for example dev or console.")
    parser.add_argument("--start-dir", default="testcases", help="Directory used by pytest discovery.")
    parser.add_argument("--pattern", default="test_*.py", help="Discovery filename pattern.")
    parser.add_argument("--top-level-dir", default=None, help="Project root added to PYTHONPATH.")
    parser.add_argument("--failfast", action="store_true", help="Stop after the first failure.")
    parser.add_argument("--buffer", action="store_true", help="Keep for compatibility; pytest captures output by default.")
    parser.add_argument("-q", "--quiet", action="store_true", help="Use quiet output.")
    parser.add_argument("-v", "--verbose", action="count", default=1, help="Increase output verbosity.")
    return parser.parse_known_args()


def _discover_pytest_targets(start_dir: Path, pattern: str) -> list[str]:
    if start_dir.is_file():
        return [str(start_dir)]
    if not start_dir.exists():
        raise FileNotFoundError(f"test start directory not found: {start_dir}")
    return [
        str(path)
        for path in sorted(start_dir.rglob("*.py"))
        if fnmatch.fnmatch(path.name, pattern)
    ]


def main() -> int:
    """pytest 命令行入口：设置环境变量、发现测试、执行并返回进程退出码。"""
    args, passthrough_args = parse_args()
    project_root = Path(__file__).resolve().parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    if args.env:
        os.environ["ENV"] = args.env

    top_level_dir = Path(args.top_level_dir).resolve() if args.top_level_dir else project_root
    if str(top_level_dir) not in sys.path:
        sys.path.insert(0, str(top_level_dir))

    start_dir = Path(args.start_dir)
    if not start_dir.is_absolute():
        start_dir = project_root / start_dir
    targets = _discover_pytest_targets(start_dir, args.pattern)

    pytest_args: list[str] = []
    if args.quiet:
        pytest_args.append("-q")
    else:
        pytest_args.append("-" + ("v" * max(args.verbose, 1)))
    if args.failfast:
        pytest_args.append("-x")
    pytest_args.extend(passthrough_args)
    pytest_args.extend(targets or [str(start_dir)])

    return pytest.main(pytest_args)


if __name__ == "__main__":
    raise SystemExit(main())
