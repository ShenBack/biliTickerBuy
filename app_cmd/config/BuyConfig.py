"""
app_cmd/config/BuyConfig.py — 抢票运行时配置定义。

文件整体功能：
  定义 BuyConfig 数据类，集中管理抢票流程所需的全部运行参数，
  包括时间控制、请求间隔、代理池、重试策略、通知配置、日志级别等。
  支持从环境变量、运行时字典、配置数据库构建实例，并可将实例导出为 CLI 参数。

所属模块：
  配置层 (app_cmd.config)

依赖文件：
  - app_cmd.config.ConfigBasic  (BasicConfig / config_field / nested_config_field / str_to_bool / normalize_log_level / 默认值常量)
  - app_cmd.config.NotifierConfig (通知渠道嵌套配置)
  - util.Constant              (DEFAULT_RATE_LIMIT_DELAY_MS)

对外能力：
  - BuyConfig 类：承载抢票全量配置。
  - from_runtime_options()：从 Gradio 运行时选项构建配置。
  - from_config_db()：从本地 ConfigDB 构建配置。
  - apply_log_env()：将日志相关配置同步到环境变量。
"""

from dataclasses import dataclass
import os
from typing import Any, ClassVar

from app_cmd.config.ConfigBasic import (
    BasicConfig,
    DEFAULT_CREATE_REQUEST_BATCH_SIZE,
    DEFAULT_CREATE_RETRY_LIMIT,
    config_field,
    nested_config_field,
    normalize_log_level,
    str_to_bool,
)
from app_cmd.config.NotifierConfig import NotifierConfig
from util.Constant import DEFAULT_RATE_LIMIT_DELAY_MS


