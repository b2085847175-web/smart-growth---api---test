"""多轮对话并发测试：扫描 data/chat_record 目录下所有 YAML，合并 cases 后线程池并发执行。"""

import concurrent.futures
import os
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from api_object.auth_api import AuthAPI
from api_object.chat_api import ChatAPI
from common.http_client import create_http_client
from config.context_runtime import load_context_runtime
from config.project_env import resolve_suite_target_env
from testcases.test_chat_multiturn_yaml import (
    _build_auth_header,
    _run_multiturn_flow,
    _safe_print,
)

_DEFAULT_CHAT_RECORD_DIR = "chat_record"


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_chat_record_dir() -> Path:
    env_dir = os.getenv("CHAT_RECORD_DIR", "").strip()
    if env_dir:
        path = Path(env_dir)
        if not path.is_absolute():
            path = _project_root() / path
        return path
    return _project_root() / "data" / _DEFAULT_CHAT_RECORD_DIR


def _scan_yaml_files(directory: Path) -> List[Path]:
    """扫描目录下所有 .yaml/.yml 文件，按文件名排序。"""
    if not directory.is_dir():
        raise FileNotFoundError(f"chat_record directory not found: {directory}")
    files = sorted(
        f for f in directory.iterdir()
        if f.is_file() and f.suffix in (".yaml", ".yml")
    )
    if not files:
        raise FileNotFoundError(f"no YAML files found in: {directory}")
    return files


def _load_all_cases(directory: Path) -> Dict[str, Any]:
    """加载目录下所有 YAML 文件，合并 cases 列表。"""
    yaml_files = _scan_yaml_files(directory)
    all_cases: List[Dict[str, Any]] = []
    target_env: Optional[str] = None

    for yaml_path in yaml_files:
        with open(yaml_path, "r", encoding="utf-8") as f:
            suite = yaml.safe_load(f) or {}

        file_env = str(suite.get("target_env", "")).strip().lower()
        if target_env is None and file_env:
            target_env = file_env

        cases = suite.get("cases") or []
        if not isinstance(cases, list):
            continue

        for case in cases:
            if isinstance(case, dict):
                case["_source_file"] = yaml_path.name
                all_cases.append(case)

    effective_env = resolve_suite_target_env({"target_env": target_env or ""})
    if effective_env not in {"dev", "console"}:
        raise ValueError(f"target_env must be dev or console, got: {effective_env}")

    _safe_print(
        f"PARALLEL_LOAD dir={directory} files={len(yaml_files)} "
        f"total_cases={len(all_cases)} target_env={effective_env}"
    )

    return {
        "target_env": effective_env,
        "cases": all_cases,
        "files_count": len(yaml_files),
    }


def _create_access_token(runtime: Dict[str, Any]) -> str:
    if runtime["auth_mode"] == "token":
        return runtime["access_token"]

    auth_client_http = create_http_client(
        base_url=runtime["api_base_url"],
        default_headers=runtime["headers"],
    )
    auth_client = AuthAPI(client=auth_client_http)
    try:
        login_response = auth_client.login(
            runtime["login_account"],
            runtime["login_password"],
        )
        assert login_response["status_code"] == 200
        assert login_response["data"].get("code") == 200
        access_token = login_response.get("access_token")
        assert access_token, "login succeeded but accessToken was empty"
        return access_token
    finally:
        auth_client_http.close()


_PARALLEL_SUITE = _load_all_cases(_resolve_chat_record_dir())


class TestChatMultiturnParallel(unittest.TestCase):
    """多轮对话并发测试套件：线程池并发执行所有 cases，最终汇总结果。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.runtime = load_context_runtime(_PARALLEL_SUITE["target_env"])
        cls.access_token = _create_access_token(cls.runtime)
        cls.max_workers = int(os.getenv("MULTITURN_MAX_WORKERS", "4"))
        _safe_print(
            f"PARALLEL_SETUP target_env={cls.runtime['target_env']} "
            f"auth_mode={cls.runtime['auth_mode']} "
            f"base_url={cls.runtime['api_base_url']} "
            f"max_workers={cls.max_workers} "
            f"total_cases={len(_PARALLEL_SUITE['cases'])}"
        )

    def _run_single_case(self, case_data: Dict[str, Any]) -> Optional[str]:
        """在独立线程中运行单条 case，返回 None 表示通过，否则返回错误信息。"""
        client = create_http_client(
            base_url=self.runtime["api_base_url"],
            default_headers=self.runtime["headers"],
        )
        client.set_header("Authorization", _build_auth_header(self.access_token))
        apis = {
            "chat_api": ChatAPI(client=client),
            "runtime": self.runtime,
            "access_token": self.access_token,
        }
        case_name = case_data.get("name", "unknown") if isinstance(case_data, dict) else "unknown"
        source_file = case_data.get("_source_file", "unknown") if isinstance(case_data, dict) else "unknown"
        try:
            _run_multiturn_flow(apis, case_data)
            return None
        except (AssertionError, Exception) as exc:
            return f"[{case_name}] file={source_file} {type(exc).__name__}: {exc}"
        finally:
            client.close()

    def test_multiturn_all_parallel(self) -> None:
        """并发执行所有多轮对话 case。"""
        cases = _PARALLEL_SUITE["cases"]
        if not cases:
            self.skipTest("no cases to run")

        failures: List[str] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_case = {
                executor.submit(self._run_single_case, case): case
                for case in cases
            }
            for future in concurrent.futures.as_completed(future_to_case):
                result = future.result()
                if result is not None:
                    failures.append(result)

        passed = len(cases) - len(failures)
        _safe_print(
            f"PARALLEL_SUMMARY total={len(cases)} passed={passed} "
            f"failed={len(failures)} workers={self.max_workers}"
        )

        if failures:
            detail = "\n".join(f"  FAILURE [{i}] {msg}" for i, msg in enumerate(failures, 1))
            self.fail(
                f"并发测试失败 {len(failures)}/{len(cases)} 条 case:\n{detail}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
