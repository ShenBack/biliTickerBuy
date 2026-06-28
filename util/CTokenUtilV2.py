"""
文件整体功能：生成 B 站会员购下单流程所需的 ctoken 参数。
所属模块：util
依赖文件：无外部业务依赖，仅使用 Python 标准库（base64、logging、random、time）。
对外能力：提供 CTokenGeneratorV2 类，用于在 prepare/create 等阶段生成符合旧版 BHYG 字节布局的 ctoken 字符串。
"""

import base64
import logging
import random
import time

logger = logging.getLogger("ctoken")


def _get_env_data():
    """
    构造 ctoken 所需的环境数据数组。

    参数：无。
    返回值：list[int]，长度固定为 16 的整数列表，取值范围控制在 0-255 之间，
            包含随机生成的窗口尺寸、时间戳低位等环境模拟值。
    内部逻辑：直接返回预定义结构的多项随机值，用于后续 _m 函数计算校验字节。
    调用位置：仅被 CTokenGeneratorV2.generate_ctoken 方法调用。
    """
    return [
        0,
        0,
        random.randint(1000, 2000),
        random.randint(800, 1200),
        random.randint(1600, 2400),
        random.randint(800, 1200),
        0,
        0,
        random.randint(1600, 2400),
        random.randint(800, 1200),
        random.randint(1600, 2400),
        random.randint(10, 50),
        random.randint(100, 200),
        random.randint(50, 100),
        20,
        int(time.time() * 1000) % 256,
    ]


def _m(t, env_data):
    """
    根据环境数据计算单个校验字节。

    参数：
        t (int)：当前计算位置索引，参与取模运算。
        env_data (list[int])：长度为 16 的环境数据数组。
    返回值：int，0-255 范围内的校验字节值。
    内部逻辑：取 env_data 中第 t%16 项与第 (3*t)%16 项之和，加上 17*t 后按 255 掩码截断。
    调用位置：仅被 CTokenGeneratorV2.generate_ctoken 方法调用，用于生成 m1-m9 共 9 个校验字节。
    """
    idx1 = t % 16
    idx2 = (3 * t) % 16
    return (env_data[idx1] + env_data[idx2] + 17 * t) & 255


