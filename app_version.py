"""
文件说明：
- 文件整体功能：提供应用版本号获取能力，优先从已安装的 Python 包元数据读取版本，
  在包未安装（例如源码直接运行或 PyInstaller 单文件分发）时回退到 pyproject.toml 解析。
- 所属模块：应用顶层工具模块，供 app_update 等模块在检查更新时获取当前版本号。
- 依赖文件：依赖项目根目录下的 pyproject.toml 文件作为版本回退来源；依赖标准库
  importlib.metadata、pathlib、sys、tomllib。
- 对外能力：对外暴露 get_app_version() 函数，返回当前应用版本字符串。
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import sys

import tomllib


def _read_pyproject_version() -> str:
    """
    从 pyproject.toml 文件中读取项目版本号。

    核心作用：作为应用版本号的回退来源，当通过 importlib.metadata 无法获取已安装包版本时使用。
    输入参数：无。
    返回值 (str)：pyproject.toml 中 project.version 字段对应的版本字符串。
    内部关键执行逻辑：
        1. 按优先级构造候选 pyproject.toml 路径：
           - 当前文件同级目录；
           - PyInstaller 运行时的 _MEIPASS 目录；
           - 当前可执行文件父目录；
           - 当前工作目录。
        2. 遍历候选路径，找到第一个存在的文件；
        3. 使用 tomllib 以二进制方式加载 TOML 数据；
        4. 返回 data["project"]["version"] 并强转为字符串；
        5. 若全部候选都不存在则抛出 FileNotFoundError。
    调用位置：由 get_app_version() 在捕获 PackageNotFoundError 后调用。
    """
    candidates = [
        Path(__file__).resolve().with_name("pyproject.toml"),
        Path(getattr(sys, "_MEIPASS", "")).resolve() / "pyproject.toml"
        if getattr(sys, "_MEIPASS", "")
        else None,
        Path(sys.executable).resolve().parent / "pyproject.toml",
        Path.cwd() / "pyproject.toml",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            with candidate.open("rb") as fh:
                data = tomllib.load(fh)
            return str(data["project"]["version"])
    raise FileNotFoundError("pyproject.toml not found for version fallback")


def get_app_version() -> str:
    """
    获取当前应用版本号。

    核心作用：返回 bilitickerbuy 包的已安装版本；若包未安装则回退到读取 pyproject.toml。
    输入参数：无。
    返回值 (str)：当前应用版本字符串，例如 "2.15.4"。
    内部关键执行逻辑：
        1. 尝试调用 importlib.metadata.version("bilitickerbuy") 获取已安装版本；
        2. 若抛出 PackageNotFoundError，则调用 _read_pyproject_version() 作为回退。
    调用位置：由 app_update.fetch_update 等需要在检查更新时知道当前版本的模块调用，
              也可能由 UI 在关于页面展示版本号时调用。
    """
    try:
        return version("bilitickerbuy")
    except PackageNotFoundError:
        return _read_pyproject_version()
