"""
interface/config.py — 运行时选项与票务配置生成/校验模块。

文件整体功能：
  1. 定义 RuntimeOptions 数据类，承载抢票运行时的全部可调参数（轮询间隔、重试次数、通知 Token、代理等）。
  2. 提供时间字符串与间隔数值的规范化函数。
  3. 提供票务配置的加载、保存、生成与完整性校验能力。
  4. 支持从购票上下文（项目、票档、购票人、地址）一键生成完整配置。

所属模块：接口层 (interface)
依赖文件：
  - interface.common  （REQUIRED_FIELDS、BUYER_REQUIRED_FIELDS、DELIVER_REQUIRED_FIELDS、
                       COOKIE_REQUIRED_FIELDS、_coerce_cookie_store、_load_config、_load_json_file）
  - interface.types   （ValidationResult）

对外能力：
  - RuntimeOptions                    → 运行时选项数据类。
  - build_runtime_options             → 从关键字参数构建 RuntimeOptions。
  - load_ticket_config / save_ticket_config → 配置的加载与保存。
  - generate_ticket_config            → 根据参数生成规范票务配置。
  - build_ticket_config_from_selection → 根据购票上下文选择生成配置。
  - validate_config                   → 校验配置完整性并返回 ValidationResult。
  - normalize_interval / normalize_time_start → 时间与间隔规范化。
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .common import (
    BUYER_REQUIRED_FIELDS,
    COOKIE_REQUIRED_FIELDS,
    DELIVER_REQUIRED_FIELDS,
    REQUIRED_FIELDS,
    _coerce_cookie_store,
    _load_config,
    _load_json_file,
)
from .types import ValidationResult


@dataclass(slots=True)
class RuntimeOptions:
    """
    抢票运行时选项。

    类设计作用：
      集中管理抢票任务执行过程中的全部可调参数，包括轮询间隔、重试策略、通知渠道、
      代理设置、日志级别等；支持通过 build_runtime_options 构建、merged_with 合并覆盖。

    存储属性：
      - interval                       : int — 主循环轮询间隔（毫秒），默认 1000。
      - outer_interval                 : int — 外层循环间隔（毫秒），默认 0。
      - create_retry_limit             : int — 下单重试次数上限，默认 20。
      - create_request_batch_size      : int — 单次批量下单请求数，默认 3。
      - time_start                     : str — 定时启动时间（ISO 格式或 HH:MM[:SS]），默认空字符串表示立即启动。
      - audio_path                     : str — 抢票成功后播放的音频路径。
      - pushplusToken                  : str — PushPlus 通知 Token。
      - serverchanKey                  : str — Server 酱通知 Key。
      - barkToken                      : str — Bark 通知 Token。
      - meowNickname                   : str — Meow 通知昵称。
      - https_proxys                   : str — 代理配置字符串，默认 "none"。
      - proxy_api_url                  : str — 代理 API 地址。
      - proxy_api_protocol             : str — 代理 API 协议，默认 "http"。
      - proxy_api_request_count        : int — 代理 API 请求计数。
      - serverchan3ApiUrl              : str — Server 酱 3 API 地址。
      - ntfy_url                       : str — ntfy 通知 URL。
      - ntfy_username                  : str — ntfy 用户名。
      - ntfy_password                  : str — ntfy 密码。
      - notify_proxy_exhausted         : bool — 代理耗尽时是否通知，默认 False。
      - show_random_message            : bool — 是否展示随机提示信息，默认 True。
      - show_qrcode                    : bool — 是否展示支付二维码，默认 True。
      - use_local_token                : bool — 是否使用本地 Token，默认 False。
      - proxy_max_consecutive_failures : int — 代理连续失败最大次数，默认 2。
      - proxy_cooldown_seconds         : int — 代理冷却时间（秒），默认 180。
      - proxy_backoff_max_seconds      : int — 代理退避最大时间（秒），默认 600。
      - auto_open_payment_url          : bool — 是否自动打开支付链接，默认 True。
      - log_level                      : str — 日志级别，默认 "standard"。
      - log_retention_days             : int — 日志保留天数，默认 7。
    """

    interval: int = 1000
    outer_interval: int = 0
    create_retry_limit: int = 20
    create_request_batch_size: int = 3
    time_start: str = ""
    audio_path: str = ""
    pushplusToken: str = ""
    serverchanKey: str = ""
    barkToken: str = ""
    meowNickname: str = ""
    https_proxys: str = "none"
    proxy_api_url: str = ""
    proxy_api_protocol: str = "http"
    proxy_api_request_count: int = 0
    serverchan3ApiUrl: str = ""
    ntfy_url: str = ""
    ntfy_username: str = ""
    ntfy_password: str = ""
    notify_proxy_exhausted: bool = False
    show_random_message: bool = True
    show_qrcode: bool = True
    use_local_token: bool = False
    proxy_max_consecutive_failures: int = 2
    proxy_cooldown_seconds: int = 180
    proxy_backoff_max_seconds: int = 600
    auto_open_payment_url: bool = True
    log_level: str = "standard"
    log_retention_days: int = 7

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "RuntimeOptions":
        """
        从字典映射构建 RuntimeOptions。

        核心作用：
          委托给 build_runtime_options，对字典中的字段进行规范化后实例化。

        输入参数：
          - data : dict[str, Any] — 包含运行时选项字段的字典。

        返回值：
          RuntimeOptions — 规范化后的运行时选项实例。

        调用位置：
          由 build_runtime_options、merged_with 调用；
          managed_runner.py 中的 main 函数也使用该入口从 runtime.json 还原配置。
        """
        return build_runtime_options(**data)

    def merged_with(
        self,
        overrides: dict[str, Any] | "RuntimeOptions" | None,
    ) -> "RuntimeOptions":
        """
        将当前实例与覆盖项合并，返回新的 RuntimeOptions。

        核心作用：
          1. 若 overrides 为 None，返回当前实例的深拷贝。
          2. 若 overrides 为 RuntimeOptions，直接深拷贝。
          3. 若 overrides 为 dict，先转当前实例为 dict，再更新覆盖项，最后通过 from_mapping 重建。

        输入参数：
          - overrides : dict[str, Any] | RuntimeOptions | None — 覆盖项。

        返回值：
          RuntimeOptions — 合并后的新实例。

        调用位置：
          由 start_buy、run_buy_sync、start_managed_buy 在合并用户传入的运行时选项时调用。
        """
        if overrides is None:
            return copy.deepcopy(self)
        if isinstance(overrides, RuntimeOptions):
            return copy.deepcopy(overrides)
        merged = self.to_dict()
        merged.update(copy.deepcopy(overrides))
        return RuntimeOptions.from_mapping(merged)

    def to_dict(self) -> dict[str, Any]:
        """
        将运行时选项序列化为普通字典。

        核心作用：
          使用 dataclasses.asdict 深拷贝所有字段，便于持久化到 runtime.json 或网络传输。

        输入参数：无。

        返回值：
          dict[str, Any] — 包含全部运行时选项字段的字典。

        调用位置：
          由 merged_with、start_managed_buy（写入 runtime.json）调用。
        """
        return asdict(self)


def normalize_time_start(value: Any) -> str:
    """
    将启动时间字符串规范化为 ISO 格式。

    核心作用：
      1. 空值返回空字符串。
      2. datetime 对象格式化为 "%Y-%m-%dT%H:%M:%S"。
      3. 支持 "%Y-%m-%dT%H:%M:%S"、"%Y-%m-%dT%H:%M"、"%Y-%m-%d %H:%M:%S"、"%Y-%m-%d %H:%M" 格式。
      4. 支持纯时间 "HH:MM[:SS]"；若时间早于当前时间则自动顺延到次日。

    输入参数：
      - value : Any — 原始时间值（str / datetime / None / 空字符串）。

    返回值：
      str — 规范化后的时间字符串；空值返回空字符串。

    异常：
      ValueError — 格式不合法或时间超出范围时抛出。

    调用位置：
      由 build_runtime_options 调用。
    """
    if value in (None, ""):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    text = str(value).strip()
    if not text:
        return ""

    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime(
                "%Y-%m-%dT%H:%M:%S" if "%S" in fmt else "%Y-%m-%dT%H:%M"
            )
        except ValueError:
            continue

    match = re.fullmatch(r"(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?", text)
    if not match:
        raise ValueError(
            "time_start must be ISO-like datetime or HH:MM[:SS], for example 2026-04-12T00:36 or 00:36"
        )

    hour = int(match.group(1))
    minute = int(match.group(2))
    second = int(match.group(3) or 0)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError("time_start clock value is out of range")

    now = datetime.now()
    parsed = now.replace(hour=hour, minute=minute, second=second, microsecond=0)
    if parsed <= now:
        parsed += timedelta(days=1)
    if match.group(3) is None:
        return parsed.strftime("%Y-%m-%dT%H:%M")
    return parsed.strftime("%Y-%m-%dT%H:%M:%S")


def normalize_interval(value: Any) -> int:
    """
    将间隔值规范化为正整数毫秒。

    核心作用：
      1. 空值默认返回 1000。
      2. 支持 int / float 正数。
      3. 支持字符串形式的纯数字或带单位 "ms/s/sec/secs/m/min/mins"。

    输入参数：
      - value : Any — 原始间隔值（int / float / str / None）。

    返回值：
      int — 规范化后的正整数毫秒（至少为 1）。

    异常：
      ValueError — 非正数或格式不支持时抛出。

    调用位置：
      由 build_runtime_options 解析 interval 字段时调用。
    """
    if value in (None, ""):
        return 1000
    if isinstance(value, bool):
        raise ValueError("interval must be a positive duration")
    if isinstance(value, int):
        if value <= 0:
            raise ValueError("interval must be greater than 0")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value <= 0:
            raise ValueError("interval must be greater than 0")
        return int(round(value))

    text = str(value).strip().lower()
    if not text:
        return 1000
    if text.isdigit():
        parsed = int(text)
        if parsed <= 0:
            raise ValueError("interval must be greater than 0")
        return parsed

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec|secs|m|min|mins)?", text)
    if not match:
        raise ValueError(
            "interval must be milliseconds or a duration like 500, 500ms, 0.5s, 0.36m"
        )
    amount = float(match.group(1))
    unit = match.group(2) or "ms"
    if amount <= 0:
        raise ValueError("interval must be greater than 0")
    multiplier = {
        "ms": 1,
        "s": 1000,
        "sec": 1000,
        "secs": 1000,
        "m": 60000,
        "min": 60000,
        "mins": 60000,
    }[unit]
    return max(1, int(round(amount * multiplier)))


def normalize_non_negative_interval(value: Any, *, default: int = 0) -> int:
    """
    将间隔值规范化为非负整数毫秒。

    核心作用：
      与 normalize_interval 类似，但允许 0，用于 outer_interval、proxy_api_request_count 等可禁用项。

    输入参数：
      - value   : Any — 原始间隔值。
      - default : int — 空值时的默认值，默认 0。

    返回值：
      int — 规范化后的非负整数毫秒。

    异常：
      ValueError — 负数或格式不支持时抛出。

    调用位置：
      由 build_runtime_options 解析 outer_interval、proxy_api_request_count 时调用。
    """
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("interval must be a non-negative duration")
    if isinstance(value, int):
        if value < 0:
            raise ValueError("interval must be greater than or equal to 0")
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0:
            raise ValueError("interval must be greater than or equal to 0")
        return int(round(value))

    text = str(value).strip().lower()
    if not text:
        return default
    if text.isdigit():
        return int(text)

    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec|secs|m|min|mins)?", text)
    if not match:
        raise ValueError(
            "interval must be milliseconds or a duration like 0, 500ms, 0.5s, 0.36m"
        )
    amount = float(match.group(1))
    unit = match.group(2) or "ms"
    if amount < 0:
        raise ValueError("interval must be greater than or equal to 0")
    multiplier = {
        "ms": 1,
        "s": 1000,
        "sec": 1000,
        "secs": 1000,
        "m": 60000,
        "min": 60000,
        "mins": 60000,
    }[unit]
    return max(0, int(round(amount * multiplier)))


def normalize_positive_int(value: Any, *, default: int) -> int:
    """
    将值规范化为正整数。

    核心作用：
      用于 create_retry_limit、create_request_batch_size、proxy_max_consecutive_failures 等
      必须为正整数的字段；空值返回 default。

    输入参数：
      - value   : Any — 原始值。
      - default : int — 空值时的默认值。

    返回值：
      int — 规范化后的正整数。

    异常：
      ValueError — 无法转换或值不大于 0 时抛出。

    调用位置：
      由 build_runtime_options 解析各类正整数字段时调用。
    """
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        raise ValueError("value must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError("value must be a positive integer") from None
    if parsed <= 0:
        raise ValueError("value must be greater than 0")
    return parsed


def load_ticket_config(path: str | Path) -> dict[str, Any]:
    """
    从文件路径加载票务配置。

    核心作用：
      委托 _load_config 读取 JSON 文件并深拷贝。

    输入参数：
      - path : str | Path — 配置文件路径。

    返回值：
      dict[str, Any] — 深拷贝后的配置字典。

    调用位置：
      由外部接口或脚本在启动抢票前加载本地配置文件时调用。
    """
    return _load_config(path)


def save_ticket_config(
    config: dict[str, Any],
    path: str | Path,
    *,
    ensure_ascii: bool = False,
    indent: int = 2,
) -> Path:
    """
    将票务配置保存为 JSON 文件。

    核心作用：
      1. 自动创建目标目录。
      2. 以 UTF-8 编码写入 JSON，默认保留非 ASCII 字符并缩进 2 格。

    输入参数：
      - config       : dict[str, Any] — 待保存的配置字典。
      - path         : str | Path — 目标文件路径。
      - ensure_ascii : bool — 是否转义非 ASCII 字符，默认 False。
      - indent       : int — JSON 缩进空格数，默认 2。

    返回值：
      Path — 保存后的文件路径。

    调用位置：
      由外部配置保存入口、UI 配置导出功能调用。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=ensure_ascii, indent=indent)
    return target


