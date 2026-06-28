"""
interface/__init__.py — 接口层对外暴露的聚合入口。

文件整体功能：
  将 interface 子模块中的核心类型与函数统一导入并注册到 __all__，
  使外部调用者可以通过 from interface import ... 一次性引入所需能力。

所属模块：接口层 (interface)
依赖文件：
  - interface.auth      （登录状态、二维码登录、Cookie 登录）
  - interface.config    （运行时选项、票务配置生成/加载/校验）
  - interface.execution （抢票任务启动、状态查询、托管运行）
  - interface.project   （项目详情、票档、购票人、收货地址）
  - interface.search    （票务搜索与结果格式化）
  - interface.types     （ValidationResult、BuyTaskRecord 等数据类型）

对外能力：
  - 登录相关：get_login_state、start_qr_login、poll_qr_login、login_with_cookies
  - 配置相关：RuntimeOptions、build_runtime_options、generate_ticket_config、
    load_ticket_config、save_ticket_config、validate_config、build_ticket_config_from_selection
  - 抢票相关：start_buy、run_buy_sync、task_status、start_managed_buy、managed_task_status、
    cancel_managed_buy、delete_managed_buy
  - 项目相关：fetch_project_detail、fetch_ticket_options、fetch_buyers、fetch_addresses、fetch_purchase_context
  - 搜索相关：search_tickets、format_ticket_search_results_text
  - 类型：BuyTaskRecord、ValidationResult
"""

from __future__ import annotations

from .auth import get_login_state, login_with_cookies, poll_qr_login, start_qr_login
from .config import (
    RuntimeOptions,
    build_runtime_options,
    build_ticket_config_from_selection,
    generate_ticket_config,
    load_ticket_config,
    normalize_interval,
    normalize_time_start,
    save_ticket_config,
    validate_config,
)
from .execution import (
    cancel_managed_buy,
    delete_managed_buy,
    managed_task_status,
    run_buy_sync,
    start_buy,
    start_managed_buy,
    task_status,
)
from .project import (
    fetch_addresses,
    fetch_buyers,
    fetch_project_detail,
    fetch_purchase_context,
    fetch_ticket_options,
)
from .search import format_ticket_search_results_text, search_tickets
from .types import BuyTaskRecord, ValidationResult

__all__ = [
    "BuyTaskRecord",
    "RuntimeOptions",
    "ValidationResult",
    "build_runtime_options",
    "build_ticket_config_from_selection",
    "cancel_managed_buy",
    "delete_managed_buy",
    "fetch_addresses",
    "fetch_buyers",
    "fetch_project_detail",
    "fetch_purchase_context",
    "fetch_ticket_options",
    "format_ticket_search_results_text",
    "generate_ticket_config",
    "get_login_state",
    "load_ticket_config",
    "login_with_cookies",
    "managed_task_status",
    "normalize_interval",
    "normalize_time_start",
    "poll_qr_login",
    "run_buy_sync",
    "save_ticket_config",
    "search_tickets",
    "start_qr_login",
    "start_buy",
    "start_managed_buy",
    "task_status",
    "validate_config",
]
