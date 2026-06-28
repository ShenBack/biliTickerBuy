"""
文件整体功能：封装 B 站会员购的 HTTP/HTTPS 请求，管理浏览器指纹、Cookie、代理与 HTTP/2 连接。
所属模块：util.request
依赖文件：
    - util.Constant（H2_LIMITS、H2_TIMEOUT 等常量）
    - util.request.BrowerState（浏览器指纹与请求头构造）
    - util.request.CookieManager（Cookie 管理）
    - util.request.exceptions（BiliConnectionError、BiliRateLimitError）
    - util.proxy.ProxyManager（代理池管理）
    - requests、loguru（第三方库）
对外能力：
    1. 提供统一的 get/post 方法；
    2. 自动管理代理轮换、Cookie 同步、HTTP/2 连接预热与恢复；
    3. 提供代理状态快照/恢复、错误码 100001 处理等扩展能力。
"""
import secrets
import time
from collections.abc import Callable

import loguru
import requests
from requests import Response
from util.Constant import H2_LIMITS, H2_TIMEOUT
from util.request.BrowerState import (
    BrowserFingerprintState,
    build_headers_from_browser_state,
    finalize_device_id,
    generate_browser_fingerprint_state,
)
from util.request.CookieManager import CookieManager
from util.request.exceptions import BiliConnectionError, BiliRateLimitError
from util.proxy.ProxyManager import ProxyManager


