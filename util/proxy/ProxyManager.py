"""
文件整体功能：代理池管理器，负责解析、维护、轮换与应用代理配置。
所属模块：util.proxy
依赖文件：
    - util.proxy.ProxyState.ProxyStateRegistry（代理状态注册表）
    - requests（HTTP Session）
对外能力：
    1. 解析逗号分隔的代理字符串；
    2. 维护当前代理索引并提供切换到下一个可用代理的能力；
    3. 记录代理成功/失败状态，失败达到阈值后进入冷却；
    4. 将代理设置应用到 requests.Session；
    5. 提供代理状态快照与恢复功能。
"""
import requests

from util.proxy.ProxyState import ProxyStateRegistry


class ProxyManager:
    """
    代理池管理器。

    类设计作用：将用户配置的代理字符串转换为内部代理列表，
                结合 ProxyStateRegistry 实现代理的轮换、冷却与状态展示。
    存储属性：
        proxy_list (list[str])：规范化后的代理列表，至少包含 "none"。
        state_registry (ProxyStateRegistry)：代理状态注册表，记录可用性与失败次数。
    承担业务：为 BiliRequest 等模块提供统一代理应用、切换与状态查询接口。
    """

    def __init__(
        self,
        proxy_string: str = "none",
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 180.0,
    ):
        """
        初始化代理管理器。

        参数：
            proxy_string (str)：逗号分隔的代理地址，如 "http://a:1,http://b:2"，默认 "none"。
            failure_threshold (int)：触发冷却的连续失败次数阈值，默认 2。
            cooldown_seconds (float)：单次冷却时长（秒），默认 180。
        返回值：无。
        内部逻辑：解析并规范化代理列表，创建 ProxyStateRegistry。
        调用位置：BiliRequest 初始化时创建。
        """
        self.proxy_list = self.parse_proxy_list(proxy_string)
        if not self.proxy_list:
            raise ValueError("at least have none proxy")
        self.state_registry = ProxyStateRegistry(
            self.proxy_list,
            mask_proxy=self.mask_proxy_value,
            failure_threshold=failure_threshold,
            cooldown_seconds=cooldown_seconds,
        )

    @property
    def now_proxy_idx(self) -> int:
        """
        当前使用代理的索引。

        参数：无。
        返回值：int，当前代理在 proxy_list 中的索引。
        内部逻辑：委托给 state_registry.current_index。
        调用位置：current_proxy、snapshot 等属性与方法中读取。
        """
        return self.state_registry.current_index

    @now_proxy_idx.setter
    def now_proxy_idx(self, index: int) -> None:
        """
        设置当前代理索引。

        参数：
            index (int)：目标代理索引。
        返回值：无。
        内部逻辑：委托给 state_registry.set_current_index，越界时抛出 IndexError。
        调用位置：restore 等需要恢复代理位置的方法中调用。
        """
        self.state_registry.set_current_index(index)

    @staticmethod
    def normalize_proxy_value(proxy: str) -> str:
        """
        规范化单个代理值。

        参数：
            proxy (str)：原始代理字符串。
        返回值：str，去空白后的小写 "none"/"direct" 统一为 "none"，空字符串保留。
        内部逻辑：去除首尾空白，对 none/direct 做归一化。
        调用位置：parse_proxy_list、ProxyTester 等模块中调用。
        """
        proxy = (proxy or "").strip()
        if not proxy:
            return ""
        if proxy.lower() in {"none", "direct"}:
            return "none"
        return proxy

    @classmethod
    def parse_proxy_list(
        cls, proxy_string: str | None, include_direct_fallback: bool = False
    ) -> list[str]:
        """
        将代理字符串解析为规范化代理列表。

        参数：
            proxy_string (str | None)：逗号分隔的代理字符串，可能为 None。
            include_direct_fallback (bool)：是否在没有 none 时前置 "none"，默认 False。
        返回值：list[str]，去重并规范化后的代理列表。
        内部逻辑：
            1. 按逗号拆分；
            2. 逐个 normalize_proxy_value；
            3. 去重并保留首次出现顺序；
            4. 若开启 fallback 且不含 none，则在列表头部插入 none。
        调用位置：__init__、replace_proxy_list、ProxyTester 中调用。
        """
        proxy_list = []
        if proxy_string:
            proxy_list = [
                cls.normalize_proxy_value(item)
                for item in proxy_string.split(",")
                if item and item.strip()
            ]

        normalized: list[str] = []
        seen: set[str] = set()
        for item in proxy_list:
            if not item:
                continue
            key = item.lower()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(item)

        if include_direct_fallback and "none" not in seen:
            normalized.insert(0, "none")

        return normalized

    @staticmethod
    def mask_proxy_value(proxy: str) -> str:
        """
        对代理地址中的认证信息进行脱敏。

        参数：
            proxy (str)：原始代理字符串。
        返回值：str，脱敏后的代理字符串；直连显示为"直连"，无认证代理原样返回。
        内部逻辑：
            1. 空值返回空；
            2. none/direct 返回"直连"；
            3. 含 @ 的代理将用户名密码替换为 ***:***。
        调用位置：state_registry 初始化、current_proxy_display、ProxyTester 中调用。
        """
        proxy = (proxy or "").strip()
        if not proxy:
            return ""
        if proxy.lower() in {"none", "direct"}:
            return "直连"
        if "://" not in proxy:
            return proxy

        scheme, remainder = proxy.split("://", 1)
        if "@" not in remainder:
            return proxy

        _, host_part = remainder.rsplit("@", 1)
        return f"{scheme}://***:***@{host_part}"

    @classmethod
    def mask_proxy_string(cls, proxy_string: str | None) -> str:
        """
        对整个代理字符串进行脱敏。

        参数：
            proxy_string (str | None)：逗号分隔的代理字符串。
        返回值：str，脱敏后用逗号连接的字符串。
        内部逻辑：解析列表后逐个 mask_proxy_value，再拼接。
        调用位置：日志输出、前端展示等需要隐藏敏感信息的场景。
        """
        proxies = cls.parse_proxy_list(proxy_string)
        masked = [cls.mask_proxy_value(proxy) for proxy in proxies]
        return ",".join(item for item in masked if item)

    @property
    def current_proxy(self) -> str:
        """
        获取当前正在使用的代理地址。

        参数：无。
        返回值：str，当前代理字符串。
        内部逻辑：通过 now_proxy_idx 索引 proxy_list。
        调用位置：apply_to_session、current_proxy_display 等方法中调用。
        """
        return self.proxy_list[self.now_proxy_idx]

    @property
    def current_proxy_display(self) -> str:
        """
        获取当前代理的脱敏显示名称。

        参数：无。
        返回值：str，脱敏后的当前代理名称。
        内部逻辑：对 current_proxy 调用 mask_proxy_value。
        调用位置：BiliRequest.current_proxy_display、日志与界面展示。
        """
        return self.mask_proxy_value(self.current_proxy)

    def current_proxy_status(self) -> str:
        """
        获取当前代理状态文本。

        参数：无。
        返回值：str，包含当前代理名、可用数与冷却数的描述。
        内部逻辑：委托给 state_registry.current_status_text。
        调用位置：BiliRequest.current_proxy_status、界面状态栏。
        """
        return self.state_registry.current_status_text()

    def proxy_pool_status(self) -> str:
        """
        获取代理池整体状态文本。

        参数：无。
        返回值：str，包含每个代理可用/冷却状态的描述。
        内部逻辑：委托给 state_registry.describe_all_states。
        调用位置：BiliRequest.proxy_pool_status、调试日志。
        """
        return self.state_registry.describe_all_states()

    def replace_proxy_list(self, proxy_string: str) -> None:
        """
        替换当前代理列表并重新初始化状态注册表。

        参数：
            proxy_string (str)：新的逗号分隔代理字符串。
        返回值：无。
        内部逻辑：
            1. 解析新列表；
            2. 校验至少存在一个代理；
            3. 重置 proxy_list 与 state_registry，保留原有阈值与冷却时长。
        调用位置：用户在线更新代理配置时调用，如 BiliRequest.replace_proxy_pool。
        """
        proxy_list = self.parse_proxy_list(proxy_string)
        if not proxy_list:
            raise ValueError("at least have none proxy")
        self.proxy_list = proxy_list
        self.state_registry = ProxyStateRegistry(
            self.proxy_list,
            mask_proxy=self.mask_proxy_value,
            failure_threshold=self.state_registry.failure_threshold,
            cooldown_seconds=self.state_registry.cooldown_seconds,
        )

    def snapshot(self) -> int:
        """
        快照当前代理索引。

        参数：无。
        返回值：int，当前代理索引。
        内部逻辑：直接返回 now_proxy_idx。
        调用位置：需要保存当前代理位置以便后续恢复的场景，如 BiliRequest.snapshot_proxy_state。
        """
        return self.now_proxy_idx

    def restore(self, index: int) -> None:
        """
        恢复到指定代理索引。

        参数：
            index (int)：目标代理索引。
        返回值：无。
        内部逻辑：设置 now_proxy_idx。
        调用位置：BiliRequest.restore_proxy_state 中调用。
        """
        self.now_proxy_idx = index

    def apply_to_session(self, session: requests.Session) -> None:
        """
        将当前代理应用到 requests.Session。

        参数：
            session (requests.Session)：目标 Session 对象。
        返回值：无。
        内部逻辑：
            1. 关闭 trust_env 避免环境变量代理干扰；
            2. 当前代理为 none 时清空 proxies；
            3. 否则设置 http 与 https 代理。
        调用位置：BiliRequest 初始化、切换代理、恢复代理状态时调用。
        """
        session.trust_env = False
        if self.current_proxy == "none":
            session.proxies = {}
            return
        session.proxies = {
            "http": self.current_proxy,
            "https": self.current_proxy,
        }

    def rotate(self) -> bool:
        """
        切换到下一个可用代理。

        参数：无。
        返回值：bool，切换成功返回 True，无可用代理返回 False。
        内部逻辑：委托给 state_registry.switch_to_next_available。
        调用位置：BiliRequest._rotate_proxy、手动切换代理时调用。
        """
        return self.state_registry.switch_to_next_available()

    def ensure_current_available(self) -> bool:
        """
        确保当前代理可用，若不可用则尝试切换。

        参数：无。
        返回值：bool，当前代理可用或切换成功返回 True，否则 False。
        内部逻辑：委托给 state_registry.ensure_current_available。
        调用位置：BiliRequest.ensure_active_proxy 中调用。
        """
        return self.state_registry.ensure_current_available()

    def has_available_proxy(self) -> bool:
        """
        判断代理池中是否还有可用代理。

        参数：无。
        返回值：bool，存在可用代理返回 True，否则 False。
        内部逻辑：委托给 state_registry.has_available_proxy。
        调用位置：BiliRequest.has_available_proxy、抢票重试逻辑中调用。
        """
        return self.state_registry.has_available_proxy()

    def is_current_proxy_available(self) -> bool:
        """
        判断当前代理是否可用。

        参数：无。
        返回值：bool，当前代理不在冷却中返回 True，否则 False。
        内部逻辑：委托给 state_registry.is_current_available。
        调用位置：BiliRequest.is_current_proxy_available 中调用。
        """
        return self.state_registry.is_current_available()

    def mark_current_success(self) -> None:
        """
        标记当前代理本次请求成功。

        参数：无。
        返回值：无。
        内部逻辑：委托给 state_registry.record_current_success，清空近期失败记录。
        调用位置：BiliRequest 在请求成功后调用。
        """
        self.state_registry.record_current_success()

    def mark_current_failure(self, reason: str) -> bool:
        """
        标记当前代理本次请求失败。

        参数：
            reason (str)：失败原因描述，用于日志与状态展示。
        返回值：bool，若失败达到阈值触发冷却返回 True，否则 False。
        内部逻辑：委托给 state_registry.record_current_failure。
        调用位置：BiliRequest 在请求异常或业务失败时调用。
        """
        return self.state_registry.record_current_failure(reason)
