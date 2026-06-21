import json
import requests
from util.notifer.Notifier import NotifierBase


class FeishuNotifier(NotifierBase):
    def __init__(self, webhook_url, title, content, interval_seconds=10, duration_minutes=10):
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.webhook_url = webhook_url

    def send_message(self, title, message):
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
