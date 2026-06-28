"""
app_cmd/config/NotifierConfig.py — 通知渠道配置定义。

文件整体功能：
  定义 NotifierConfig 数据类，集中管理抢票成功或代理池耗尽时
  需要触发的各类通知渠道参数，包括 ServerChan、PushPlus、Bark、ntfy、
  MeoW、本地音频以及飞书（Lark）Webhook。

所属模块：
  配置层 (app_cmd.config)

依赖文件：
  - app_cmd.config.ConfigBasic (BasicConfig / config_field / str_to_bool)
  - util                       (ConfigDB，用于 from_config_db)

对外能力：
  - NotifierConfig 类：承载全部通知渠道配置。
  - from_runtime_options(runtime_options)：从运行时选项构建通知配置。
  - from_config_db()：从本地 ConfigDB 构建通知配置。
"""

from dataclasses import dataclass

from app_cmd.config.ConfigBasic import BasicConfig, config_field, str_to_bool


@dataclass(slots=True)
class NotifierConfig(BasicConfig):
    """
    通知渠道配置类。

    类设计作用：
      集中定义所有可用的外部通知方式参数，便于在抢票成功或异常时统一发送通知。

    存储属性：
      serverchan_key / serverchan3_api_url : ServerChan 通知相关密钥与 API 地址。
      pushplus_token : PushPlus 通知 Token。
      bark_token : Bark 推送 Token 或自建路径。
      ntfy_url / ntfy_username / ntfy_password : ntfy 通知主题与认证信息。
      meow_nickname : MeoW 通知昵称。
      audio_path : 本地音频文件路径，抢票成功后播放。
      feishu_webhook : 飞书（Lark）Webhook 地址。
      notify_proxy_exhausted : 是否在代理池全部进入冷却时发送通知。

    整体承担业务：
      1. 作为 BuyConfig 的嵌套子配置存在。
      2. 支持从运行时选项、环境变量、配置数据库读取。
      3. 为通知发送模块提供统一的配置来源。
    """

    serverchan_key: str = config_field(
        "",
        env="BTB_SERVERCHANKEY",
        runtime="serverchanKey",
        db="serverchanKey",
        cli="--notifier-config.serverchan-key",
    )
    """
    ServerChan Turbo 的发送密钥。
    用于在抢票成功或异常时向 ServerChan 发送推送消息。
    """

    serverchan3_api_url: str = config_field(
        "",
        env="BTB_SERVERCHAN3APIURL",
        runtime="serverchan3ApiUrl",
        db="serverchan3ApiUrl",
        cli="--notifier-config.serverchan3-api-url",
    )
    """
    ServerChan3 的 API 端点地址。
    使用 ServerChan3 渠道时必填。
    """

    pushplus_token: str = config_field(
        "",
        env="BTB_PUSHPLUSTOKEN",
        runtime="pushplusToken",
        db="pushplusToken",
        cli="--notifier-config.pushplus-token",
    )
    """
    PushPlus 的 Token。
    用于向 PushPlus 通道发送通知。
    """

    bark_token: str = config_field(
        "",
        env="BTB_BARKTOKEN",
        runtime="barkToken",
        db="barkToken",
        cli="--notifier-config.bark-token",
    )
    """
    Bark 推送 Token 或自建 Bark 推送路径。
    用于向 iOS 设备推送通知。
    """

    ntfy_url: str = config_field(
        "",
        env="BTB_NTFY_URL",
        runtime="ntfy_url",
        db="ntfyUrl",
        cli="--notifier-config.ntfy-url",
    )
    """
    ntfy 主题 URL。
    用于向 ntfy 服务订阅主题发送通知。
    """

    ntfy_username: str = config_field(
        "",
        env="BTB_NTFY_USERNAME",
        runtime="ntfy_username",
        db="ntfyUsername",
        cli="--notifier-config.ntfy-username",
    )
    """
    ntfy 认证用户名。
    当 ntfy 主题需要 HTTP Basic 认证时填写。
    """

    ntfy_password: str = config_field(
        "",
        env="BTB_NTFY_PASSWORD",
        runtime="ntfy_password",
        db="ntfyPassword",
        cli="--notifier-config.ntfy-password",
    )
    """
    ntfy 认证密码。
    与 ntfy_username 配合使用。
    """

    meow_nickname: str = config_field(
        "",
        env="BTB_MEOWNICKNAME",
        runtime="meowNickname",
        db="meowNickname",
        cli="--notifier-config.meow-nickname",
    )
    """
    MeoW 通知使用的昵称。
    用于在 MeoW 渠道展示发送者名称。
    """

    audio_path: str = config_field(
        "",
        env="BTB_AUDIO_PATH",
        runtime="audio_path",
        db="audioPath",
        cli="--notifier-config.audio-path",
    )
    """
    本地音频文件路径。
    抢票成功后将播放该音频文件作为提示音。
    """

    feishu_webhook: str = config_field(
        "",
        env="BTB_FEISHU_WEBHOOK",
        runtime="feishuWebhook",
        db="feishuWebhook",
        cli="--notifier-config.feishu-webhook",
    )
    """
    飞书（Lark）Webhook 地址。
    用于向飞书群机器人发送通知消息。
    """

    notify_proxy_exhausted: bool = config_field(
        False,
        env="BTB_NOTIFY_PROXY_EXHAUSTED",
        runtime="notify_proxy_exhausted",
        db="notifyProxyExhausted",
        cast=str_to_bool,
        cli_true="--notifier-config.notify-proxy-exhausted",
    )
    """
    是否在代理池全部进入冷却时发送通知。
    开启后可在代理全部不可用时及时提醒用户。
    """

    @classmethod
    def from_runtime_options(cls, runtime_options) -> "NotifierConfig":
        """
        从运行时选项构建 NotifierConfig 实例。

        核心作用：
          将前端或其他调用方传递的运行时选项字典转换为通知配置对象。

        输入参数：
          runtime_options : Any
            运行时选项对象，需支持 to_dict() 或可直接转为 dict。

        返回值：
          NotifierConfig
            构建完成的通知配置对象。

        内部关键执行逻辑：
          1. 将 runtime_options 转为字典。
          2. 调用 cls.from_mapping(data, source_name="runtime") 解析字段。

        调用场景：
          被 BuyConfig.from_runtime_options 递归调用以构建嵌套通知配置。
        """
        data = (
            runtime_options.to_dict()
            if hasattr(runtime_options, "to_dict")
            else dict(runtime_options)
        )
        return cls.from_mapping(data, source_name="runtime")

    @classmethod
    def from_config_db(cls) -> "NotifierConfig":
        """
        从本地 ConfigDB 构建 NotifierConfig 实例。

        核心作用：
          读取用户在 Web UI 中保存的通知渠道配置。

        输入参数：无（通过 util.ConfigDB.get 隐式读取）。

        返回值：
          NotifierConfig
            基于配置数据库构建的通知配置对象。

        内部关键执行逻辑：
          1. 导入 util.ConfigDB。
          2. 调用 cls.from_config_getter(ConfigDB.get) 读取数据库字段。

        调用场景：
          被 BuyConfig.from_config_db 递归调用以构建嵌套通知配置。
        """
        from util import ConfigDB

        return cls.from_config_getter(ConfigDB.get)
