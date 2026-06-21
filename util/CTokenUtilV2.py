import base64
import logging
import random
import time

logger = logging.getLogger("ctoken")


def _get_env_data():
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
    idx1 = t % 16
    idx2 = (3 * t) % 16
    return (env_data[idx1] + env_data[idx2] + 17 * t) & 255


class CTokenGeneratorV2:
    """对齐 BHYG-mainOld bilibili_util.py generate_ctoken"""

    def __init__(self, ticket_collection_t=0, time_offset=0, stay_time=0):
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
