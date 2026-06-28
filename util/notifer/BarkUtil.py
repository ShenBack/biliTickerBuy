"""
文件整体功能：实现 Bark 推送通知能力。
所属模块：util.notifer
依赖文件：依赖 util.notifer.Notifier 中的 NotifierBase 基类。
对外能力：提供 BarkNotifier 类，可通过 Bark 服务向 iOS 设备推送通知。
"""

import json
import requests

from urllib.parse import urlparse
from util.notifer.Notifier import NotifierBase


class BarkNotifier(NotifierBase):
    """
    Bark 推送器。

    类设计作用：将抢票成功等关键事件通过 Bark 服务推送到 iOS 设备。
    存储属性：
        token (str)：Bark 的 key 或完整的 Bark 服务端 URL。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：构造 Bark 推送 URL 与 JSON 负载，通过 HTTP POST 发送通知。
    """

    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        """
        初始化 Bark 推送器。

        参数：
            token (str)：Bark key 或完整 Bark URL。
            title (str)：推送标题。
            content (str)：推送正文。
            interval_seconds (int)：重试间隔秒数，默认 10。
            duration_minutes (int)：持续推送时长（分钟），默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存 token。
        调用位置：NotifierManager.create_from_config 在检测到 bark_token 配置时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.token = token

    def send_message(self, title, message):
        """
        发送一次 Bark 推送通知。

        参数：
            title (str)：推送标题。
            message (str)：推送正文。
        返回值：无。
        内部逻辑：
            1. 构造包含图标、分组、跳转链接、铃声、级别、音量的 JSON 数据；
            2. 判断 token 是否为完整 URL，否则使用默认 api.day.app 域名拼接；
            3. 通过 requests.post 发送通知。
        调用位置：NotifierBase.run 中调用。
        """
        headers = {"Content-Type": "application/json"}
        data = {
            "icon": "https://raw.githubusercontent.com/mikumifa/biliTickerBuy/refs/heads/main/assets/icon.ico",  # 推送LOGO
            "group": "biliTickerBuy",
            "url": "https://mall.bilibili.com/neul/index.html?page=box_me&noTitleBar=1",  # 跳转会员购链接
            "sound": "telegraph",  # 警告铃声
            "level": "critical",  # 重要警告
            "volume": "10",
        }
        if isinstance(self.token, str) and urlparse(self.token).scheme in {
            "http",
            "https",
        }:
            url = f"{self.token.rstrip('/')}/{title}/{message}"
        else:
            url = f"https://api.day.app/{self.token}/{title}/{message}"

        requests.post(url, headers=headers, data=json.dumps(data))
