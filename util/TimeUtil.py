"""
文件整体功能：提供带时间偏移的毫秒时间戳获取与 NTP 时间同步能力。
所属模块：util
依赖文件：无外部业务依赖，使用 ntplib 与 loguru。
对外能力：提供 current_time_ms 函数与 TimeUtil 类，用于本地时间校准和偏移量管理。
"""

import time

import ntplib
from loguru import logger


def current_time_ms(*, timeoffset: float = 0, base_ms: int | None = None) -> int:
    """
    获取经过 timeoffset 修正后的毫秒时间戳。

    参数：
        timeoffset (float)：时间偏移量，单位为秒，默认 0。
        base_ms (int | None)：基准毫秒时间戳，None 则取当前系统时间。
    返回值：int，修正后的毫秒时间戳。
    内部逻辑：若未提供 base_ms，则使用 time.time() * 1000 获取当前毫秒时间，
              再加上 timeoffset 换算后的毫秒偏移量并取整。
    调用位置：抢票任务中需要精确时间（如倒计时、token 生成）时调用。
    """
    if base_ms is None:
        base_ms = int(time.time() * 1000)
    return int(base_ms + timeoffset * 1000)


class TimeUtil:
    """
    NTP 时间同步工具类。

    类设计作用：通过 NTP 服务器获取网络标准时间，计算并维护本地时钟偏移量。
    存储属性：
        ntp_server (str)：NTP 服务器地址，默认 ntp.aliyun.com。
        client (ntplib.NTPClient)：NTP 客户端实例。
        timeoffset (float)：本地时间与 NTP 时间的偏移量，单位秒；正数表示本地时间偏慢。
    承担业务：为抢票流程提供统一的时间基准，减少因本地时钟不准导致的开票时机偏差。
    """

    def __init__(self, _ntp_server="ntp.aliyun.com") -> None:
        """
        初始化时间同步工具。

        参数：
            _ntp_server (str)：NTP 服务器地址，默认 "ntp.aliyun.com"。
        返回值：无。
        内部逻辑：保存服务器地址并创建 NTPClient 实例，初始化 timeoffset 为 0。
        调用位置：util/__init__.py 中创建全局 time_service 时调用。
        """
        self.ntp_server = _ntp_server
        self.client = ntplib.NTPClient()
        self.timeoffset: float = 0

    def compute_timeoffset(self) -> str:
        """
        计算本地时间与 NTP 服务器时间的偏移量。

        参数：无。
        返回值：str，格式化到 5 位小数的偏移秒数字符串；失败返回 "error"。
        内部逻辑：
            1. 最多重试 3 次请求 NTP 服务器；
            2. 成功后取 response.offset 的相反数并格式化；
            3. 超时或异常时返回 "error"。
        调用位置：程序启动时或需要重新校准时调用，如 util/__init__.py。
        """
        response = None
        for i in range(0, 3):
            try:
                response = self.client.request(self.ntp_server, version=4)
                break
            except Exception:
                logger.warning("第" + str(i + 1) + "次获取NTP时间失败, 尝试重新获取")
                if i == 2:
                    return "error"
                time.sleep(0.5)
        if response is None:
            logger.error("无法获取NTP时间")
            return "error"
        # response.offset 为[NTP时钟源 - 设备时钟]的偏差, 使用时需要取反
        return format(-(response.offset), ".5f")

    def set_timeoffset(self, _timeoffset: str) -> None:
        """
        设置时间偏移量。

        参数：
            _timeoffset (str)：由 compute_timeoffset 返回的偏移秒数字符串，
                              或为 "error" 表示同步失败。
        返回值：无。
        内部逻辑：若传入 "error" 则将 timeoffset 置 0 并使用本地时间；否则转换为 float 保存。
        调用位置：NTP 校准后调用，如 util/__init__.py 中初始化 time_service 时。
        """
        if _timeoffset == "error":
            self.timeoffset = 0
            logger.warning("NTP时间同步失败, 使用本地时间")
        else:
            self.timeoffset = float(_timeoffset)

    def get_timeoffset(self) -> float:
        """
        获取当前时间偏移量。

        参数：无。
        返回值：float，当前保存的时间偏移量，单位秒。
        内部逻辑：直接返回 self.timeoffset。
        调用位置：业务代码需要获取本地与 NTP 时间偏差时调用。
        """
        return self.timeoffset
