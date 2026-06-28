"""
tab/config.py — 全局设置页（代理、音乐、推送、杂项）。

文件整体功能：
  提供 Gradio UI 的“设置”标签页 go_settings_tab()，集中管理抢票相关的全局运行配置：
  1. 代理设置：手动填写代理、测试代理连通性、通过代理 API 自动获取代理、代理策略参数。
  2. 音乐设置：上传抢票成功后的提示音并支持本地测试播放。
  3. 推送设置：配置 Server酱 / PushPlus / Bark / MeoW / ntfy 等推送渠道并测试。
  4. 杂项设置：支付二维码、并发策略、日志清理、使用本地 token、抢票间隔等。

所属模块：
  UI 层 (tab)

依赖文件：
  - app_cmd.config.BuyConfig (BuyConfig 配置对象与默认值)
  - util.ConfigDB (持久化配置数据库)
  - util.Constant (各类默认常量)
  - util.proxy.ProxyTester / ProxyApiProvider (代理测试与 API 获取)
  - util.notifer 各模块 (音频与推送通知能力)

对外能力：
  - go_settings_tab(header_ui) → 返回 (load_go_settings_configs, 受控 UI 组件列表)，
    供 ticker.py 注册“设置”标签页并在页面加载时回填配置。
"""

import re

import gradio as gr
from loguru import logger

from app_cmd.config.BuyConfig import BuyConfig
from util import (
    ConfigDB,
)
from util.Constant import (
    DEFAULT_CREATE_REQUEST_BATCH_SIZE,
    DEFAULT_CREATE_RETRY_LIMIT,
    DEFAULT_LOG_RETENTION_DAYS,
    DEFAULT_MAX_LOG_FILES,
    DEFAULT_MAX_RUN_DIRS,
    DEFAULT_PROXY_BACKOFF_MAX_SECONDS,
    DEFAULT_PROXY_COOLDOWN_SECONDS,
    DEFAULT_PROXY_MAX_CONSECUTIVE_FAILURES,
    DEFAULT_RATE_LIMIT_DELAY_MS,
    DEFAULT_REQUEST_INTERVAL,
)