class BiliRequest:
    """
    B 站会员购请求封装类。

    类设计作用：为抢票流程提供统一的 HTTP/2 请求入口，同时维护浏览器指纹、
                Cookie、代理池与连接状态，降低上层业务对网络细节的感知。
    存储属性：
        browser_state (BrowserFingerprintState)：当前使用的浏览器指纹。
        deviceId (str)：经过 finalize_device_id 变换后的设备 ID。
        session (requests.Session)：普通 HTTP/1.1 请求会话。
        proxy_manager (ProxyManager)：代理池管理器。
        cookieManager (CookieManager)：Cookie 管理器。
        headers (dict[str, str])：基于 browser_state 构造的请求头。
        request_count (int)：连续 412 风控请求计数。
        _h2_client (httpx.Client | None)：HTTP/2 客户端实例，按需懒加载。
        createTime (int)：实例创建时间戳（毫秒）。
        _handle_100001 (Callable | None)：错误码 100001 的自定义处理回调。
    承担业务：统一封装 B 站 API 的 get/post 请求、代理管理、Cookie 同步与异常转换。
    """

    def __init__(
        self,
        headers=None,
        cookies=None,
        cookies_config_path=None,
        proxy: str = "none",
        browser_state: BrowserFingerprintState | None = None,
        proxy_failure_threshold: int = 2,
        proxy_cooldown_seconds: float = 180.0,
    ):
        """
        初始化 BiliRequest 实例。

        参数：
            headers (dict | None)：用户自定义基础请求头。
            cookies (list[dict] | None)：初始 Cookie 列表。
            cookies_config_path (str | None)：Cookie 数据库路径。
            proxy (str)：逗号分隔的代理字符串，默认 "none"。
            browser_state (BrowserFingerprintState | None)：自定义浏览器指纹，为 None 时随机生成。
            proxy_failure_threshold (int)：触发代理冷却的连续失败次数，默认 2。
            proxy_cooldown_seconds (float)：代理冷却时长（秒），默认 180。
        返回值：无。
        内部逻辑：
            1. 生成或采用传入的 browser_state 与 deviceId；
            2. 创建 requests.Session 与 ProxyManager；
            3. 初始化 CookieManager 并构造请求头；
            4. 将代理应用到 Session。
        调用位置：抢票任务初始化 B 站请求客户端时调用。
        """
        self.browser_state = browser_state or generate_browser_fingerprint_state()
        self.deviceId = finalize_device_id(secrets.token_hex(16))
        self.session = requests.Session()
        self.proxy_manager = ProxyManager(
            proxy,
            failure_threshold=proxy_failure_threshold,
            cooldown_seconds=proxy_cooldown_seconds,
        )
        self.cookieManager = CookieManager(cookies_config_path, cookies)
        self.headers = build_headers_from_browser_state(
            self.browser_state,
            base_headers=headers,
            referer="https://show.bilibili.com/",
            content_type="application/x-www-form-urlencoded",
        )
        self.request_count = 0  # 记录请求次数
        self.proxy_manager.apply_to_session(self.session)
        self._h2_client = None
        self.createTime = int(time.time() * 1000)
        self._handle_100001: Callable[[], None] | None = None

    def _rotate_proxy(self, reason: str) -> bool:
        """
        轮换到下一个可用代理。

        参数：
            reason (str)：轮换原因，用于日志记录。
        返回值：bool，轮换成功返回 True，无可用代理返回 False。
        内部逻辑：调用 proxy_manager.rotate，成功后应用新代理到 Session 并失效旧 H2 客户端。
        调用位置：switch_proxy 等需要主动切换代理的方法中调用。
        """
        if not self.proxy_manager.rotate():
            return False
        self.proxy_manager.apply_to_session(self.session)
        self._invalidate_h2_client()
        return True

    def _invalidate_h2_client(self):
        """
        关闭并清空当前 HTTP/2 客户端。

        参数：无。
        返回值：无。
        内部逻辑：若 _h2_client 存在则尝试关闭，忽略异常后置为 None。
        调用位置：代理切换、恢复或 H2 连接异常后调用。
        """
        if self._h2_client is None:
            return
        try:
            self._h2_client.close()
        except Exception:
            pass
        self._h2_client = None

    def get_user_agent(self) -> str:
        """
        获取当前 User-Agent。

        参数：无。
        返回值：str，当前 headers 中的 user-agent。
        内部逻辑：从 self.headers 读取 user-agent 字段。
        调用位置：需要记录或展示 UA 时调用。
        """
        return self.headers.get("user-agent", "")

    def snapshot_proxy_state(self) -> int:
        """
        快照当前代理索引。

        参数：无。
        返回值：int，当前代理索引。
        内部逻辑：委托给 proxy_manager.snapshot。
        调用位置：抢票任务需要保存代理状态以便恢复时调用。
        """
        return self.proxy_manager.snapshot()

    def restore_proxy_state(self, state: int) -> None:
        """
        恢复到指定代理索引。

        参数：
            state (int)：目标代理索引。
        返回值：无。
        内部逻辑：调用 proxy_manager.restore 后重新应用代理并失效 H2 客户端。
        调用位置：抢票任务从快照恢复代理状态时调用。
        """
        self.proxy_manager.restore(state)
        self.proxy_manager.apply_to_session(self.session)
        self._invalidate_h2_client()

    def clear_request_count(self):
        """
        清空连续 412 请求计数。

        参数：无。
        返回值：无。
        内部逻辑：将 request_count 重置为 0。
        调用位置：请求成功返回后调用。
        """
        self.request_count = 0

    def set_100001_handler(self, handler: Callable[[], None] | None) -> None:
        """
        设置错误码 100001 的自定义处理回调。

        参数：
            handler (Callable | None)：无参回调函数，为 None 时取消处理。
        返回值：无。
        内部逻辑：保存 handler 到 _handle_100001。
        调用位置：上层业务需要响应 100001 维护状态时调用。
        """
        self._handle_100001 = handler

    def handle_100001(self, err: int) -> bool:
        """
        处理错误码 100001。

        参数：
            err (int)：接口返回的 errno。
        返回值：bool，若 err 为 100001 且存在回调并执行成功返回 True，否则 False。
        内部逻辑：校验 err 与回调存在性，记录日志后执行回调。
        调用位置：_request 在解析响应后检测到 100001 时调用。
        """
        if err != 100001 or self._handle_100001 is None:
            return False
        loguru.logger.warning("错误码 100001，执行维护逻辑")
        self._handle_100001()
        return True

    def get(self, url, data=None, isJson=False):
        """
        发起 GET 请求。

        参数：
            url (str)：请求地址。
            data (dict | None)：查询参数。
            isJson (bool)：是否以 JSON 格式发送，默认 False。
        返回值：requests.Response 或 httpx.Response，底层响应对象。
        内部逻辑：委托给 _request("get", ...)。
        调用位置：上层业务调用 B 站 GET 接口时直接使用。
        """
        return self._request("get", url, data=data, isJson=isJson)

    def switch_proxy(self):
        """
        手动切换代理。

        参数：无。
        返回值：bool，切换成功返回 True，否则 False。
        内部逻辑：调用 _rotate_proxy 并传入原因"手动切换代理"。
        调用位置：用户手动触发代理切换或任务检测到当前代理不可用时调用。
        """
        return self._rotate_proxy("手动切换代理")

    def post(self, url, data=None, isJson=False):
        """
        发起 POST 请求。

        参数：
            url (str)：请求地址。
            data (dict | None)：请求体数据。
            isJson (bool)：是否以 JSON 格式发送，默认 False。
        返回值：requests.Response 或 httpx.Response，底层响应对象。
        内部逻辑：委托给 _request("post", ...)。
        调用位置：上层业务调用 B 站 POST 接口时直接使用。
        """
        return self._request("post", url, data=data, isJson=isJson)

    def current_proxy_display(self) -> str:
        """
        获取当前代理的脱敏显示名称。

        参数：无。
        返回值：str，脱敏后的当前代理。
        内部逻辑：委托给 proxy_manager.current_proxy_display。
        调用位置：界面展示或日志中需要隐藏敏感代理信息时调用。
        """
        return self.proxy_manager.current_proxy_display

    def current_proxy_status(self) -> str:
        """
        获取当前代理状态文本。

        参数：无。
        返回值：str，包含当前代理与可用/冷却数量的描述。
        内部逻辑：委托给 proxy_manager.current_proxy_status。
        调用位置：状态展示、调试日志中调用。
        """
        return self.proxy_manager.current_proxy_status()

    def proxy_pool_status(self) -> str:
        """
        获取代理池整体状态文本。

        参数：无。
        返回值：str，逐条展示每个代理的状态。
        内部逻辑：委托给 proxy_manager.proxy_pool_status。
        调用位置：调试或详情展示中调用。
        """
        return self.proxy_manager.proxy_pool_status()

    def replace_proxy_pool(self, proxy_string: str) -> None:
        """
        替换当前代理池。

        参数：
            proxy_string (str)：新的逗号分隔代理字符串。
        返回值：无。
        内部逻辑：调用 proxy_manager.replace_proxy_list 后应用新代理并失效 H2 客户端。
        调用位置：用户在线更新代理配置时调用。
        """
        self.proxy_manager.replace_proxy_list(proxy_string)
        self.proxy_manager.apply_to_session(self.session)
        self._invalidate_h2_client()

    def has_available_proxy(self) -> bool:
        """
        判断代理池中是否还有可用代理。

        参数：无。
        返回值：bool，存在可用代理返回 True。
        内部逻辑：委托给 proxy_manager.has_available_proxy。
        调用位置：抢票重试前检查代理可用性时调用。
        """
        return self.proxy_manager.has_available_proxy()

    def is_current_proxy_available(self) -> bool:
        """
        判断当前代理是否可用。

        参数：无。
        返回值：bool，当前代理不在冷却中返回 True。
        内部逻辑：委托给 proxy_manager.is_current_proxy_available。
        调用位置：请求前检查当前代理状态时调用。
        """
        return self.proxy_manager.is_current_proxy_available()

    def ensure_active_proxy(self) -> bool:
        """
        确保当前代理可用，否则尝试切换。

        参数：无。
        返回值：bool，当前可用或切换成功返回 True，否则 False。
        内部逻辑：调用 proxy_manager.ensure_current_available，若切换成功则应用新代理。
        调用位置：请求前确保代理可用时调用。
        """
        if not self.proxy_manager.ensure_current_available():
            return False
        self.proxy_manager.apply_to_session(self.session)
        return True

    def mark_current_proxy_failure(self, reason: str) -> bool:
        """
        标记当前代理失败。

        参数：
            reason (str)：失败原因。
        返回值：bool，若达到阈值触发冷却返回 True，否则 False。
        内部逻辑：委托给 proxy_manager.mark_current_failure。
        调用位置：请求异常或业务失败时调用。
        """
        return self.proxy_manager.mark_current_failure(reason)

    def mark_current_proxy_success(self) -> None:
        """
        标记当前代理成功。

        参数：无。
        返回值：无。
        内部逻辑：委托给 proxy_manager.mark_current_success。
        调用位置：请求成功后调用。
        """
        self.proxy_manager.mark_current_success()

    def describe_non_json_response(
        self, response: Response, body_limit: int = 300
    ) -> str:
        """
        生成非 JSON 响应的描述文本，用于日志记录。

        参数：
            response (Response)：requests 或 httpx 响应对象。
            body_limit (int)：正文预览最大长度，默认 300。
        返回值：str，包含状态码、Content-Type、URL 与正文预览的描述。
        内部逻辑：
            1. 读取响应正文并转义换行符；
            2. 超过 body_limit 则截断；
            3. 拼接状态码、content_type、url、body_preview。
        调用位置：解析响应 JSON 失败或需要记录异常响应时调用。
        """
        content_type = response.headers.get("Content-Type", "未知")
        body = response.text or ""
        body = body.replace("\r", "\\r").replace("\n", "\\n")
        if len(body) > body_limit:
            body = body[:body_limit] + "..."
        if not body:
            body = "<empty>"
        return (
            f"status={response.status_code}, "
            f"content_type={content_type}, "
            f"url={response.url}, "
            f"body_preview={body}"
        )

    def _build_h2_client(self):
        """
        构建 HTTP/2 客户端。

        参数：无。
        返回值：httpx.Client，配置好 HTTP/2、代理、超时与默认头的客户端。
        内部逻辑：
            1. 从 session.proxies 读取当前代理；
            2. 读取 session.verify；
            3. 使用 H2_TIMEOUT、H2_LIMITS 构造 httpx.Client。
        调用位置：_h2_send 与 prewarm_h2_connection 中按需调用。
        """
        import httpx

        proxies = self.session.proxies or {}
        proxy = proxies.get("https") or proxies.get("http") or None
        verify = (
            self.session.verify
            if isinstance(self.session.verify, (bool, str))
            else True
        )
        return httpx.Client(
            http2=True,
            verify=verify,
            proxy=proxy,
            timeout=httpx.Timeout(**H2_TIMEOUT),
            limits=httpx.Limits(**H2_LIMITS),
            headers={
                "accept": "*/*",
                "accept-encoding": "gzip, deflate, br, zstd",
                "connection": "keep-alive",
                "user-agent": self.headers.get("user-agent", ""),
            },
        )

    def prewarm_h2_connection(self, url: str) -> None:
        """
        预热 HTTP/2 连接。

        参数：
            url (str)：目标 URL，用于发送 HEAD 请求预热。
        返回值：无。
        内部逻辑：
            1. 若 H2 客户端未创建则构建；
            2. 同步当前 UA 与 Cookie 到 H2 客户端；
            3. 发送 HEAD 请求忽略异常。
        调用位置：抢票开始前或需要提前建立 H2 连接时调用。
        """
        import httpx

        if self._h2_client is None:
            self._h2_client = self._build_h2_client()
        client = self._h2_client
        client.headers["user-agent"] = self.headers.get("user-agent", "")
        for cookie in self.cookieManager.get_cookies(force=True) or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                client.cookies.set(name, value, domain=".bilibili.com")
        try:
            client.head(url)
        except httpx.HTTPError:
            pass

    def _h2_send(self, method: str, url, data=None, isJson=False):
        """
        使用 HTTP/2 客户端发送请求。

        参数：
            method (str)：请求方法，get 或 post。
            url (str)：请求地址。
            data (dict | None)：请求数据或查询参数。
            isJson (bool)：POST 时是否以 JSON 发送。
        返回值：httpx.Response，H2 响应对象。
        内部逻辑：
            1. 确保 H2 客户端已创建；
            2. 同步 UA 与 Cookie；
            3. 根据 method 调用 get 或 post。
        调用位置：_send_with_h2_recovery 中调用。
        """
        if self._h2_client is None:
            self._h2_client = self._build_h2_client()
        client = self._h2_client
        client.headers["user-agent"] = self.headers.get("user-agent", "")
        for cookie in self.cookieManager.get_cookies(force=True) or []:
            name = cookie.get("name")
            value = cookie.get("value")
            if name and value is not None:
                client.cookies.set(name, value, domain=".bilibili.com")
        
        # 检查实际请求中的 cookie
        loguru.logger.debug(f"[H2 Client Cookies] {dict(client.cookies)}")
        
        if method.lower() == "post":
            return (
                client.post(url, json=data) if isJson else client.post(url, data=data)
            )
        return client.get(url, params=data)

    def _send_with_h2_recovery(self, method: str, url, data=None, isJson=False):
        """
        带恢复机制的 HTTP/2 请求发送。

        参数：
            method (str)：请求方法。
            url (str)：请求地址。
            data (dict | None)：请求数据。
            isJson (bool)：是否以 JSON 发送。
        返回值：httpx.Response，H2 响应对象。
        内部逻辑：
            1. 最多重试 2 次；
            2. 捕获 TimeoutException 与 LocalProtocolError，失效 H2 客户端后重试；
            3. 第二次失败则抛出 BiliConnectionError。
        调用位置：_request 中实际发送请求时调用。
        """
        import httpx

        for attempt in range(2):
            try:
                return self._h2_send(method, url, data=data, isJson=isJson)
            except httpx.TimeoutException as exc:
                self._invalidate_h2_client()
                if attempt >= 1:
                    raise BiliConnectionError(
                        "网络请求超时：服务器响应过慢，请稍后重试",
                        cause=exc,
                    ) from exc
                loguru.logger.warning("HTTP 请求超时，已重建连接后重试: {}", exc)
            except httpx.LocalProtocolError as exc:
                self._invalidate_h2_client()
                if attempt >= 1:
                    raise BiliConnectionError(
                        "网络连接异常：HTTP/2 连接已断开，重试后仍失败，请稍后再试",
                        cause=exc,
                    ) from exc
                loguru.logger.warning("HTTP/2 连接状态异常，已重建连接后重试: {}", exc)

    def _request(self, method: str, url, data=None, isJson=False):
        """
        统一请求入口，处理状态码、限流、Cookie 同步与登录校验。

        参数：
            method (str)：请求方法，get 或 post。
            url (str)：请求地址。
            data (dict | None)：请求数据。
            isJson (bool)：是否以 JSON 发送。
        返回值：httpx.Response，底层响应对象。
        内部逻辑：
            1. 通过 _send_with_h2_recovery 发送请求；
            2. 412 状态码增加计数并直接返回；
            3. 429 状态码抛出 BiliRateLimitError；
            4. 调用 raise_for_status 处理其他 HTTP 错误；
            5. 清空 412 计数、标记代理成功、同步响应 Cookie；
            6. 若响应提示"请先登录"则抛出 RuntimeError。
        调用位置：get、post 方法内部调用。
        """
        response = self._send_with_h2_recovery(
            method,
            url,
            data=data,
            isJson=isJson,
        )

        if response.status_code == 412:
            self.request_count += 1
            return response
        if response.status_code == 429:
            raise BiliRateLimitError(
                "请求被限流(HTTP 429)",
                response=response,
            )

        response.raise_for_status()
        self.clear_request_count()
        self.mark_current_proxy_success()
        self._sync_response_cookies(response)
        if response.json().get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return response

    def _sync_response_cookies(self, response):
        """
        将服务器返回的 Set-Cookie 同步到 CookieManager。

        参数：
            response (Response)：requests 或 httpx 响应对象。
        返回值：无。
        内部逻辑：
            1. 检查 response.cookies；
            2. 获取当前 Cookie 列表；
            3. 将新 Cookie 追加到列表并写回数据库；
            4. 记录调试日志。
        调用位置：_request 在请求成功后调用。
        """
        if not response.cookies:
            return
        try:
            current_cookies = self.cookieManager.get_cookies(force=True)
        except RuntimeError:
            return

        current_names = {c["name"] for c in current_cookies}
        updated = False
        for name, value in response.cookies.items():
            if name not in current_names:
                current_cookies.append({"name": name, "value": value})
                loguru.logger.debug(f"新增Cookie: {name}={value[:30]}...")
                updated = True

        if updated:
            self.cookieManager.db.insert(self.cookieManager._COOKIE_KEY, current_cookies)

    def get_all_cookies(self) -> list[dict]:
        """
        获取合并后的所有 Cookie（CookieManager + Session）。

        参数：无。
        返回值：list[dict]，包含 name 与 value 的 Cookie 列表。
        内部逻辑：
            1. 从 CookieManager 读取当前 Cookie；
            2. 从 session.cookies 补充未出现的 Cookie；
            3. 返回合并后的列表。
        调用位置：调试或导出完整 Cookie 时调用。
        """
        result = {}
        try:
            for c in self.cookieManager.get_cookies():
                result[c["name"]] = c["value"]
        except RuntimeError:
            pass
        for cookie in self.session.cookies:
            if cookie.name not in result:
                result[cookie.name] = cookie.value
        return [{"name": k, "value": v} for k, v in result.items()]

    def get_request_name(self):
        """
        获取当前登录账号的用户名。

        参数：无。
        返回值：str，当前账号昵称；未登录或失败返回"未登录"。
        内部逻辑：
            1. 检查是否已保存 Cookie；
            2. 调用 B 站 nav 接口获取 uname；
            3. 异常时返回"未登录"。
        调用位置：界面展示当前登录用户时调用。
        """
        try:
            if not self.cookieManager.have_cookies():
                loguru.logger.warning("获取用户名失败，请重新登录")
                return "未登录"
            result = self.get("https://api.bilibili.com/x/web-interface/nav").json()
            return result["data"]["uname"]
        except Exception:
            return "未登录"
