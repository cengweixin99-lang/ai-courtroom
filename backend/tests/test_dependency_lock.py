from __future__ import annotations

import tomllib
from pathlib import Path

BACKEND = Path(__file__).parents[1]


def _normalize_package_name(value: str) -> str:
    return value.lower().replace("_", "-")


def _runtime_package_name(requirement: str) -> str:
    # 当前项目依赖均使用 PEP 508 的常见写法；先去除 extra 与版本约束即可得到包名。
    return _normalize_package_name(requirement.split("[", maxsplit=1)[0].split(">", maxsplit=1)[0])


def test_runtime_dependencies_are_present_in_the_locked_install_set() -> None:
    """CI 用 --no-deps 安装 editable 包，lock 缺项必须在导入前被发现。"""

    pyproject = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    runtime_dependencies = {
        _runtime_package_name(item) for item in pyproject["project"]["dependencies"]
    }
    locked_packages = {
        _normalize_package_name(line.split("==", maxsplit=1)[0])
        for line in (BACKEND / "requirements-dev.lock").read_text(encoding="utf-8").splitlines()
        if "==" in line and not line.startswith("#")
    }

    assert runtime_dependencies <= locked_packages