def _normalize_buyer_info(value: Any) -> list[dict[str, Any]]:
    """
    将购票人信息规范化为列表。

    核心作用：
      1. 若为 dict，包装为单元素列表并深拷贝。
      2. 若为 list，深拷贝后返回。
      3. 其他类型返回空列表。

    输入参数：
      - value : Any — 原始 buyer_info（dict / list / 其他）。

    返回值：
      list[dict[str, Any]] — 规范化后的购票人列表。

    调用位置：
      由 generate_ticket_config 调用。
    """
    if isinstance(value, dict):
        return [copy.deepcopy(value)]
    if isinstance(value, list):
        return copy.deepcopy(value)
    return []


def generate_ticket_config(
    parameters: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    根据传入参数生成规范的票务配置。

    核心作用：
      1. 将 defaults 与 parameters 合并并深拷贝。
      2. 若未提供 cookies 但提供了 cookies_path，自动从文件加载。
      3. 规范化 buyer_info 为列表。
      4. 根据 unit_price 与 count 自动计算 pay_money（若未提供）。
      5. 填充 username、order_type、is_hot_project、phone 等默认值。
      6. 若 detail 为空，自动生成 "username-project-project_id-screen-screen_id-sku-sku_id" 描述。
      7. 清理空值的 link_id 与 cookies。

    输入参数：
      - parameters : dict[str, Any] — 用户传入的参数，必填。
      - defaults   : dict[str, Any] | None — 默认参数，将优先合并并在后续被 parameters 覆盖。

    返回值：
      dict[str, Any] — 规范化后的完整票务配置字典。

    调用位置：
      由 validate_config、build_ticket_config_from_selection 调用；
      外部也可直接调用以生成可执行配置。
    """
    config: dict[str, Any] = copy.deepcopy(defaults or {})
    config.update(copy.deepcopy(parameters))

    if "cookies" not in config and config.get("cookies_path"):
        config["cookies"] = _coerce_cookie_store(
            _load_json_file(config["cookies_path"])
        )
    elif "cookies" in config:
        config["cookies"] = _coerce_cookie_store(config["cookies"])

    if "buyer_info" in config:
        config["buyer_info"] = _normalize_buyer_info(config["buyer_info"])

    count = config.get("count")
    unit_price = config.pop("unit_price", None)
    if config.get("pay_money") in (None, "") and unit_price is not None and count:
        config["pay_money"] = int(unit_price) * int(count)

    config.setdefault("username", "unknown-user")
    config.setdefault("order_type", 1)
    config.setdefault("is_hot_project", False)
    config.setdefault("phone", "")

    if not config.get("detail"):
        config["detail"] = (
            "{username}-project-{project_id}-screen-{screen_id}-sku-{sku_id}".format(
                username=config.get("username", "unknown-user"),
                project_id=config.get("project_id", "unknown"),
                screen_id=config.get("screen_id", "unknown"),
                sku_id=config.get("sku_id", "unknown"),
            )
        )

    if config.get("link_id") in ("", None):
        config.pop("link_id", None)

    if config.get("cookies") is None:
        config.pop("cookies", None)

    return config


def build_runtime_options(
    *,
    interval: int = 1000,
    outer_interval: int = 0,
    create_retry_limit: int = 20,
    create_request_batch_size: int = 3,
    time_start: str = "",
    audio_path: str = "",
    pushplusToken: str = "",
    serverchanKey: str = "",
    barkToken: str = "",
    meowNickname: str = "",
    https_proxys: str = "none",
    proxy_api_url: str = "",
    proxy_api_protocol: str = "http",
    proxy_api_request_count: int = 0,
    serverchan3ApiUrl: str = "",
    ntfy_url: str = "",
    ntfy_username: str = "",
    ntfy_password: str = "",
    notify_proxy_exhausted: bool = False,
    show_random_message: bool = True,
    show_qrcode: bool = True,
    use_local_token: bool = False,
    proxy_max_consecutive_failures: int = 2,
    proxy_cooldown_seconds: int = 180,
    proxy_backoff_max_seconds: int = 600,
    auto_open_payment_url: bool = True,
    log_level: str = "standard",
    log_retention_days: int = 7,
) -> RuntimeOptions:
    """
    从关键字参数构建并规范化 RuntimeOptions。

    核心作用：
      对 interval、outer_interval、time_start、各类正整数/非负整数字段调用对应规范化函数，
      最终返回一个字段类型与取值范围均合法的 RuntimeOptions 实例。

    输入参数：
      与 RuntimeOptions 字段一一对应（全部关键字参数，均有默认值）。

    返回值：
      RuntimeOptions — 规范化后的运行时选项实例。

    调用位置：
      由 start_buy、run_buy_sync、start_managed_buy 调用；
      RuntimeOptions.from_mapping 也委托给本函数。
    """
    return RuntimeOptions(
        interval=normalize_interval(interval),
        outer_interval=normalize_non_negative_interval(outer_interval, default=0),
        create_retry_limit=normalize_positive_int(
            create_retry_limit,
            default=20,
        ),
        create_request_batch_size=normalize_positive_int(
            create_request_batch_size,
            default=3,
        ),
        time_start=normalize_time_start(time_start),
        audio_path=audio_path,
        pushplusToken=pushplusToken,
        serverchanKey=serverchanKey,
        barkToken=barkToken,
        meowNickname=meowNickname,
        https_proxys=https_proxys,
        proxy_api_url=proxy_api_url,
        proxy_api_protocol=proxy_api_protocol,
        proxy_api_request_count=normalize_non_negative_interval(
            proxy_api_request_count,
            default=0,
        ),
        serverchan3ApiUrl=serverchan3ApiUrl,
        ntfy_url=ntfy_url,
        ntfy_username=ntfy_username,
        ntfy_password=ntfy_password,
        notify_proxy_exhausted=notify_proxy_exhausted,
        show_random_message=show_random_message,
        show_qrcode=show_qrcode,
        use_local_token=use_local_token,
        proxy_max_consecutive_failures=normalize_positive_int(
            proxy_max_consecutive_failures,
            default=2,
        ),
        proxy_cooldown_seconds=normalize_positive_int(
            proxy_cooldown_seconds,
            default=180,
        ),
        proxy_backoff_max_seconds=normalize_positive_int(
            proxy_backoff_max_seconds,
            default=600,
        ),
        auto_open_payment_url=auto_open_payment_url,
        log_level=str(log_level or "standard").lower(),
        log_retention_days=normalize_positive_int(log_retention_days, default=7),
    )


def build_ticket_config_from_selection(
    purchase_context: dict[str, Any],
    selection: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    根据购票上下文与选择索引生成完整票务配置。

    核心作用：
      1. 从 purchase_context 中取出 ticket_options、buyers、addresses。
      2. 校验 ticket_index、buyer_indices、address_index 的合法性。
      3. 校验联系人姓名 buyer 与电话 tel 必填。
      4. 根据所选票档、购票人、地址构造 detail、pay_money、deliver_info 等字段。
      5. 调用 generate_ticket_config 进行最终规范化。

    输入参数：
      - purchase_context : dict[str, Any] — 由 fetch_purchase_context 返回的完整购票上下文。
      - selection        : dict[str, Any] — 用户选择，至少包含 ticket_index、buyer_indices、address_index、buyer、tel。
      - defaults         : dict[str, Any] | None — 默认参数。

    返回值：
      dict[str, Any] — 规范化后的完整票务配置。

    异常：
      ValueError — 任意索引非法或必填字段缺失时抛出。

    调用位置：
      由 UI 选择确认页或自动抢票脚本在拿到 purchase_context 与用户选择后调用。
    """
    ticket_options = purchase_context.get("ticket_options") or []
    buyers = purchase_context.get("buyers") or []
    addresses = purchase_context.get("addresses") or []

    ticket_index = selection.get("ticket_index")
    if not isinstance(ticket_index, int) or not (
        0 <= ticket_index < len(ticket_options)
    ):
        raise ValueError("ticket_index is required and must point to a valid option")

    buyer_indices = selection.get("buyer_indices")
    if not isinstance(buyer_indices, list) or not buyer_indices:
        raise ValueError("buyer_indices must be a non-empty list")
    if any(
        not isinstance(idx, int) or idx < 0 or idx >= len(buyers)
        for idx in buyer_indices
    ):
        raise ValueError("buyer_indices contains an invalid buyer index")

    address_index = selection.get("address_index")
    if not isinstance(address_index, int) or not (0 <= address_index < len(addresses)):
        raise ValueError("address_index is required and must point to a valid address")

    buyer_name = selection.get("buyer")
    buyer_phone = selection.get("tel")
    if not buyer_name:
        raise ValueError("buyer is required")
    if not buyer_phone:
        raise ValueError("tel is required")

    ticket = copy.deepcopy(ticket_options[ticket_index])
    selected_buyers = [copy.deepcopy(buyers[idx]) for idx in buyer_indices]
    address = copy.deepcopy(addresses[address_index])
    buyer_names = "-".join(item.get("name", "") for item in selected_buyers)

    detail = (
        "{username}-{project_name}-{ticket_label}-{buyers}".format(
            username=purchase_context.get("username", "unknown-user"),
            project_name=purchase_context.get("project_name", "unknown-project"),
            ticket_label=ticket.get("display", "unknown-ticket"),
            buyers=buyer_names,
        )
    ).strip("-")

    parameters = {
        "username": purchase_context.get("username", "unknown-user"),
        "detail": detail,
        "count": len(selected_buyers),
        "screen_id": ticket["screen_id"],
        "project_id": ticket.get("project_id", purchase_context.get("project_id")),
        "is_hot_project": ticket.get(
            "is_hot_project",
            purchase_context.get("is_hot_project", False),
        ),
        "sku_id": ticket["id"],
        "order_type": 1,
        "pay_money": int(ticket["price"]) * len(selected_buyers),
        "buyer_info": selected_buyers,
        "buyer": buyer_name,
        "tel": buyer_phone,
        "deliver_info": {
            "name": address.get("name", ""),
            "tel": address.get("phone", ""),
            "addr_id": address.get("id", 0),
            "addr": "{prov}{city}{area}{addr}".format(
                prov=address.get("prov", ""),
                city=address.get("city", ""),
                area=address.get("area", ""),
                addr=address.get("addr", ""),
            ),
        },
        "cookies": purchase_context.get("cookies"),
        "phone": selection.get("phone", purchase_context.get("phone", "")),
    }
    if ticket.get("link_id") not in (None, ""):
        parameters["link_id"] = ticket["link_id"]

    return generate_ticket_config(parameters, defaults=defaults)


def validate_config(config_or_path: str | Path | dict[str, Any]) -> ValidationResult:
    """
    校验票务配置完整性并返回校验结果。

    核心作用：
      1. 通过 _load_config 加载配置并调用 generate_ticket_config 规范化。
      2. 检查 REQUIRED_FIELDS 是否齐全。
      3. 将 count、screen_id、project_id、sku_id、pay_money 强制转换为整数。
      4. 校验 buyer_info、deliver_info、cookies 的结构与必填字段。
      5. 收集 warnings（如 phone 为空）。
      6. 返回 ValidationResult，包含是否通过、错误、警告与规范化配置。

    输入参数：
      - config_or_path : str | Path | dict[str, Any] — 配置对象或配置文件路径。

    返回值：
      ValidationResult — 校验结果。

    调用位置：
      由 start_buy、run_buy_sync、start_managed_buy 在启动抢票前调用；
      也作为对外独立校验接口使用。
    """
    try:
        config = generate_ticket_config(_load_config(config_or_path))
    except Exception as exc:
        return ValidationResult(ok=False, errors=[str(exc)])

    errors: list[str] = []
    warnings: list[str] = []

    for key in REQUIRED_FIELDS:
        if key not in config or config[key] in (None, "", []):
            errors.append("missing required field: {0}".format(key))

    for key in ("count", "screen_id", "project_id", "sku_id", "pay_money"):
        if key in config and config[key] not in (None, ""):
            try:
                config[key] = int(config[key])
            except (TypeError, ValueError):
                errors.append("{0} must be an integer".format(key))

    if isinstance(config.get("count"), int) and config["count"] <= 0:
        errors.append("count must be greater than 0")

    buyer_info = config.get("buyer_info")
    if not isinstance(buyer_info, list) or not buyer_info:
        errors.append("buyer_info must be a non-empty list")
    else:
        for idx, buyer in enumerate(buyer_info):
            if not isinstance(buyer, dict):
                errors.append("buyer_info[{0}] must be an object".format(idx))
                continue
            for field_name in BUYER_REQUIRED_FIELDS:
                if not buyer.get(field_name):
                    errors.append(
                        "buyer_info[{0}] missing field: {1}".format(idx, field_name)
                    )

    deliver_info = config.get("deliver_info")
    if not isinstance(deliver_info, dict):
        errors.append("deliver_info must be an object")
    else:
        for field_name in DELIVER_REQUIRED_FIELDS:
            if deliver_info.get(field_name) in (None, ""):
                errors.append("deliver_info missing field: {0}".format(field_name))

    cookies = config.get("cookies")
    if not isinstance(cookies, list) or not cookies:
        errors.append("cookies must be a non-empty list")
    else:
        for idx, cookie in enumerate(cookies):
            if not isinstance(cookie, dict):
                errors.append("cookies[{0}] must be an object".format(idx))
                continue
            for field_name in COOKIE_REQUIRED_FIELDS:
                if cookie.get(field_name) in (None, ""):
                    errors.append(
                        "cookies[{0}] missing field: {1}".format(idx, field_name)
                    )

    if config.get("phone") in (None, ""):
        warnings.append("phone is empty; this is allowed but some flows may rely on it")

    return ValidationResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        normalized_config=config,
    )