class CTokenGeneratorV2:
    """
    ctoken 生成器（V2 版本）。

    类设计作用：对齐旧版 BHYG bilibili_util.py 中的 generate_ctoken 实现，
                为 B 站会员购抢票流程生成可被服务端接受的 ctoken。
    存储属性：
        ticket_collection_t (int)：票品收藏/获取时间点（秒级或毫秒级，视调用方传入）。
        time_offset (float)：本地时间与 NTP 时间的偏移量，用于校准时间差。
        stay_time (int)：页面停留时长，create_v2 阶段用于计算 timer。
    承担业务：在 prepare 和 create 两种阶段下，按不同随机策略填充 touchend、visibilitychange、
              beforeunload、timer 等字段，最终编码为 Base64 字符串返回。
    """

    def __init__(self, ticket_collection_t=0, time_offset=0, stay_time=0):
        """
        初始化 CTokenGeneratorV2 实例。

        参数：
            ticket_collection_t (int)：票务收藏/进入时间戳，默认 0。
            time_offset (int/float)：时间偏移量，默认 0。
            stay_time (int)：停留时长，默认 0。
        返回值：无。
        内部逻辑：将传入参数保存为实例属性，供 generate_ctoken 阶段使用。
        调用位置：业务代码在创建订单前实例化本类时调用。
        """
        self.ticket_collection_t = ticket_collection_t
        self.time_offset = time_offset
        self.stay_time = stay_time

    def generate_ctoken(
        self,
        is_create_v2: bool = False,
        touchend: int = -1,
        visibilitychange: int = -1,
        beforeunload: int = -1,
        open_window: int = -1,
        ticket_collection_t: int = 0,
    ) -> str:
        """
        生成 ctoken 字符串。

        参数：
            is_create_v2 (bool)：是否为 create_v2 阶段，True 时使用真实时间差计算 timer。
            touchend (int)：触摸结束事件值，-1 表示使用默认值。
            visibilitychange (int)：页面可见性变化事件值，-1 表示使用默认值。
            beforeunload (int)：页面卸载事件值，-1 表示使用默认值。
            open_window (int)：打开窗口事件值，-1 表示未指定。
            ticket_collection_t (int)：本次生成时覆盖的收藏时间戳，0 则使用 self.ticket_collection_t。
        返回值：str，Base64 编码后的 ctoken 字符串。
        内部逻辑：
            1. 生成环境数据并记录日志；
            2. 根据 is_create_v2 决定各事件字段的默认值；
            3. 计算 m1-m9 校验字节；
            4. 按固定字节布局拼接 token_bytes，对可能溢出的字段做截断保护；
            5. Base64 编码并返回。
        调用位置：抢票任务在 prepare/create 请求前调用，用于填充 token 参数。
        """
        env_data = _get_env_data()

        logger.info(f"[ctoken] 输入参数: is_create_v2={is_create_v2}, "
                     f"touchend={touchend}, visibilitychange={visibilitychange}, "
                     f"beforeunload={beforeunload}, open_window={open_window}, "
                     f"ticket_collection_t={ticket_collection_t}, "
                     f"self.ticket_collection_t={self.ticket_collection_t}, "
                     f"self.time_offset={self.time_offset}, self.stay_time={self.stay_time}")
        logger.info(f"[ctoken] env_data={env_data}")

        # 默认值：与 BHYG-mainOld 一致
        if touchend == -1:
            touchend = random.randint(30, 50)
        if visibilitychange == -1:
            visibilitychange = random.randint(10, 50)
        if beforeunload == -1:
            if open_window != -1:
                beforeunload = open_window
            else:
                beforeunload = random.randint(10, 50)
        timer = random.randint(1, 10)

        if is_create_v2:
            time_difference = int(
                time.time() + self.time_offset - self.ticket_collection_t
            )
            timer = int(time_difference + self.stay_time)
            beforeunload = 25
        else:
            # prepare 阶段，与 BHYG-mainOld api.py L728-732 对齐
            touchend = random.randint(1, 5)
            beforeunload = random.randint(1, 3)
            open_window = random.randint(1, 3)
            # open_window 覆盖 beforeunload
            if open_window != -1:
                beforeunload = open_window

        # m1-m9 使用环境数据生成
        m1 = _m(1, env_data)
        m2 = _m(2, env_data)
        m3 = _m(3, env_data)
        m4 = _m(4, env_data)
        m5 = _m(5, env_data)
        m6 = _m(6, env_data)
        m7 = _m(7, env_data)
        m8 = _m(8, env_data)
        m9 = _m(9, env_data)

        logger.info(f"[ctoken] 最终值: touchend={touchend}, visibilitychange={visibilitychange}, "
                     f"beforeunload={beforeunload}, timer={timer}, "
                     f"m1={m1}, m2={m2}, m3={m3}, m4={m4}, m5={m5}, "
                     f"m6={m6}, m7={m7}, m8={m8}, m9={m9}")

        # 编码：完全对齐 BHYG-mainOld 字节布局
        token_bytes = b""

        token_bytes += m1.to_bytes(1, "big") + b"\x00"

        try:
            token_bytes += touchend.to_bytes(1, "big") + b"\x00"
        except OverflowError:
            token_bytes += b"\xff\x00"

        token_bytes += m2.to_bytes(1, "big") + b"\x00"

        try:
            token_bytes += visibilitychange.to_bytes(1, "big") + b"\x00"
        except OverflowError:
            token_bytes += b"\xff\x00"

        token_bytes += m3.to_bytes(1, "big") + b"\x00"
        token_bytes += m4.to_bytes(1, "big") + b"\x00"

        try:
            token_bytes += beforeunload.to_bytes(1, "big") + b"\x00"
        except OverflowError:
            token_bytes += b"\xff\x00"

        token_bytes += m5.to_bytes(1, "big") + b"\x00"

        try:
            temp_timer = timer.to_bytes(2, "big")
            token_bytes += temp_timer[0].to_bytes(1, "big") + b"\x00"
            token_bytes += temp_timer[1].to_bytes(1, "big") + b"\x00"
        except OverflowError:
            token_bytes += b"\xff\x00\xff\x00"

        try:
            tct = int(ticket_collection_t if ticket_collection_t else self.ticket_collection_t)
            temp_tct = tct.to_bytes(2, "big")
            token_bytes += temp_tct[0].to_bytes(1, "big") + b"\x00"
            token_bytes += temp_tct[1].to_bytes(1, "big") + b"\x00"
        except OverflowError:
            token_bytes += b"\xff\x00\xff\x00"

        token_bytes += m6.to_bytes(1, "big") + b"\x00"
        token_bytes += m7.to_bytes(1, "big") + b"\x00"
        token_bytes += m8.to_bytes(1, "big") + b"\x00"
        token_bytes += m9.to_bytes(1, "big") + b"\x00"

        result = base64.b64encode(token_bytes).decode("utf-8")
        logger.info(f"[ctoken] 字节流({len(token_bytes)}字节): {token_bytes.hex()}")
        logger.info(f"[ctoken] Base64结果: {result}")
        return result
