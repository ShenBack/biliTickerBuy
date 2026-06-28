"""
文件整体功能：实现代理池耗尽后的指数退避策略，避免在代理不可用时疯狂重试。
所属模块：util.proxy
依赖文件：无外部依赖。
对外能力：
    提供 ProxyBackoff 类，用于计算下次重试等待时间、重置计数以及控制告警通知频率。
"""
from __future__ import annotations


class ProxyBackoff:
    """
    代理池耗尽退避计算器。

    类设计作用：当代理池全部进入冷却或不可用时，按指数退避延长等待时间，
                并通过 notification_sent 控制告警只触发一次。
    存储属性：
        base_seconds (int)：初始退避秒数。
        factor (float)：指数增长因子。
        max_seconds (int)：最大退避秒数上限。
        exhausted_rounds (int)：已连续触发代理池耗尽的次数。
        notification_sent (bool)：本轮是否已发送过耗尽告警。
    承担业务：在抢票任务中代理全部不可用时决定等待多久再试，并防止告警轰炸。
    """

    def __init__(
        self,
        *,
        base_seconds: int = 30,
        factor: float = 2.0,
        max_seconds: int = 600,
    ):
        """
        初始化退避计算器。

        参数：
            base_seconds (int)：初始退避秒数，默认 30。
            factor (float)：指数因子，默认 2.0。
            max_seconds (int)：最大退避秒数，默认 600。
        返回值：无。
        内部逻辑：对输入做基本校验与下限保护，初始化计数器与通知标志。
        调用位置：ProxyManager 或抢票任务初始化时创建。
        """
        self.base_seconds = max(1, int(base_seconds))
        self.factor = max(1.0, float(factor))
        self.max_seconds = max(self.base_seconds, int(max_seconds))
        self.exhausted_rounds = 0
        self.notification_sent = False

    def next_delay_seconds(self) -> int:
        """
        计算并返回下一次退避等待秒数。

        参数：无。
        返回值：int，本次建议等待秒数，受 max_seconds 上限约束。
        内部逻辑：按 base_seconds * factor^exhausted_rounds 计算，并将计数器加一。
        调用位置：检测到代理池全部不可用时调用，决定任务暂停时长。
        """
        delay = int(round(self.base_seconds * (self.factor**self.exhausted_rounds)))
        self.exhausted_rounds += 1
        return min(delay, self.max_seconds)

    def reset(self) -> None:
        """
        重置退避状态。

        参数：无。
        返回值：无。
        内部逻辑：将 exhausted_rounds 与 notification_sent 恢复为初始值。
        调用位置：代理池恢复可用或任务重新启动时调用。
        """
        self.exhausted_rounds = 0
        self.notification_sent = False

    def should_notify(self) -> bool:
        """
        判断本轮是否需要发送代理池耗尽告警。

        参数：无。
        返回值：bool，本轮首次返回 True，之后返回 False，直到 reset。
        内部逻辑：检查 notification_sent 标志，若为 False 则置为 True 并返回 True。
        调用位置：代理池全部不可用时，决定是否向用户/日志发送告警。
        """
        if self.notification_sent:
            return False
        self.notification_sent = True
        return True
