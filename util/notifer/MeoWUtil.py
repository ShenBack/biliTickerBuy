"""
文件整体功能：实现 MeoW 推送通知能力。
所属模块：util.notifer
依赖文件：
    - util.Constant 中的 MEOW_API_BASE
    - util.notifer.Notifier 中的 NotifierBase
对外能力：提供 MeoWNotifier 类，可通过 MeoW 服务发送推送通知。
"""

import requests

from util.Constant import MEOW_API_BASE
from util.notifer.Notifier import NotifierBase


class MeoWNotifier(NotifierBase):
    """
    MeoW 推送器。

    类设计作用：将抢票成功等关键事件通过 MeoW 服务推送到用户设备。
    存储属性：
        nickname (str)：MeoW 用户标识（已去除首尾斜杠和空白）。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：向 MeoW API 发送 JSON 请求，校验响应状态后完成通知。
    """

    def __init__(
        self,
        nickname,
        title,
        content,
        interval_seconds=10,
        duration_minutes=10,
    ):
        """
        初始化 MeoW 推送器。

        参数：
            nickname (str)：MeoW 用户昵称/标识。
            title (str)：推送标题。
            content (str)：推送正文。
            interval_seconds (int)：重试间隔秒数，默认 10。
            duration_minutes (int)：持续推送时长（分钟），默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存清洗后的 nickname。
        调用位置：NotifierManager.create_from_config 在检测到 meow_nickname 配置时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.nickname = str(nickname or "").strip().strip("/")

    def send_message(self, title, message):
        """
        发送一次 MeoW 推送通知。

        参数：
            title (str)：推送标题。
            message (str)：推送正文。
        返回值：无。
        内部逻辑：
            1. 检查 nickname 是否为空；
            2. 向 MEOW_API_BASE/nickname 发送 POST 请求，JSON 体包含 title 和 msg；
            3. 校验 HTTP 状态与业务 status 字段，异常时抛出 RuntimeError。
        调用位置：NotifierBase.run 中调用。
        """
        if not self.nickname:
            raise ValueError("MeoW nickname is required")

        response = requests.post(
            f"{MEOW_API_BASE}/{self.nickname}",
            json={"title": title, "msg": message},
            timeout=10,
        )
        response.raise_for_status()

        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeError(f"MeoW response is not JSON: {response.text}") from exc

        if data.get("status") != 200:
            raise RuntimeError(f"MeoW push failed: {data}")
