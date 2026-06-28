"""
app_cmd/cli_args.py — 命令行参数与运行配置定义。

文件整体功能：
  定义程序运行所需的全部配置项，包括环境变量读取辅助函数、
  Web UI 启动参数（TickerCliArgs）以及抢票运行配置（BuyCliArgs）。
  所有配置项均支持通过命令行参数、环境变量或配置文件传入。

所属模块：
  CLI 命令层 (app_cmd)

依赖文件：
  - app_cmd.config.BuyConfig   (抢票详细配置 BuyConfig)

对外能力：
  - 提供 _env_bool / _env_optional_int / _env_optional_str 等环境变量读取工具函数。
  - 提供 TickerCliArgs 供 ticker.py 使用，控制 Gradio 服务监听地址与分享选项。
  - BuyCliArgs 实质指向 BuyConfig，由 buy.py 使用。
"""


from __future__ import annotations

import os
from dataclasses import dataclass

from app_cmd.config.BuyConfig import BuyConfig


# ---------------------------------------------------------------------------
# 环境变量读取辅助函数
# ---------------------------------------------------------------------------

def _env_bool(key: str, default: bool) -> bool:
    """
    从环境变量读取布尔值。

    核心作用：
      读取形如 BTB_<KEY> 的环境变量，将其转换为布尔值。

    输入参数：
      key    : str
        环境变量名后缀（完整变量名为 BTB_{key}）。
      default: bool
        环境变量不存在时的默认值。

    返回值：
      bool
        若环境变量值为 "1", "true", "yes", "y", "on"（不区分大小写）返回 True；
        否则返回 False；环境变量不存在时返回 default。

    内部关键执行逻辑：
      1. 通过 os.environ.get 读取 BTB_{key}。
      2. 对非空值做 strip().lower() 后匹配真值集合。

    调用场景：
      被 TickerCliArgs 字段默认值表达式调用。
    """
    raw = os.environ.get(f"BTB_{key}")
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_optional_int(*keys: str) -> int | None:
    """
    按优先级从多个环境变量名中读取第一个有效的整数值。

    核心作用：
      尝试依次读取给定的环境变量名，返回第一个非空且可转为整数的值。

    输入参数：
      *keys : str
        一个或多个环境变量名（完整名，无需加 BTB_ 前缀）。

    返回值：
      int | None
        第一个成功解析的整数值；全部失败或为空时返回 None。

    内部关键执行逻辑：
      按 keys 顺序遍历，使用 int() 转换首个非空值；遇到异常或空值则继续尝试下一 key。

    调用场景：
      TickerCliArgs.port 的默认值计算。
    """
    for key in keys:
        raw = os.environ.get(key)
        if raw not in (None, ""):
            return int(raw)
    return None


def _env_optional_str(*keys: str) -> str | None:
    """
    按优先级从多个环境变量名中读取第一个有效的字符串值。

    核心作用：
      与 _env_optional_int 类似，但返回原始字符串而非整数。

    输入参数：
      *keys : str
        一个或多个环境变量名。

    返回值：
      str | None
        第一个非空的环境变量值；全部不存在时返回 None。

    内部关键执行逻辑：
      按 keys 顺序遍历，返回首个非空字符串值。

    调用场景：
      TickerCliArgs.root_path 的默认值计算。
    """
    for key in keys:
        raw = os.environ.get(key)
        if raw not in (None, ""):
            return raw
    return None


# ---------------------------------------------------------------------------
# CLI 参数数据类
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TickerCliArgs:
    """
    Gradio Web UI 启动参数。

    类设计作用：
      将 UI 服务的启动选项（监听地址、端口、公网分享等）集中管理，
      支持通过命令行参数、环境变量双重覆盖。

    存储属性：
      share      : bool
        是否通过 Gradio 隧道公开访问。
      server_name: str
        服务绑定地址，默认 127.0.0.1。
      port       : int | None
        服务端口，默认由 Gradio 或环境变量决定。
      root_path  : str | None
        反向代理时的外部根路径。

    整体承担业务：
      为 ticker_cmd() 提供启动 Web UI 所需的网络与分享配置。
    """

    share: bool = _env_bool("SHARE", False)
    """
    是否通过 Gradio 隧道将 Web UI 公开到公网。
    默认 False，仅本地访问；设为 True 时生成 gradio.live 分享链接。
    """

    server_name: str = os.environ.get("BTB_SERVER_NAME", "127.0.0.1")
    """
    UI 服务绑定的网络地址。
    默认 127.0.0.1（仅本机访问），在 Docker 等场景可能需要改为 0.0.0.0。
    """

    port: int | None = _env_optional_int("BTB_PORT", "GRADIO_SERVER_PORT")
    """
    UI 服务监听的端口号。
    默认 None，由 Gradio 或 GRADIO_SERVER_PORT 环境变量决定。
    """

    root_path: str | None = _env_optional_str("BTB_ROOT_PATH", "GRADIO_ROOT_PATH")
    """
    通过反向代理或子路径访问 UI 时使用的外部根路径。
    例如 /btb，用于生成正确的静态资源与跳转链接。
    """


# BuyCliArgs 直接复用 BuyConfig，保持类型统一。
BuyCliArgs = BuyConfig
