"""
文件整体功能：实现飞书（Lark）机器人消息推送能力。
所属模块：util
依赖文件：依赖 util.notifer.Notifier 中的 NotifierBase 基类。
对外能力：提供 FeishuNotifier 类，可通过飞书自定义机器人 Webhook 发送卡片消息。
"""

import json
import requests
from util.notifer.Notifier import NotifierBase


class FeishuNotifier(NotifierBase):
    """
    飞书机器人推送器。

    类设计作用：将抢票成功等关键事件通过飞书自定义机器人 Webhook 推送到指定群聊。
    存储属性：
        webhook_url (str)：飞书自定义机器人的完整 Webhook 地址。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：构造 interactive 类型卡片消息，包含标题、正文和跳转按钮，
              并通过 HTTP POST 发送到飞书服务器。
    """

    def __init__(self, webhook_url, title, content, interval_seconds=10, duration_minutes=10):
        """
        初始化飞书推送器。

        参数：
            webhook_url (str)：飞书机器人 Webhook 地址。
            title (str)：推送标题。
            content (str)：推送正文。
            interval_seconds (int)：重试间隔秒数，默认 10。
            duration_minutes (int)：持续推送时长（分钟），默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存 webhook_url。
        调用位置：NotifierManager.create_from_config 在检测到飞书配置时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.webhook_url = webhook_url

    def send_message(self, title, message):
        """
        向飞书发送一次卡片消息。

        参数：
            title (str)：卡片标题。
            message (str)：卡片正文（支持 Markdown）。
        返回值：无。
        内部逻辑：
            1. 构造 interactive 类型消息卡片，包含标题、正文和“查看订单”按钮；
            2. 通过 requests.post 发送 JSON 负载；
            3. 校验 HTTP 状态码和业务 code，异常时抛出 Exception。
        调用位置：NotifierBase.run 或测试逻辑中调用。
        """
        headers = {"Content-Type": "application/json"}
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    },
                    "template": "red",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": message,
                    },
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": "查看订单",
                                },
                                "type": "primary",
                                "url": "https://mall.bilibili.com/neul/index.html?page=box_me&noTitleBar=1",
                            }
                        ],
                    }
                ],
            },
        }
        resp = requests.post(self.webhook_url, headers=headers, json=payload, timeout=10)
        if resp.status_code != 200:
            raise Exception(f"飞书推送失败: HTTP {resp.status_code}")
        result = resp.json()
        if result.get("code") != 0:
            raise Exception(f"飞书推送失败: {result.get('msg', '未知错误')}")
