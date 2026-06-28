"""
文件整体功能：维护代理池中每个代理的可用状态、失败计数与冷却时间。
所属模块：util.proxy
依赖文件：无项目内业务依赖。
对外能力：
    1. 提供 ProxyStateEntry 记录单个代理的冷却、失败、成功等信息；
    2. 提供 ProxyStateRegistry 管理整个代理列表的状态、切换与统计。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import time
from typing import Callable


@dataclass
class ProxyStateEntry:
    """
    单个代理的状态条目。

    类设计作用：记录某个代理的原始地址、展示名称、冷却截止时间、
                总失败/成功次数以及近期失败时间戳。
    存储属性：
        raw_proxy (str)：原始代理字符串。
        display_name (str)：脱敏后的展示名称。
        cooldown_until (float)：冷却结束时间戳（秒级）。
        total_failures (int)：累计失败次数。
        total_successes (int)：累计成功次数。
        last_reason (str)：最近一次失败原因。
        recent_failures (deque[float])：近期失败时间戳，用于滑动窗口判定。
    承担业务：为 ProxyStateRegistry 提供单代理粒度的状态维护。
    """
    raw_proxy: str
    display_name: str
    cooldown_until: float = 0.0
    total_failures: int = 0
    total_successes: int = 0
    last_reason: str = ""
    recent_failures: deque[float] = field(default_factory=deque)

    def is_available(self, now: float | None = None) -> bool:
        """
        判断该代理当前是否可用。

        参数：
            now (float | None)：可选的当前时间戳，为 None 时使用 time.time()。
        返回值：bool，当前时间已过冷却时间返回 True，否则 False。
        内部逻辑：比较当前时间与 cooldown_until。
        调用位置：ProxyStateRegistry 中所有可用性判断逻辑。
        """
        current_time = time.time() if now is None else now
        return current_time >= self.cooldown_until

    def cooldown_remaining(self, now: float | None = None) -> int:
        """
        获取该代理剩余冷却秒数。

        参数：
            now (float | None)：可选的当前时间戳，为 None 时使用 time.time()。
        返回值：int，剩余冷却秒数，已可用返回 0。
        内部逻辑：计算 cooldown_until - current_time 并与 0 取最大值。
        调用位置：describe_all_states 等需要展示冷却倒计时的场景。
        """
        current_time = time.time() if now is None else now
        return max(0, int(self.cooldown_until - current_time))


class ProxyStateRegistry:
    """
    代理状态注册表。

    类设计作用：管理一组代理的实时状态，提供当前代理维护、失败阈值判定、
                冷却控制、可用代理切换与状态文本生成。
    存储属性：
        failure_threshold (int)：触发冷却所需的近期失败次数。
        failure_window_seconds (float)：近期失败的统计窗口时长。
        cooldown_seconds (float)：触发冷却后的惩罚时长。
        current_index (int)：当前正在使用的代理索引。
        states (list[ProxyStateEntry])：所有代理的状态条目列表。
    承担业务：ProxyManager 通过此类完成代理轮换、成功/失败记录与状态展示。
    """

    def __init__(
        self,
        proxy_list: list[str],
        *,
        mask_proxy: Callable[[str], str],
        failure_threshold: int = 2,
        failure_window_seconds: float = 45.0,
        cooldown_seconds: float = 180.0,
    ):
        """
        初始化代理状态注册表。

        参数：
            proxy_list (list[str])：代理字符串列表。
            mask_proxy (Callable[[str], str])：代理脱敏函数，用于生成 display_name。
            failure_threshold (int)：触发冷却的连续失败次数，默认 2。
            failure_window_seconds (float)：失败统计窗口（秒），默认 45。
            cooldown_seconds (float)：冷却时长（秒），默认 180。
        返回值：无。
        内部逻辑：校验参数下限，为每个代理创建 ProxyStateEntry。
        调用位置：ProxyManager 初始化与替换代理列表时调用。
        """
        self.failure_threshold = max(1, int(failure_threshold))
        self.failure_window_seconds = max(1.0, float(failure_window_seconds))
        self.cooldown_seconds = max(1.0, float(cooldown_seconds))
        self.current_index = 0
        self.states = [
            ProxyStateEntry(
                raw_proxy=proxy,
                display_name=mask_proxy(proxy) or proxy,
            )
            for proxy in proxy_list
        ]

    def set_current_index(self, index: int) -> None:
        """
        设置当前代理索引。

        参数：
            index (int)：目标索引。
        返回值：无。
        内部逻辑：校验索引范围后赋值。
        调用位置：ProxyManager 恢复代理或显式切换时调用。
        """
        if index < 0 or index >= len(self.states):
            raise IndexError("proxy index out of range")
        self.current_index = index

    def current_state(self) -> ProxyStateEntry:
        """
        获取当前代理的状态条目。

        参数：无。
        返回值：ProxyStateEntry，当前代理对应的状态条目。
        内部逻辑：通过 current_index 索引 states。
        调用位置：record_current_success、record_current_failure 等内部方法调用。
        """
        return self.states[self.current_index]

    def current_display_name(self) -> str:
        """
        获取当前代理的展示名称。

        参数：无。
        返回值：str，当前代理的 display_name。
        内部逻辑：委托给 current_state().display_name。
        调用位置：current_status_text 等状态展示方法中调用。
        """
        return self.current_state().display_name

    def _trim_failures(self, state: ProxyStateEntry, now: float) -> None:
        """
        清理超出统计窗口的近期失败记录。

        参数：
            state (ProxyStateEntry)：要清理的代理状态条目。
            now (float)：当前时间戳。
        返回值：无。
        内部逻辑：从 deque 头部移除早于窗口起始时间的记录。
        调用位置：record_current_failure 中调用，确保阈值判定基于滑动窗口。
        """
        window_start = now - self.failure_window_seconds
        while state.recent_failures and state.recent_failures[0] < window_start:
            state.recent_failures.popleft()

    def record_current_success(self) -> None:
        """
        记录当前代理一次成功请求。

        参数：无。
        返回值：无。
        内部逻辑：增加 total_successes 并清空 recent_failures。
        调用位置：ProxyManager.mark_current_success 中调用。
        """
        state = self.current_state()
        state.total_successes += 1
        state.recent_failures.clear()

    def record_current_failure(self, reason: str) -> bool:
        """
        记录当前代理一次失败请求。

        参数：
            reason (str)：失败原因描述。
        返回值：bool，若近期失败达到阈值并触发冷却返回 True，否则 False。
        内部逻辑：
            1. 增加 total_failures 并记录 last_reason；
            2. 将当前时间加入 recent_failures；
            3. 清理过期失败记录；
            4. 达到阈值则设置 cooldown_until 并清空近期失败。
        调用位置：ProxyManager.mark_current_failure 中调用。
        """
        now = time.time()
        state = self.current_state()
        state.total_failures += 1
        state.last_reason = reason
        state.recent_failures.append(now)
        self._trim_failures(state, now)
        if len(state.recent_failures) < self.failure_threshold:
            return False
        state.cooldown_until = max(state.cooldown_until, now + self.cooldown_seconds)
        state.recent_failures.clear()
        return True

    def available_count(self, now: float | None = None) -> int:
        """
        统计当前可用代理数量。

        参数：
            now (float | None)：可选的当前时间戳。
        返回值：int，可用代理数量。
        内部逻辑：遍历 states 统计 is_available 为 True 的数量。
        调用位置：current_status_text、has_available_proxy 中调用。
        """
        current_time = time.time() if now is None else now
        return sum(1 for state in self.states if state.is_available(current_time))

    def cooldown_count(self, now: float | None = None) -> int:
        """
        统计当前处于冷却中的代理数量。

        参数：
            now (float | None)：可选的当前时间戳。
        返回值：int，冷却中代理数量。
        内部逻辑：遍历 states 统计 is_available 为 False 的数量。
        调用位置：current_status_text 中调用。
        """
        current_time = time.time() if now is None else now
        return sum(1 for state in self.states if not state.is_available(current_time))

    def has_available_proxy(self, now: float | None = None) -> bool:
        """
        判断代理池中是否至少有一个可用代理。

        参数：
            now (float | None)：可选的当前时间戳。
        返回值：bool，可用数量大于 0 返回 True。
        内部逻辑：调用 available_count 并判断结果。
        调用位置：ProxyManager.has_available_proxy、抢票重试逻辑中调用。
        """
        return self.available_count(now) > 0

    def is_current_available(self, now: float | None = None) -> bool:
        """
        判断当前代理是否可用。

        参数：
            now (float | None)：可选的当前时间戳。
        返回值：bool，当前代理不在冷却中返回 True。
        内部逻辑：委托给 current_state().is_available。
        调用位置：ProxyManager.is_current_proxy_available 中调用。
        """
        return self.current_state().is_available(now)

    def switch_to_next_available(self) -> bool:
        """
        切换到下一个可用代理。

        参数：无。
        返回值：bool，切换成功返回 True，无可用代理返回 False。
        内部逻辑：
            1. 若只有一个代理直接返回 False；
            2. 从 current_index 后开始遍历；
            3. 找到可用代理则更新 current_index。
        调用位置：ProxyManager.rotate、ensure_current_available 中调用。
        """
        now = time.time()
        if len(self.states) <= 1:
            return False
        for offset in range(1, len(self.states)):
            next_index = (self.current_index + offset) % len(self.states)
            if self.states[next_index].is_available(now):
                self.current_index = next_index
                return True
        return False

    def ensure_current_available(self) -> bool:
        """
        确保当前代理可用，否则尝试切换。

        参数：无。
        返回值：bool，当前可用或切换成功返回 True，否则 False。
        内部逻辑：若当前可用直接返回 True，否则调用 switch_to_next_available。
        调用位置：ProxyManager.ensure_current_available 中调用。
        """
        if self.is_current_available():
            return True
        return self.switch_to_next_available()

    def current_status_text(self) -> str:
        """
        生成当前代理状态文本。

        参数：无。
        返回值：str，包含当前代理名、可用数与冷却数的描述。
        内部逻辑：拼接 current_display_name、available_count、cooldown_count。
        调用位置：ProxyManager.current_proxy_status、BiliRequest 状态展示。
        """
        return (
            f"{self.current_display_name()} | "
            f"可用 {self.available_count()}/{len(self.states)} | "
            f"冷却 {self.cooldown_count()}"
        )

    def describe_all_states(self) -> str:
        """
        生成代理池整体状态文本。

        参数：无。
        返回值：str，逐条展示每个代理的可用或冷却状态。
        内部逻辑：
            1. 遍历所有 state；
            2. 当前代理附加"(当前)"标记；
            3. 可用显示"可用"，冷却显示剩余秒数。
        调用位置：ProxyManager.proxy_pool_status、调试日志中调用。
        """
        now = time.time()
        parts: list[str] = []
        for index, state in enumerate(self.states):
            label = state.display_name
            if index == self.current_index:
                label += "(当前)"
            if state.is_available(now):
                status = "可用"
            else:
                status = f"冷却 {state.cooldown_remaining(now)} 秒"
            parts.append(f"{label}:{status}")
        return "；".join(parts)
