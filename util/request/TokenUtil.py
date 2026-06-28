"""
文件整体功能：生成 B 站会员购抢票流程中使用的 token 参数。
所属模块：util.request
依赖文件：无外部业务依赖，仅使用 Python 标准库（base64、time）。
对外能力：
    提供 generate_token 函数，按固定字节布局生成经过 Base64 与字符映射的 token 字符串。
"""
import base64
import time


_BASE64_STD_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789/+="
)
_BASE64_TOKEN_ALPHABET = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-."
)


def generate_token(
    project_id: int,
    screen_id: int,
    order_type: int,
    count: int,
    sku_id: int,
    ts: int | None = None,
) -> str:
    """
    生成 B 站会员购下单 token。

    参数：
        project_id (int)：项目 ID。
        screen_id (int)：场次 ID。
        order_type (int)：订单类型。
        count (int)：购买数量。
        sku_id (int)：SKU ID。
        ts (int | None)：可选时间戳，为 None 时使用当前时间。
    返回值：str，编码后的 token 字符串。
    内部逻辑：
        1. 按 1 byte header + 4 bytes timestamp + 4 bytes project_id +
           4 bytes screen_id + 1 byte order_type + 2 bytes count +
           4 bytes sku_id 的字节布局构造二进制 token；
        2. 使用标准 Base64 编码；
        3. 将 +/= 字符映射为 _-.，得到最终字符串。
    调用位置：B 站会员购 prepare/create 等下单阶段构造 token 时调用。
    """

    timestamp = int(time.time()) if ts is None else int(ts)
    token = bytes([0xC0])
    token += timestamp.to_bytes(4, "big")
    token += int(project_id).to_bytes(4, "big")
    token += int(screen_id).to_bytes(4, "big")
    token += int(order_type).to_bytes(1, "big")
    token += int(count).to_bytes(2, "big")
    token += int(sku_id).to_bytes(4, "big")

    encoded = base64.b64encode(token).decode("ascii")
    return encoded.translate(
        str.maketrans(_BASE64_STD_ALPHABET, _BASE64_TOKEN_ALPHABET)
    )