@dataclass(slots=True)
class BuyConfig(BasicConfig):
    """
    抢票运行时配置类。

    类设计作用：
      集中定义并管理抢票流程所需的全部运行参数，提供统一的配置读取、覆盖与导出能力。

    存储属性：
      包含时间控制（time_start、interval）、票档信息（tickets_info）、代理池（https_proxys、proxy_api_*）、
      重试与限流（create_retry_limit、create_request_batch_size、rate_limit_delay_ms、refresh_interval_*、proxy_*）、
      交互选项（show_random_message、show_qrcode、use_local_token、auto_open_payment_url）、
      日志选项（log_level、log_retention_days）以及嵌套通知配置（notifier_config）。

    整体承担业务：
      1. 作为 buy 子命令的核心配置对象。
      2. 支持 env / runtime / db 三种来源的字段读取。
      3. 支持运行时局部覆盖。
      4. 负责将日志配置同步到环境变量供日志模块读取。
    """

    _skip_cli_fields: ClassVar[set[str]] = {"tickets_info", "config_file"}
    """
    不参与 to_cli_args() 导出的字段集合。
    tickets_info 通常由代码直接传入而非命令行；config_file 用于指定配置文件路径本身。
    """

    tickets_info: str = ""
    """
    票档 JSON 内容字符串。
    由前端或调用方传入，包含项目 ID、票档、观众、联系人等抢票所需信息。
    """

    config_file: str = config_field(
        "",
        cli="--config-file",
    )
    """
    抢票配置文件路径。
    通过 --config-file 命令行参数指定，用于从 JSON 文件加载 tickets_info 等配置。
    """

    time_start: str = config_field(
        "",
        env="BTB_TIME_START",
        runtime="time_start",
        cli="--time-start",
    )
    """
    抢票开始时间字符串。
    格式示例：2026-06-18T20:00:00，用于控制抢票流程的启动时机。
    """

    interval: int | None = config_field(
        1000,
        env="BTB_INTERVAL",
        runtime="interval",
        db="requestInterval",
        cli="--interval",
        cast=int,
    )
    """
    抢票循环默认请求间隔（毫秒）。
    控制每次 create 请求之间的时间间隔，避免触发接口限流。
    """

    notifier_config: NotifierConfig = nested_config_field(NotifierConfig)
    """
    通知渠道嵌套配置。
    包含 ServerChan、PushPlus、Bark、ntfy、飞书等通知方式的相关密钥与开关。
    """

    https_proxys: str = config_field(
        "none",
        env="BTB_HTTPS_PROXYS",
        runtime="https_proxys",
        db="https_proxy",
        cli="--https-proxys",
    )
    """
    HTTPS 代理字符串或代理池。
    可填写单个代理或逗号分隔的多个代理；为 "none" 时表示不使用代理。
    """

    proxy_api_url: str = config_field(
        "",
        env="BTB_PROXY_API_URL",
        runtime="proxy_api_url",
        db="proxyApiUrl",
        cli="--proxy-api-url",
    )
    """
    代理 API 接口地址。
    当代理池耗尽或不足时，通过该 URL 动态获取新的代理节点。
    """

    proxy_api_protocol: str = config_field(
        "http",
        env="BTB_PROXY_API_PROTOCOL",
        runtime="proxy_api_protocol",
        db="proxyApiProtocol",
        cli="--proxy-api-protocol",
    )
    """
    代理 API 请求的协议类型。
    可选 "http" 或 "socks5"，决定代理池节点的协议。
    """

    proxy_api_request_count: int = config_field(
        0,
        env="BTB_PROXY_API_REQUEST_COUNT",
        runtime="proxy_api_request_count",
        db="queueConcurrencyLimit",
        cli="--proxy-api-request-count",
        cast=int,
    )
    """
    每次向代理 API 请求的代理数量。
    0 表示跟随当前代理池大小，不额外扩展。
    """

    # ConfigDB 里原字段是 hideRandomMessage，语义和 show_random_message 相反
    show_random_message: bool = config_field(
        True,
        runtime="show_random_message",
        db="hideRandomMessage",
        db_default=True,
        cast=str_to_bool,
        db_transform=lambda hide: not hide,
        cli_false="--no-show-random-message",
    )
    """
    是否在每轮抢票失败后显示随机提示信息。
    由于 ConfigDB 中存储的是 hideRandomMessage（隐藏随机消息），因此通过 db_transform 取反。
    """

    show_qrcode: bool = config_field(
        True,
        runtime="show_qrcode",
        db="showQrcode",
        db_default=True,
        cast=str_to_bool,
        cli_false="--no-show-qrcode",
    )
    """
    是否在下单成功后展示支付二维码。
    关闭时不会自动弹出二维码窗口。
    """

    use_local_token: bool = config_field(
        False,
        env="BTB_USE_LOCAL_TOKEN",
        runtime="use_local_token",
        db="useLocalToken",
        cast=str_to_bool,
        cli_true="--use-local-token",
    )
    """
    是否使用本地生成的 token。
    在项目流程允许本地 token 时启用，可减少对远程验证接口的依赖。
    """

    create_retry_limit: int = config_field(
        DEFAULT_CREATE_RETRY_LIMIT,
        env="BTB_CREATE_RETRY_LIMIT",
        runtime="create_retry_limit",
        db="createRetryLimit",
        cli="--create-retry-limit",
        cast=int,
    )
    """
    每轮抢票中创建订单的最大重试次数。
    达到此次数后本回合停止重试，等待下一周期。
    """

    create_request_batch_size: int = config_field(
        DEFAULT_CREATE_REQUEST_BATCH_SIZE,
        env="BTB_CREATE_REQUEST_BATCH_SIZE",
        runtime="create_request_batch_size",
        db="createRequestBatchSize",
        cli="--create-request-batch-size",
        cast=int,
    )
    """
    单次批量发送的创建订单请求数量。
    数值越大并发越高，但也更容易触发服务端限流。
    """

    rate_limit_delay_ms: int = config_field(
        DEFAULT_RATE_LIMIT_DELAY_MS,
        env="BTB_RATE_LIMIT_DELAY_MS",
        runtime="rate_limit_delay_ms",
        db="rateLimitDelayMs",
        cli="--rate-limit-delay-ms",
        cast=int,
    )
    """
    收到 HTTP 429 限流响应后的等待时间（毫秒）。
    用于在触发限流时降低请求频率。
    """

    refresh_interval_min_count: int = config_field(
        10,
        env="BTB_REFRESH_INTERVAL_MIN_COUNT",
        runtime="refresh_interval_min_count",
        db="refreshIntervalMinCount",
        cli="--refresh-interval-min-count",
        cast=int,
    )
    """
    循环内主动复检项目详情的最小 create 次数。
    当本周期 create 次数达到该值后，才会触发项目详情刷新。
    """

    refresh_interval_max_count: int = config_field(
        30,
        env="BTB_REFRESH_INTERVAL_MAX_COUNT",
        runtime="refresh_interval_max_count",
        db="refreshIntervalMaxCount",
        cli="--refresh-interval-max-count",
        cast=int,
    )
    """
    循环内主动复检项目详情的最大 create 次数。
    达到该值后强制刷新项目详情，避免缓存数据过期。
    """

    proxy_max_consecutive_failures: int = config_field(
        10,
        env="BTB_PROXY_MAX_CONSECUTIVE_FAILURES",
        runtime="proxy_max_consecutive_failures",
        db="proxyMaxConsecutiveFailures",
        cli="--proxy-max-consecutive-failures",
        cast=int,
    )
    """
    单个代理连续失败次数上限。
    超过此次数后该代理会被暂时移出可用池，进入冷却。
    """

    proxy_cooldown_seconds: int = config_field(
        60,
        env="BTB_PROXY_COOLDOWN_SECONDS",
        runtime="proxy_cooldown_seconds",
        db="proxyCooldownSeconds",
        cli="--proxy-cooldown-seconds",
        cast=int,
    )
    """
    代理进入冷却后的等待时间（秒）。
    冷却结束后代理重新加入可用池。
    """

    proxy_backoff_max_seconds: int = config_field(
        240,
        env="BTB_PROXY_BACKOFF_MAX_SECONDS",
        runtime="proxy_backoff_max_seconds",
        db="proxyBackoffMaxSeconds",
        cli="--proxy-backoff-max-seconds",
        cast=int,
    )
    """
    当整个代理池不可用时最大退避等待时间（秒）。
    避免在代理全部失效时频繁重试。
    """

    # 原来的 from_config_db 里 ConfigDB 缺省时是 True，这里保留这个行为
    auto_open_payment_url: bool = config_field(
        False,
        runtime="auto_open_payment_url",
        db="autoOpenPaymentUrl",
        db_default=True,
        cast=str_to_bool,
        cli_true="--auto-open-payment-url",
    )
    """
    是否在抢票成功后自动打开支付页面。
    数据库字段缺省时按 True 处理，因此设置 db_default=True。
    """

    log_level: str = config_field(
        "standard",
        env="BTB_LOG_LEVEL",
        runtime="log_level",
        db="logLevel",
        cli="--log-level",
        cast=normalize_log_level,
    )
    """
    控制台日志输出预设级别。
    可选 simple（精简）、standard（标准）、debug（调试），不区分大小写。
    """

    log_retention_days: int = config_field(
        7,
        env="BTB_LOG_RETENTION_DAYS",
        runtime="log_retention_days",
        db="logRetentionDays",
        cli="--log-retention-days",
        cast=int,
    )
    """
    日志文件保留天数。
    过期日志将被自动清理。
    """

    @classmethod
    def from_runtime_options(
        cls,
        tickets_info: str,
        runtime_options,
        *,
        show_qrcode: bool | None = None,
    ) -> "BuyConfig":
        """
        从 Gradio 运行时选项构建 BuyConfig 实例。

        核心作用：
          将前端传递的运行时选项字典转换为 BuyConfig 对象，
          并注入票档信息与二维码显示选项。

        输入参数：
          tickets_info : str
            票档 JSON 内容字符串。
          runtime_options : Any
            运行时选项对象，需支持 to_dict() 或可直接转为 dict。
          show_qrcode : bool | None
            是否显示支付二维码；为 None 时保留配置默认值。

        返回值：
          BuyConfig
            构建完成的抢票配置对象。

        内部关键执行逻辑：
          1. 将 runtime_options 转为字典。
          2. 调用 cls.from_mapping(source_name="runtime") 解析字段。
          3. 使用 with_overrides 注入 tickets_info。
          4. 如传入 show_qrcode，则再次覆盖。

        调用场景：
          被 UI 抢票启动流程调用，例如 tab.go 中的 go_start_tab 回调。
        """
        data = (
            runtime_options.to_dict()
            if hasattr(runtime_options, "to_dict")
            else dict(runtime_options)
        )

        config = cls.from_mapping(data, source_name="runtime").with_overrides(
            tickets_info=tickets_info,
        )

        if show_qrcode is not None:
            config = config.with_overrides(show_qrcode=show_qrcode)

        return config

    @classmethod
    def from_config_db(
        cls,
        *,
        tickets_info: str = "",
        time_start: str = "",
        interval: int | None = None,
        https_proxys: str | None = None,
        show_qrcode: bool | None = None,
    ) -> "BuyConfig":
        """
        从本地 ConfigDB 构建 BuyConfig 实例。

        核心作用：
          读取用户通过 Web UI 保存的配置数据库内容，结合调用方传入的
          tickets_info、time_start、interval、https_proxys、show_qrcode 等显式参数生成配置。

        输入参数：
          tickets_info : str
            票档 JSON 内容，默认为空字符串。
          time_start : str
            抢票开始时间，默认为空字符串。
          interval : int | None
            请求间隔（毫秒），为 None 时采用数据库值。
          https_proxys : str | None
            代理池字符串，为 None 时采用数据库值。
          show_qrcode : bool | None
            是否显示支付二维码，为 None 时采用数据库值。

        返回值：
          BuyConfig
            基于配置数据库与显式参数覆盖后的抢票配置对象。

        内部关键执行逻辑：
          1. 导入 util.ConfigDB。
          2. 调用 cls.from_config_getter(ConfigDB.get) 读取数据库。
          3. 构造 overrides 字典，注入显式参数。
          4. 调用 with_overrides 返回新实例。

        调用场景：
          在需要结合 UI 保存配置与代码传入参数时使用，例如 buy 子命令启动前构建配置。
        """
        from util import ConfigDB

        config = cls.from_config_getter(ConfigDB.get)

        overrides: dict[str, Any] = {
            "tickets_info": tickets_info,
            "time_start": time_start,
        }

        if interval is not None:
            overrides["interval"] = interval

        if https_proxys is not None:
            overrides["https_proxys"] = https_proxys

        if show_qrcode is not None:
            overrides["show_qrcode"] = show_qrcode

        return config.with_overrides(**overrides)

    def apply_log_env(self) -> None:
        """
        将日志相关配置同步到环境变量。

        核心作用：
          根据 self.log_level 与 self.log_retention_days 设置 BTB_LOG_LEVEL、
          BTB_CONSOLE_LOG_LEVEL、BTB_LOG_RETENTION_DAYS 环境变量，供日志模块初始化时读取。

        输入参数：无（读取 self 字段）。

        返回值：无。

        内部关键执行逻辑：
          1. 规范化 log_level。
          2. simple 级别将日志与控制台级别均设为 INFO。
          3. debug 级别将两者均设为 DEBUG。
          4. standard 级别将文件日志设为 DEBUG，控制台设为 INFO。
          5. 将 log_retention_days 限制至少为 1 天后写入环境变量。

        调用场景：
          在 buy 子命令启动日志系统前调用。
        """
        normalized_log_level = normalize_log_level(self.log_level)

        if normalized_log_level == "simple":
            os.environ["BTB_LOG_LEVEL"] = "INFO"
            os.environ["BTB_CONSOLE_LOG_LEVEL"] = "INFO"
        elif normalized_log_level == "debug":
            os.environ["BTB_LOG_LEVEL"] = "DEBUG"
            os.environ["BTB_CONSOLE_LOG_LEVEL"] = "DEBUG"
        else:
            os.environ["BTB_LOG_LEVEL"] = "DEBUG"
            os.environ["BTB_CONSOLE_LOG_LEVEL"] = "INFO"

        os.environ["BTB_LOG_RETENTION_DAYS"] = str(max(1, int(self.log_retention_days)))