def go_settings_tab(header_ui):
    """
    构建“设置”标签页。

    核心作用：
      1. 从 ConfigDB 加载代理、音频、推送、杂项等现有配置作为默认值。
      2. 定义大量输入控件变更回调，将用户修改实时写入 ConfigDB。
      3. 提供代理测试、代理 API 拉取、音频测试、推送测试等辅助功能。
      4. 返回 load_go_settings_configs 函数，供外部在页面加载时统一刷新所有控件。

    输入参数：
      header_ui : gradio 组件 — 顶部 Header 组件引用，用于“隐藏顶部大 Header”选项联动隐藏。

    返回值：
      tuple — (load_go_settings_configs 回调函数, 受控 UI 组件列表)。

    调用场景：
      由 ticker.py 注册为全局设置标签页。
    """
    buy_defaults = BuyConfig.from_config_db()
    hide_header_default = ConfigDB.get_as_bool("hideHeader", False)
    proxy_assignment_strategy_default = str(
        ConfigDB.get("proxyAssignmentStrategy") or "balanced"
    ).lower()

    def _split_proxy_lines(proxy_text: str | None) -> list[str]:
        """
        将代理文本按换行或逗号拆分为非空代理地址列表。

        输入参数：
          proxy_text : str | None — 用户填写的代理原始文本。

        返回值：
          list[str] — 去空后的代理地址列表。

        调用场景：
          _serialize_proxy_text / _format_proxy_text / fetch_proxy_from_api 内部使用。
        """
        if not proxy_text:
            return []
        return [
            item.strip()
            for item in re.split(r"[\n,]+", proxy_text)
            if item and item.strip()
        ]

    def _serialize_proxy_text(proxy_text: str | None) -> str:
        """
        将代理文本标准化为逗号分隔的单行字符串。

        输入参数：
          proxy_text : str | None — 原始代理文本。

        返回值：
          str — 逗号分隔的代理字符串，用于写入 ConfigDB。

        调用场景：
          input_https_proxy / test_proxy_connectivity 中统一代理格式。
        """
        return ",".join(_split_proxy_lines(proxy_text))

    def _format_proxy_text(proxy_text: str | None) -> str:
        """
        将代理文本格式化为每行一个代理地址，便于在 UI 中展示。

        输入参数：
          proxy_text : str | None — 原始代理文本。

        返回值：
          str — 换行分隔的代理字符串。

        调用场景：
          get_latest_proxy / input_https_proxy 返回值格式化。
        """
        return "\n".join(_split_proxy_lines(proxy_text))

    def get_latest_proxy():
        """
        获取最新保存的代理配置并以多行形式返回。

        返回值：
          str — 每行一个代理地址的文本；未配置返回空字符串。

        调用场景：
          页面初始化时设置 https_proxy_ui 默认值；load_go_settings_configs 中刷新。
        """
        return _format_proxy_text(ConfigDB.get("https_proxy") or "")

    def get_proxy_api_url():
        """
        获取保存的代理 API URL。

        返回值：
          str — 代理 API 地址；未配置返回空字符串。

        调用场景：
          页面初始化时设置 proxy_api_url_ui 默认值。
        """
        return ConfigDB.get("proxyApiUrl") or ""

    def get_proxy_api_protocol():
        """
        获取保存的代理 API 协议类型（http 或 socks5）。

        返回值：
          str — "socks5" 或 "http"；默认 "http"。

        调用场景：
          页面初始化时设置 proxy_api_protocol_ui 默认值。
        """
        protocol = str(ConfigDB.get("proxyApiProtocol") or "http").lower()
        return "socks5" if protocol in {"socks", "socks5"} else "http"

    def input_https_proxy(_https_proxy):
        """
        保存手动填写的代理配置。

        核心作用：
          将用户输入按换行/逗号拆分后，以逗号连接写入 ConfigDB 的 https_proxy。

        输入参数：
          _https_proxy : str — 代理输入框内容。

        返回值：
          gradio.update — 更新输入框显示为多行格式。

        调用场景：
          save_proxy_btn 按钮点击时触发。
        """
        normalized_proxy = _serialize_proxy_text(_https_proxy)
        ConfigDB.insert("https_proxy", normalized_proxy)
        gr.Info("代理配置已保存。")
        return gr.update(value=_format_proxy_text(normalized_proxy))

    def clear_https_proxy():
        """
        清空已保存的代理配置。

        返回值：
          gradio.update — 将输入框重置为空。

        调用场景：
          clear_proxy_btn 按钮点击时触发。
        """
        ConfigDB.insert("https_proxy", "")
        gr.Info("代理配置已清空。")
        return gr.update(value="")

    def test_proxy_connectivity(proxy_string, timeout):
        """
        测试代理连通性。

        核心作用：
          调用 util.proxy.ProxyTester.test_proxy_connectivity 对给定代理进行可用性测试。
          若用户未填写代理则传入 "none" 测试直连。

        输入参数：
          proxy_string : str — 代理地址文本，支持多行/逗号分隔。
          timeout      : int | float — 测试超时秒数。

        返回值：
          gradio.update — 显示测试结果文本框并填入结果。

        调用场景：
          test_proxy_btn 按钮点击链式触发（先显示加载中，再调用本函数）。
        """
        try:
            from util.proxy.ProxyTester import test_proxy_connectivity

            proxy_string = _serialize_proxy_text(proxy_string)
            if not proxy_string or proxy_string.strip() == "":
                proxy_string = "none"
            result = test_proxy_connectivity(proxy_string, int(timeout))
            return gr.update(value=result, visible=True)
        except Exception as e:
            return gr.update(value=f"❌ 测试过程中发生错误: {str(e)}", visible=True)

    def show_proxy_test_loading():
        """
        显示代理测试中的占位提示。

        返回值：
          gradio.update — 将测试结果文本框设为可见并写入加载提示。

        调用场景：
          test_proxy_btn 点击时首先触发，随后再执行 test_proxy_connectivity。
        """
        return gr.update(value="正在测试代理连通性，请稍候...", visible=True)

    def fetch_proxy_from_api(api_url, protocol):
        """
        从代理 API 获取代理列表并填充到代理配置中。

        核心作用：
          1. 保存 API URL 与协议到 ConfigDB。
          2. 根据队列并发上限（或当前代理数量）决定获取数量。
          3. 调用 fetch_proxy_api 获取代理，写入 https_proxy 并刷新 UI。

        输入参数：
          api_url  : str — 代理 API 地址。
          protocol : str — 代理协议（"http" 或 "socks5"）。

        返回值：
          tuple — (代理输入框 update, API 结果文本框 update)。

        调用场景：
          fetch_proxy_api_btn 按钮点击时触发。
        """
        try:
            from util.proxy.ProxyApiProvider import fetch_proxy_api

            protocol = (
                "socks5" if str(protocol).lower() in {"socks", "socks5"} else "http"
            )
            ConfigDB.insert("proxyApiUrl", str(api_url or "").strip())
            ConfigDB.insert("proxyApiProtocol", protocol)
            count = ConfigDB.get_as_int("queueConcurrencyLimit", 0)
            if count <= 0:
                count = max(
                    1, len(_split_proxy_lines(ConfigDB.get("https_proxy") or ""))
                )
            result = fetch_proxy_api(api_url, count=count, protocol=protocol)
            ConfigDB.insert("https_proxy", ",".join(result.proxies))
            gr.Info(f"已从代理 API 获取 {len(result.proxies)} 个代理。")
            return gr.update(value="\n".join(result.proxies)), gr.update(
                value=f"✅ 已获取 {len(result.proxies)} 个代理",
                visible=True,
            )
        except Exception as e:
            return gr.update(), gr.update(
                value=f"❌ 获取代理失败: {str(e)}", visible=True
            )

    def save_proxy_api_config(api_url, protocol):
        """
        仅保存代理 API 配置而不拉取代理。

        输入参数：
          api_url  : str — 代理 API 地址。
          protocol : str — 代理协议。

        返回值：
          tuple — (URL 输入框 update, 协议下拉框 update)。

        调用场景：
          save_proxy_api_btn 按钮点击时触发。
        """
        protocol = "socks5" if str(protocol).lower() in {"socks", "socks5"} else "http"
        ConfigDB.insert("proxyApiUrl", str(api_url or "").strip())
        ConfigDB.insert("proxyApiProtocol", protocol)
        gr.Info("代理 API 配置已保存。")
        return gr.update(value=ConfigDB.get("proxyApiUrl") or ""), gr.update(
            value=protocol
        )

    def inner_input_serverchan(x):
        """
        保存 Server酱 Turbo 的 SendKey。

        输入参数：
          x : str — SendKey。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          serverchan_ui 提交（回车）时触发。
        """
        ConfigDB.insert("serverchanKey", x)
        return gr.update(value=ConfigDB.get("serverchanKey"))

    def inner_input_serverchan3(x):
        """
        保存 Server酱³ 的 API URL。

        输入参数：
          x : str — API URL。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          serverchan3_ui 提交时触发。
        """
        ConfigDB.insert("serverchan3ApiUrl", x)
        return gr.update(value=ConfigDB.get("serverchan3ApiUrl"))

    def inner_input_pushplus(x):
        """
        保存 PushPlus Token。

        输入参数：
          x : str — PushPlus Token。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          pushplus_ui 提交时触发。
        """
        ConfigDB.insert("pushplusToken", x)
        return gr.update(value=ConfigDB.get("pushplusToken"))

    def inner_input_bark(x):
        """
        保存 Bark Token。

        输入参数：
          x : str — Bark Token 或自托管完整推送地址。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          bark_ui 提交时触发。
        """
        ConfigDB.insert("barkToken", x)
        return gr.update(value=ConfigDB.get("barkToken"))

    def inner_input_meow(x):
        """
        保存 MeoW 昵称。

        输入参数：
          x : str — MeoW 昵称。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          meow_ui 提交时触发。
        """
        ConfigDB.insert("meowNickname", x)
        return gr.update(value=ConfigDB.get("meowNickname"))

    def inner_input_ntfy(x):
        """
        保存 ntfy 服务器 URL。

        输入参数：
          x : str — ntfy URL（含 topic）。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          ntfy_ui 提交时触发。
        """
        ConfigDB.insert("ntfyUrl", x)
        return gr.update(value=ConfigDB.get("ntfyUrl"))

    def inner_input_ntfy_username(x):
        """
        保存 ntfy 认证用户名。

        输入参数：
          x : str — ntfy 用户名。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          ntfy_username_ui 提交时触发。
        """
        ConfigDB.insert("ntfyUsername", x)
        return gr.update(value=ConfigDB.get("ntfyUsername"))

    def inner_input_ntfy_password(x):
        """
        保存 ntfy 认证密码。

        输入参数：
          x : str — ntfy 密码。

        返回值：
          gradio.update — 回显保存后的值。

        调用场景：
          ntfy_password_ui 提交时触发。
        """
        ConfigDB.insert("ntfyPassword", x)
        return gr.update(value=ConfigDB.get("ntfyPassword"))

    def inner_input_audio_path(x):
        """
        保存用户上传的提示音文件路径。

        核心作用：
          将音频文件路径写入 ConfigDB 的 audioPath；若用户清除上传则清空配置。

        输入参数：
          x : str | None — 上传的音频文件路径。

        返回值：
          gradio.update — 回显保存后的路径或 None。

        调用场景：
          audio_path_ui.upload 事件触发。
        """
        if not x:
            ConfigDB.insert("audioPath", "")
            return gr.update(value=None)

        ConfigDB.insert("audioPath", x)
        gr.Info("提示音已保存。")
        return gr.update(value=ConfigDB.get("audioPath"))

    def test_terminal_audio():
        """
        测试终端音频通知播放。

        核心作用：
          读取 ConfigDB 中 audioPath，使用 AudioNotifier 播放测试音并返回结果文本。

        返回值：
          str — 测试成功或失败的提示文本。

        调用场景：
          test_audio_button 按钮点击时触发。
        """
        audio_path = ConfigDB.get("audioPath")
        if not audio_path:
            return "错误: 请先上传提示音"

        try:
            from util.notifer.AudioUtil import AudioNotifier

            AudioNotifier(audio_path).send_message(
                "🎫 抢票测试",
                "这是一条终端版音频测试消息",
            )
            return "✅ 终端音频通知: 测试播放成功"
        except Exception as e:
            logger.exception(e)
            return f"❌ 终端音频通知: 测试播放失败 - {str(e)}"

    def test_all_push():
        """
        测试所有已配置的推送渠道。

        返回值：
          str — 各推送渠道测试结果汇总文本。

        调用场景：
          test_all_push_button 按钮点击时触发。
        """
        try:
            from util.notifer.Notifier import NotifierManager

            return NotifierManager.test_all_notifiers(include_audio=False)
        except Exception as e:
            logger.exception(e)
            return f"错误: 测试过程中发生异常 - {str(e)}"

    def test_ntfy_connection():
        """
        测试 ntfy 服务器连接。

        核心作用：
          读取 ConfigDB 中的 ntfy URL、用户名、密码，调用 NtfyUtil.test_connection 验证。

        返回值：
          str — 连接成功或失败的提示文本。

        调用场景：
          test_ntfy_button 按钮点击时触发。
        """
        url = ConfigDB.get("ntfyUrl")
        username = ConfigDB.get("ntfyUsername")
        password = ConfigDB.get("ntfyPassword")

        if not url:
            return "错误: 请先设置Ntfy服务器URL"

        from util.notifer import NtfyUtil

        success, message = NtfyUtil.test_connection(url, username, password)
        return f"成功: {message}" if success else f"错误: {message}"

    def update_hide_random_message(value):
        """
        保存“关闭群友语录”开关状态。

        输入参数：
          value : bool — 是否关闭随机失败语录。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          show_random_message_ui.change 事件触发。
        """
        ConfigDB.insert("hideRandomMessage", value)
        return gr.update(value=ConfigDB.get("hideRandomMessage"))

    def update_hide_header(value):
        """
        保存“隐藏顶部大 Header”开关状态，并联动控制 header_ui 可见性。

        输入参数：
          value : bool — 是否隐藏顶部 Header。

        返回值：
          tuple — (hide_header_ui update, header_ui visibility update)。

        调用场景：
          hide_header_ui.change 事件触发。
        """
        ConfigDB.insert("hideHeader", value)
        return (
            gr.update(value=ConfigDB.get("hideHeader")),
            gr.update(visible=not value),
        )

    def update_auto_fill_time(value):
        """
        保存“默认自动填写抢票时间”开关状态。

        输入参数：
          value : bool — 是否在上传抢票配置后自动回填起售时间。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          auto_fill_time_ui.change 事件触发。
        """
        ConfigDB.insert("autoFillTime", value)
        return gr.update(value=ConfigDB.get("autoFillTime"))

    def update_notify_proxy_exhausted(value):
        """
        保存“无可用代理时发送提醒”开关状态。

        输入参数：
          value : bool — 是否在所有代理均不可用时推送提醒。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          notify_proxy_exhausted_ui.change 事件触发。
        """
        ConfigDB.insert("notifyProxyExhausted", value)
        return gr.update(value=ConfigDB.get("notifyProxyExhausted"))

    def update_show_qrcode(value):
        """
        保存“抢票成功后显示付款二维码”开关状态。

        输入参数：
          value : bool — 是否显示支付二维码。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          show_qrcode_ui.change 事件触发。
        """
        ConfigDB.insert("showQrcode", value)
        return gr.update(value=ConfigDB.get("showQrcode"))

    def update_auto_open_payment_url(value):
        """
        保存“抢票成功后自动打开支付链接”开关状态。

        输入参数：
          value : bool — 是否自动用系统浏览器打开支付链接。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          auto_open_payment_url_ui.change 事件触发。
        """
        ConfigDB.insert("autoOpenPaymentUrl", value)
        return gr.update(value=ConfigDB.get("autoOpenPaymentUrl"))

    def update_use_local_token(value):
        """
        保存“使用本地 token”开关状态。

        输入参数：
          value : bool — 是否跳过 prepare 接口直接使用本地生成的 token。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          use_local_token_ui.change 事件触发。
        """
        ConfigDB.insert("useLocalToken", value)
        return gr.update(value=ConfigDB.get("useLocalToken"))

    def update_proxy_assignment_strategy(value):
        """
        保存代理分配策略。

        输入参数：
          value : str — "balanced"（均匀分配）或 "queue"（队列模式）。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          proxy_assignment_strategy_ui.change 事件触发。
        """
        ConfigDB.insert("proxyAssignmentStrategy", value)
        return gr.update(value=ConfigDB.get("proxyAssignmentStrategy"))

    def update_log_level(value):
        """
        保存日志级别。

        输入参数：
          value : str — "simple" / "standard" / "debug"。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          log_level_ui.change 事件触发。
        """
        ConfigDB.insert("logLevel", value)
        return gr.update(value=ConfigDB.get("logLevel"))

    def update_auto_cleanup_logs(value):
        """
        保存“启动时自动清理日志”开关状态。

        输入参数：
          value : bool — 是否在启动时自动清理过期日志与运行目录。

        返回值：
          gradio.update — 回显当前保存值。

        调用场景：
          auto_cleanup_logs_ui.change 事件触发。
        """
        ConfigDB.insert("autoCleanupLogs", value)
        return gr.update(value=ConfigDB.get("autoCleanupLogs"))

    def update_request_interval(value):
        """
        保存默认抢票间隔（毫秒）。

        核心作用：
          将输入值转为整数并至少为 1ms，写入 ConfigDB。

        输入参数：
          value : int | str | None — 用户输入的间隔毫秒数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          request_interval_ui 的 blur/submit 事件触发。
        """
        try:
            parsed = max(1, int(value))
        except (TypeError, ValueError):
            parsed = DEFAULT_REQUEST_INTERVAL
        ConfigDB.insert("requestInterval", parsed)
        return gr.update(
            value=ConfigDB.get_as_int("requestInterval", DEFAULT_REQUEST_INTERVAL)
        )

    def update_create_retry_limit(value):
        """
        保存创建订单重试次数下限。

        输入参数：
          value : int | str | None — 重试次数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          create_retry_limit_ui 的 blur/submit 事件触发。
        """
        try:
            parsed = max(1, int(value))
        except (TypeError, ValueError):
            parsed = DEFAULT_CREATE_RETRY_LIMIT
        ConfigDB.insert("createRetryLimit", parsed)
        return gr.update(
            value=ConfigDB.get_as_int("createRetryLimit", DEFAULT_CREATE_RETRY_LIMIT)
        )

    def update_create_request_batch_size(value):
        """
        保存每一次准备订单后尝试抢票次数。

        输入参数：
          value : int | str | None — 批次大小。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          create_request_batch_size_ui 的 blur/submit 事件触发。
        """
        try:
            parsed = max(1, int(value))
        except (TypeError, ValueError):
            parsed = DEFAULT_CREATE_REQUEST_BATCH_SIZE
        ConfigDB.insert("createRequestBatchSize", parsed)
        return gr.update(
            value=ConfigDB.get_as_int(
                "createRequestBatchSize",
                DEFAULT_CREATE_REQUEST_BATCH_SIZE,
            )
        )

    def update_rate_limit_delay_ms(value):
        """
        保存 429 后延迟时间（毫秒）。

        输入参数：
          value : int | str | None — 延迟毫秒数，允许为 0。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          rate_limit_delay_ms_ui 的 blur/submit 事件触发。
        """
        try:
            parsed = max(0, int(value))
        except (TypeError, ValueError):
            parsed = DEFAULT_RATE_LIMIT_DELAY_MS
        ConfigDB.insert("rateLimitDelayMs", parsed)
        return gr.update(
            value=ConfigDB.get_as_int("rateLimitDelayMs", DEFAULT_RATE_LIMIT_DELAY_MS)
        )

    def _update_positive_int_config(key: str, value, default: int):
        """
        通用正整数配置更新函数。

        核心作用：
          将 value 转为整数并至少为 1，写入指定 key；解析失败使用 default。

        输入参数：
          key     : str — ConfigDB 中的配置键名。
          value   : Any — 用户输入值。
          default : int — 解析失败时的默认值。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          update_proxy_max_consecutive_failures / update_proxy_cooldown_seconds /
          update_proxy_backoff_max_seconds / update_log_retention_days /
          update_max_log_files / update_max_run_dirs 内部复用。
        """
        try:
            parsed = max(1, int(value))
        except (TypeError, ValueError):
            parsed = default
        ConfigDB.insert(key, parsed)
        return gr.update(value=ConfigDB.get_as_int(key, default))

    def update_proxy_max_consecutive_failures(value):
        """
        保存单代理最大连续失败次数。

        输入参数：
          value : int | str | None — 失败次数阈值。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          proxy_max_consecutive_failures_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "proxyMaxConsecutiveFailures",
            value,
            DEFAULT_PROXY_MAX_CONSECUTIVE_FAILURES,
        )

    def update_proxy_cooldown_seconds(value):
        """
        保存代理冷却时间（秒）。

        输入参数：
          value : int | str | None — 冷却秒数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          proxy_cooldown_seconds_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "proxyCooldownSeconds",
            value,
            DEFAULT_PROXY_COOLDOWN_SECONDS,
        )

    def update_proxy_backoff_max_seconds(value):
        """
        保存风控后休眠上限（秒）。

        输入参数：
          value : int | str | None — 最大退避秒数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          proxy_backoff_max_seconds_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "proxyBackoffMaxSeconds",
            value,
            DEFAULT_PROXY_BACKOFF_MAX_SECONDS,
        )

    def update_queue_concurrency_limit(value):
        """
        保存队列并发上限。

        核心作用：
          队列模式下允许同时运行的 worker 数量；填 0 表示等于代理数量。

        输入参数：
          value : int | str | None — 并发上限。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          queue_concurrency_limit_ui 的 blur/submit 事件触发。
        """
        try:
            parsed = max(0, int(value))
        except (TypeError, ValueError):
            parsed = 0
        ConfigDB.insert("queueConcurrencyLimit", parsed)
        return gr.update(value=ConfigDB.get_as_int("queueConcurrencyLimit", 0))

    def update_log_retention_days(value):
        """
        保存日志保留天数。

        输入参数：
          value : int | str | None — 保留天数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          log_retention_days_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "logRetentionDays",
            value,
            DEFAULT_LOG_RETENTION_DAYS,
        )

    def update_max_log_files(value):
        """
        保存最多保留日志文件数。

        输入参数：
          value : int | str | None — 最大日志文件数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          max_log_files_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "maxLogFiles",
            value,
            DEFAULT_MAX_LOG_FILES,
        )

    def update_max_run_dirs(value):
        """
        保存最多保留运行目录数。

        输入参数：
          value : int | str | None — 最大运行目录数。

        返回值：
          gradio.update — 回显保存后的整数值。

        调用场景：
          max_run_dirs_ui 的 blur/submit 事件触发。
        """
        return _update_positive_int_config(
            "maxRunDirs",
            value,
            DEFAULT_MAX_RUN_DIRS,
        )

    def _bind_number_commit(component, fn):
        """
        为数字输入框同时绑定失去焦点（blur）与回车（submit）提交事件。

        输入参数：
          component : gradio.Number — 数字输入框组件。
          fn        : Callable — 要绑定的回调函数。

        返回值：
          无。

        调用场景：
          构建杂项/代理策略中的数字配置项时统一注册事件。
        """
        component.blur(
            fn=fn,
            inputs=component,
            outputs=component,
        )
        component.submit(
            fn=fn,
            inputs=component,
            outputs=component,
        )

    with gr.Column(elem_classes="btb-page-section"):
        with gr.Tabs(elem_classes="btb-top-tabs"):
            with gr.Tab("代理"):
                with gr.Column(elem_classes="btb-card btb-layout-card"):
                    gr.Markdown("### 填写你的代理服务器")
                    https_proxy_ui = gr.Textbox(
                        label="代理服务器地址",
                        lines=4,
                        placeholder="每行填写一个代理地址，留空表示只使用直连\n例如：\nhttp://127.0.0.1:8080\nsocks5://127.0.0.1:1080\nhttp://proxyuser:proxypass@xx.xx.xx.xx:8080",
                        value=get_latest_proxy(),
                    )
                    with gr.Row(elem_classes="btb-inline-actions !justify-end"):
                        save_proxy_btn = gr.Button(
                            "保存代理配置",
                            elem_classes="btb-soft-button",
                        )
                        clear_proxy_btn = gr.Button(
                            "清空代理配置",
                            elem_classes="btb-soft-button",
                        )
                        test_proxy_btn = gr.Button(
                            "🔍 测试代理连通性",
                            elem_classes="btb-soft-button",
                        )
                    test_timeout_ui = gr.Number(
                        label="测试代理超时时间(秒)",
                        value=10,
                        minimum=5,
                        maximum=60,
                        step=1,
                    )
                    test_result_ui = gr.Textbox(
                        label="测试结果",
                        lines=10,
                        max_lines=15,
                        interactive=False,
                        placeholder="点击上方按钮开始测试代理连通性...",
                        visible=False,
                    )
                    gr.Markdown("### 通过代理 API 获取")
                    proxy_api_url_ui = gr.Textbox(
                        label="代理 API 地址",
                        placeholder="例如：http://api.youdaili.com/v1/proxy/get?app_key=...&app_secret=...&count=&format=&protocol=",
                        value=get_proxy_api_url(),
                    )
                    proxy_api_protocol_ui = gr.Dropdown(
                        label="代理地址类型",
                        choices=[
                            ("HTTP / HTTPS", "http"),
                            ("SOCKS5", "socks5"),
                        ],
                        value=get_proxy_api_protocol(),
                        interactive=True,
                        allow_custom_value=False,
                        filterable=False,
                    )
                    with gr.Row(elem_classes="btb-inline-actions !justify-end"):
                        save_proxy_api_btn = gr.Button(
                            "保存 API 配置",
                            elem_classes="btb-soft-button",
                        )
                        fetch_proxy_api_btn = gr.Button(
                            "获取并填入代理",
                            elem_classes="btb-soft-button",
                        )
                    proxy_api_result_ui = gr.Textbox(
                        label="代理 API 结果",
                        interactive=False,
                        visible=False,
                    )
                    gr.Markdown(
                        """
                        <div class="mt-3 text-sm leading-7 text-slate-700">
                          <p><strong>怎么填写：</strong>推荐每行填写一个代理地址，也支持逗号分隔。留空表示只使用直连。</p>
                          <p><strong>支持格式：</strong><code>http://IP:端口</code>、<code>https://IP:端口</code>、<code>socks5://IP:端口</code>。</p>
                          <p><strong>带账号密码的 HTTP 代理示例：</strong><code>http://proxyuser:proxypass@xx.xx.xx.xx:8080</code></p>
                          <p><strong>程序什么时候会用代理：</strong>当抢票流程检测到风控时，会按你填写的顺序切换到下一个代理；当前请求不会在请求层立刻自动重试，下一次抢票重试才会使用新代理。</p>
                          <p><strong>代理失效怎么处理：</strong>同一代理在短时间内连续失败会被暂时冷却；如果所有代理都不可用，程序会按递增时间休息后再试。</p>
                          <p><strong>代理 API：</strong>保存 API 地址后，程序会在代理全部不可用时自动按并发数请求新代理；请求会自动带上 <code>format=json</code>、<code>count</code> 和所选 <code>protocol</code>。</p>
                          <p><strong>建议先测试再开抢：</strong>保存后点击上方“测试代理连通性”，确认代理能正常访问哔哩哔哩接口。</p>
                          <p><strong>自建代理：</strong>如果你没有现成代理，可以自己在 Ubuntu / Debian 服务器上搭建 Squid HTTP 代理。</p>
                          <p><strong>完整搭建说明：</strong><a href="https://github.com/mikumifa/biliTickerBuy/blob/main/docs/proxy-self-hosting.md" target="_blank" rel="noopener noreferrer">GitHub 查看自建代理指南</a></p>
                        </div>
                        """
                    )
                    gr.Markdown("## 代理策略")
                    proxy_max_consecutive_failures_ui = gr.Number(
                        label="单代理最大连续失败次数",
                        value=buy_defaults.proxy_max_consecutive_failures,
                        minimum=1,
                        step=1,
                        info="同一代理在短时间内连续失败多少次后进入冷却。",
                    )
                    proxy_cooldown_seconds_ui = gr.Number(
                        label="代理冷却时间（秒）",
                        value=buy_defaults.proxy_cooldown_seconds,
                        minimum=1,
                        step=1,
                        info="代理进入冷却后，多久恢复可用。",
                    )
                    proxy_backoff_max_seconds_ui = gr.Number(
                        label="风控后休眠上限（秒）",
                        value=buy_defaults.proxy_backoff_max_seconds,
                        minimum=1,
                        step=1,
                        info="当所有代理都暂时不可用时，程序退避休眠的最大时长。",
                    )
                    notify_proxy_exhausted_ui = gr.Checkbox(
                        label="无可用代理时发送提醒",
                        value=buy_defaults.notifier_config.notify_proxy_exhausted,
                        info="默认关闭。开启后，当所有代理都进入冷却且程序需要休息时，会通过已配置的推送渠道提醒你补充代理。",
                    )

            with gr.Tab("音乐"):
                with gr.Column(elem_classes="btb-card btb-layout-card"):
                    gr.Markdown("### 配置抢票成功后播放音乐")
                    gr.Markdown(
                        "推荐上传 WAV。若上传 MP3、FLAC、M4A、OGG 等格式，请先在系统中安装 "
                        "`ffmpeg/ffprobe`；如果安装时报错，也可以先前往 "
                        "https://cloudconvert.com/wav-converter 转成 WAV 后再上传。"
                    )
                    audio_path_ui = gr.Audio(
                        label="上传提示声音",
                        type="filepath",
                        loop=True,
                        value=ConfigDB.get("audioPath") or None,
                    )
                    test_audio_button = gr.Button(
                        "测试终端播放",
                        elem_classes="btb-soft-button",
                    )
                    test_audio_result = gr.Textbox(
                        label="音乐测试结果",
                        interactive=False,
                    )

            with gr.Tab("推送"):
                with gr.Column(elem_classes="btb-card btb-layout-card"):
                    gr.Markdown("### 配置抢票推送消息")
                    gr.Markdown(
                        """
                        🗨️ **抢票成功提醒**

                        > 你需要去对应的网站获取 key 或 token，然后填入下面的输入框  
                        > [Server酱<sup>Turbo</sup>](https://sct.ftqq.com/sendkey) | [pushplus](https://www.pushplus.plus/uc.html) | [Server酱<sup>3</sup>](https://sc3.ft07.com/sendkey) | [ntfy](https://ntfy.sh/) | [Bark](https://bark.day.app/) | MeoW  
                        > 留空以不启用提醒功能

                        ### 🔍 推送服务对比

                        | 服务     | 优点                               | 缺点                            |
                        |----------|------------------------------------|---------------------------------|
                        | Server酱<sup>Turbo</sup> | 简单易用，微信推送              | 微信推送很难看到 |
                        | pushplus | 简单易用，微信推送| 微信推送很难看到               |
                        | Server酱<sup>3</sup> | APP推送，有中文文档              | 配置复杂 |
                        | ntfy     | APP推送, 功能强大, 支持长期响铃 | 配置复杂，需要手动搭建或注册公网地址 |
                        | Bark     | iOS通知推送，配置简单，无视静音和勿扰模式，支持APP跳转 | 仅支持iOS设备 |
                        | MeoW     | HMS系统级通知推送，配置简单，无需后台常驻 | 仅支持鸿蒙设备 |

                        ✅ 推荐：初次使用建议选择 **pushplus** 或 **Server酱ᵀᵘʳᵇᵒ**，配置最简单  
                        🍎 iOS用户推荐使用 **Bark**，通知效果最佳  
                        ⭕ 鸿蒙用户推荐使用 **MeoW**，HMS系统级推送  
                        🛠️ 追求高度自由/有自建服务器/需要在抢票成功时通过手机播放铃声时，建议用 **ntfy** 或 **Server酱³**
                        """
                    )
                    gr.Markdown("#### Server酱")
                    serverchan_ui = gr.Textbox(
                        value=ConfigDB.get("serverchanKey") or "",
                        label="Server酱ᵀᵘʳᵇᵒ的SendKey｜输入完成后，回车键保存",
                        interactive=True,
                        info="https://sct.ftqq.com/",
                    )
                    serverchan3_ui = gr.Textbox(
                        value=ConfigDB.get("serverchan3ApiUrl") or "",
                        label="Server酱³的API URL｜输入完成后，回车键保存",
                        interactive=True,
                        info="https://sc3.ft07.com/",
                    )
                    gr.Markdown("#### PushPlus")
                    pushplus_ui = gr.Textbox(
                        value=ConfigDB.get("pushplusToken") or "",
                        label="PushPlus的Token｜输入完成后，回车键保存",
                        interactive=True,
                        info="https://www.pushplus.plus/",
                    )
                    gr.Markdown("#### Bark")
                    bark_ui = gr.Textbox(
                        value=ConfigDB.get("barkToken") or "",
                        label="Bark的Token｜输入完成后，回车键保存",
                        interactive=True,
                        info='iOS Bark App的"服务器"页面获取，例如: jmGYK*****(并非Device Token)；自托管服务请输入完整推送地址，例如: https://bark.example.app/jmGYK*****',
                    )
                    gr.Markdown("#### Meow")
                    meow_ui = gr.Textbox(
                        value=ConfigDB.get("meowNickname") or "",
                        label="MeoW昵称｜输入完成后，回车键保存",
                        interactive=True,
                        info="https://www.chuckfang.com/MeoW/api_doc.html",
                    )
                    gr.Markdown("#### Ntfy")
                    ntfy_ui = gr.Textbox(
                        value=ConfigDB.get("ntfyUrl") or "",
                        label="Ntfy服务器URL｜输入完成后，回车键保存",
                        interactive=True,
                        info="例如: https://ntfy.sh/your-topic",
                    )
                    with gr.Row(elem_classes="btb-inline-actions !justify-end"):
                        ntfy_username_ui = gr.Textbox(
                            value=ConfigDB.get("ntfyUsername") or "",
                            label="Ntfy用户名",
                            interactive=True,
                            info="如果你的Ntfy服务器需要认证",
                        )
                        ntfy_password_ui = gr.Textbox(
                            value=ConfigDB.get("ntfyPassword") or "",
                            label="Ntfy密码",
                            interactive=True,
                            type="password",
                        )
                    test_ntfy_button = gr.Button(
                        "测试Ntfy连接",
                        elem_classes="btb-soft-button",
                    )
                    test_ntfy_result = gr.Textbox(
                        label="测试结果",
                        interactive=False,
                    )
                    gr.Markdown("#### 测试")
                    test_all_push_button = gr.Button(
                        "🧪 测试所有推送",
                        elem_classes="!rounded-xl !border !border-slate-300 !bg-white !text-slate-900 !shadow-sm hover:!bg-slate-100 !transition",
                    )
                    test_push_result = gr.Textbox(
                        label="推送测试结果",
                        interactive=False,
                    )

            with gr.Tab("杂项"):
                with gr.Column(elem_classes="btb-card btb-layout-card"):
                    gr.Markdown("### 杂项配置")
                    gr.Markdown("## 支付")
                    show_qrcode_ui = gr.Checkbox(
                        label="抢票成功后显示付款二维码",
                        value=buy_defaults.show_qrcode,
                        info="默认开启。关闭后，抢票成功时不再弹出付款二维码。",
                    )
                    auto_open_payment_url_ui = gr.Checkbox(
                        label="抢票成功后自动打开支付链接",
                        value=buy_defaults.auto_open_payment_url,
                        info="默认关闭。开启后，成功获取支付链接时会尝试用系统默认浏览器打开。",
                    )
                    gr.Markdown("## 并发")
                    proxy_assignment_strategy_ui = gr.Dropdown(
                        label="任务代理分配策略",
                        choices=[
                            ("均匀分配", "balanced"),
                            ("队列模式", "queue"),
                        ],
                        value=proxy_assignment_strategy_default,
                        interactive=True,
                        allow_custom_value=False,
                        filterable=False,
                    )
                    queue_concurrency_limit_ui = gr.Number(
                        label="队列并发上限（仅队列模式）",
                        value=ConfigDB.get_as_int("queueConcurrencyLimit", 0),
                        minimum=0,
                        step=1,
                        info="填 0 表示等于代理数量。",
                    )
                    gr.Markdown("## 日志")
                    log_level_ui = gr.Dropdown(
                        label="日志级别",
                        choices=[
                            ("简洁", "simple"),
                            ("标准", "standard"),
                            ("调试", "debug"),
                        ],
                        value=buy_defaults.log_level,
                        interactive=True,
                        allow_custom_value=False,
                        filterable=False,
                    )
                    auto_cleanup_logs_ui = gr.Checkbox(
                        label="启动时自动清理日志",
                        value=ConfigDB.get_as_bool("autoCleanupLogs", True),
                        info="默认开启。会清理 btb_logs 和 btb_runs 中过旧或过多的内容。",
                    )
                    log_retention_days_ui = gr.Number(
                        label="日志保留天数",
                        value=ConfigDB.get_as_int(
                            "logRetentionDays", DEFAULT_LOG_RETENTION_DAYS
                        ),
                        minimum=1,
                        step=1,
                    )
                    max_log_files_ui = gr.Number(
                        label="最多保留日志文件数",
                        value=ConfigDB.get_as_int("maxLogFiles", DEFAULT_MAX_LOG_FILES),
                        minimum=1,
                        step=1,
                    )
                    max_run_dirs_ui = gr.Number(
                        label="最多保留运行目录数",
                        value=ConfigDB.get_as_int("maxRunDirs", DEFAULT_MAX_RUN_DIRS),
                        minimum=1,
                        step=1,
                    )
                    gr.Markdown("## 其他")
                    auto_fill_time_ui = gr.Checkbox(
                        label="默认自动填写抢票时间",
                        value=ConfigDB.get_as_bool("autoFillTime", True),
                        info="开启后，上传抢票配置文件时会自动按票档起售时间回填抢票时间。",
                    )
                    show_random_message_ui = gr.Checkbox(
                        label="关闭群友语录",
                        value=not buy_defaults.show_random_message,
                        info="关闭后，抢票失败时将不再显示有趣的语录",
                    )
                    hide_header_ui = gr.Checkbox(
                        label="隐藏顶部大 Header",
                        value=hide_header_default,
                        info="默认显示。开启后将隐藏顶部包含项目地址和图标的区域。",
                    )
                    use_local_token_ui = gr.Checkbox(
                        label="使用本地 token",
                        value=buy_defaults.use_local_token,
                        info="默认关闭。开启后跳过 prepare 接口，直接使用本地生成的 token。",
                    )
                    request_interval_ui = gr.Number(
                        label="默认抢票间隔（毫秒）",
                        value=int(buy_defaults.interval or DEFAULT_REQUEST_INTERVAL),
                        minimum=1,
                        step=1,
                        info="作为抢票请求的默认间隔配置。",
                    )
                    create_retry_limit_ui = gr.Number(
                        label="创建订单重试次数",
                        value=buy_defaults.create_retry_limit,
                        minimum=1,
                        step=1,
                    )
                    create_request_batch_size_ui = gr.Number(
                        label="每一次准备订单后尝试抢票次数",
                        value=buy_defaults.create_request_batch_size,
                        minimum=1,
                        step=1,
                    )
                    rate_limit_delay_ms_ui = gr.Number(
                        label="429后延迟时间（毫秒）",
                        value=buy_defaults.rate_limit_delay_ms,
                        minimum=0,
                        step=1,
                        info="请求返回 HTTP 429 后，等待多久再继续后续流程。默认 100ms。",
                    )

    # 代理相关事件绑定
    save_proxy_btn.click(
        fn=input_https_proxy, inputs=https_proxy_ui, outputs=https_proxy_ui
    )
    clear_proxy_btn.click(fn=clear_https_proxy, outputs=https_proxy_ui)
    test_proxy_btn.click(
        fn=show_proxy_test_loading,
        outputs=test_result_ui,
    ).then(
        fn=test_proxy_connectivity,
        inputs=[https_proxy_ui, test_timeout_ui],
        outputs=test_result_ui,
    )
    save_proxy_api_btn.click(
        fn=save_proxy_api_config,
        inputs=[proxy_api_url_ui, proxy_api_protocol_ui],
        outputs=[proxy_api_url_ui, proxy_api_protocol_ui],
    )
    fetch_proxy_api_btn.click(
        fn=fetch_proxy_from_api,
        inputs=[proxy_api_url_ui, proxy_api_protocol_ui],
        outputs=[https_proxy_ui, proxy_api_result_ui],
    )

    # 推送渠道事件绑定
    serverchan_ui.submit(
        fn=inner_input_serverchan, inputs=serverchan_ui, outputs=serverchan_ui
    )
    serverchan3_ui.submit(
        fn=inner_input_serverchan3,
        inputs=serverchan3_ui,
        outputs=serverchan3_ui,
    )
    pushplus_ui.submit(fn=inner_input_pushplus, inputs=pushplus_ui, outputs=pushplus_ui)
    bark_ui.submit(fn=inner_input_bark, inputs=bark_ui, outputs=bark_ui)
    meow_ui.submit(fn=inner_input_meow, inputs=meow_ui, outputs=meow_ui)
    ntfy_ui.submit(fn=inner_input_ntfy, inputs=ntfy_ui, outputs=ntfy_ui)
    ntfy_username_ui.submit(
        fn=inner_input_ntfy_username,
        inputs=ntfy_username_ui,
        outputs=ntfy_username_ui,
    )
    ntfy_password_ui.submit(
        fn=inner_input_ntfy_password,
        inputs=ntfy_password_ui,
        outputs=ntfy_password_ui,
    )
    audio_path_ui.upload(
        fn=inner_input_audio_path,
        inputs=audio_path_ui,
        outputs=audio_path_ui,
    )

    # 开关/下拉框事件绑定
    show_random_message_ui.change(
        fn=update_hide_random_message,
        inputs=show_random_message_ui,
        outputs=show_random_message_ui,
    )
    hide_header_ui.change(
        fn=update_hide_header,
        inputs=hide_header_ui,
        outputs=[hide_header_ui, header_ui],
    )
    auto_fill_time_ui.change(
        fn=update_auto_fill_time,
        inputs=auto_fill_time_ui,
        outputs=auto_fill_time_ui,
    )
    notify_proxy_exhausted_ui.change(
        fn=update_notify_proxy_exhausted,
        inputs=notify_proxy_exhausted_ui,
        outputs=notify_proxy_exhausted_ui,
    )
    _bind_number_commit(
        proxy_max_consecutive_failures_ui,
        update_proxy_max_consecutive_failures,
    )
    _bind_number_commit(
        proxy_cooldown_seconds_ui,
        update_proxy_cooldown_seconds,
    )
    _bind_number_commit(
        proxy_backoff_max_seconds_ui,
        update_proxy_backoff_max_seconds,
    )
    show_qrcode_ui.change(
        fn=update_show_qrcode,
        inputs=show_qrcode_ui,
        outputs=show_qrcode_ui,
    )
    auto_open_payment_url_ui.change(
        fn=update_auto_open_payment_url,
        inputs=auto_open_payment_url_ui,
        outputs=auto_open_payment_url_ui,
    )
    proxy_assignment_strategy_ui.change(
        fn=update_proxy_assignment_strategy,
        inputs=proxy_assignment_strategy_ui,
        outputs=proxy_assignment_strategy_ui,
    )
    _bind_number_commit(
        queue_concurrency_limit_ui,
        update_queue_concurrency_limit,
    )
    log_level_ui.change(
        fn=update_log_level,
        inputs=log_level_ui,
        outputs=log_level_ui,
    )
    auto_cleanup_logs_ui.change(
        fn=update_auto_cleanup_logs,
        inputs=auto_cleanup_logs_ui,
        outputs=auto_cleanup_logs_ui,
    )
    _bind_number_commit(
        log_retention_days_ui,
        update_log_retention_days,
    )
    _bind_number_commit(
        max_log_files_ui,
        update_max_log_files,
    )
    _bind_number_commit(
        max_run_dirs_ui,
        update_max_run_dirs,
    )
    use_local_token_ui.change(
        fn=update_use_local_token,
        inputs=use_local_token_ui,
        outputs=use_local_token_ui,
    )
    _bind_number_commit(
        request_interval_ui,
        update_request_interval,
    )
    _bind_number_commit(
        create_retry_limit_ui,
        update_create_retry_limit,
    )
    _bind_number_commit(
        create_request_batch_size_ui,
        update_create_request_batch_size,
    )
    _bind_number_commit(
        rate_limit_delay_ms_ui,
        update_rate_limit_delay_ms,
    )
    test_audio_button.click(
        fn=test_terminal_audio,
        inputs=[],
        outputs=test_audio_result,
    )
    test_ntfy_button.click(
        fn=test_ntfy_connection,
        inputs=[],
        outputs=test_ntfy_result,
    )
    test_all_push_button.click(
        fn=test_all_push,
        inputs=[],
        outputs=test_push_result,
    )

    def load_go_settings_configs():
        """
        加载设置页所有配置并返回 Gradio update 列表。

        核心作用：
          在页面初始化或外部调用时，统一刷新代理、音频、推送、杂项等全部控件的值。

        返回值：
          list — 与返回的受控 UI 组件列表顺序一一对应的 gr.update() 对象列表。

        调用场景：
          ticker.py 在页面加载时调用，用于回填用户上一次保存的配置。
        """
        buy_defaults = BuyConfig.from_config_db()
        hide_header = ConfigDB.get_as_bool("hideHeader", False)
        return [
            gr.update(value=get_latest_proxy()),
            gr.update(value=get_proxy_api_url()),
            gr.update(value=get_proxy_api_protocol()),
            gr.update(value=ConfigDB.get("audioPath") or None),
            gr.update(value=ConfigDB.get("serverchanKey") or ""),
            gr.update(value=ConfigDB.get("serverchan3ApiUrl") or ""),
            gr.update(value=ConfigDB.get("pushplusToken") or ""),
            gr.update(value=ConfigDB.get("barkToken") or ""),
            gr.update(value=ConfigDB.get("meowNickname") or ""),
            gr.update(value=ConfigDB.get("ntfyUrl") or ""),
            gr.update(value=ConfigDB.get("ntfyUsername") or ""),
            gr.update(value=ConfigDB.get("ntfyPassword") or ""),
            gr.update(value=buy_defaults.show_qrcode),
            gr.update(value=buy_defaults.auto_open_payment_url),
            gr.update(
                value=str(ConfigDB.get("proxyAssignmentStrategy") or "balanced").lower()
            ),
            gr.update(value=ConfigDB.get_as_int("queueConcurrencyLimit", 0)),
            gr.update(value=buy_defaults.log_level),
            gr.update(value=ConfigDB.get_as_bool("autoCleanupLogs", True)),
            gr.update(
                value=ConfigDB.get_as_int(
                    "logRetentionDays",
                    DEFAULT_LOG_RETENTION_DAYS,
                )
            ),
            gr.update(value=ConfigDB.get_as_int("maxLogFiles", DEFAULT_MAX_LOG_FILES)),
            gr.update(value=ConfigDB.get_as_int("maxRunDirs", DEFAULT_MAX_RUN_DIRS)),
            gr.update(value=ConfigDB.get_as_bool("autoFillTime", True)),
            gr.update(value=not buy_defaults.show_random_message),
            gr.update(value=hide_header),
            gr.update(visible=not hide_header),
            gr.update(value=buy_defaults.use_local_token),
            gr.update(value=int(buy_defaults.interval or DEFAULT_REQUEST_INTERVAL)),
            gr.update(value=buy_defaults.create_retry_limit),
            gr.update(value=buy_defaults.create_request_batch_size),
            gr.update(value=buy_defaults.proxy_max_consecutive_failures),
            gr.update(value=buy_defaults.proxy_cooldown_seconds),
            gr.update(value=buy_defaults.proxy_backoff_max_seconds),
            gr.update(value=buy_defaults.notifier_config.notify_proxy_exhausted),
        ]

    return load_go_settings_configs, [
        https_proxy_ui,
        proxy_api_url_ui,
        proxy_api_protocol_ui,
        audio_path_ui,
        serverchan_ui,
        serverchan3_ui,
        pushplus_ui,
        bark_ui,
        meow_ui,
        ntfy_ui,
        ntfy_username_ui,
        ntfy_password_ui,
        show_qrcode_ui,
        auto_open_payment_url_ui,
        proxy_assignment_strategy_ui,
        queue_concurrency_limit_ui,
        log_level_ui,
        auto_cleanup_logs_ui,
        log_retention_days_ui,
        max_log_files_ui,
        max_run_dirs_ui,
        auto_fill_time_ui,
        show_random_message_ui,
        hide_header_ui,
        header_ui,
        use_local_token_ui,
        request_interval_ui,
        create_retry_limit_ui,
        create_request_batch_size_ui,
        proxy_max_consecutive_failures_ui,
        proxy_cooldown_seconds_ui,
        proxy_backoff_max_seconds_ui,
        notify_proxy_exhausted_ui,
    ]
