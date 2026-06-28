"""
文件整体功能：实现 PushPlus 消息推送。
所属模块：util.proxy（文件物理位置在 proxy 目录）
依赖文件：
    - util.notifer.Notifier.NotifierBase（通知器基类）
    - requests（HTTP 请求）
    - json（JSON 序列化）
对外能力：
    提供 PushPlusNotifier 类，通过 PushPlus 官方接口发送通知。
"""
import json
import requests

from util.notifer.Notifier import NotifierBase


class PushPlusNotifier(NotifierBase):
    """
    PushPlus 推送器。

    类设计作用：将抢票成功等关键事件通过 PushPlus 服务推送到用户微信。
    存储属性：
        token (str)：PushPlus 的用户 token。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：构造 JSON 负载并 POST 到 PushPlus 发送接口。
    """

    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        """
        初始化 PushPlusNotifier。

        参数：
            token (str)：PushPlus 用户 token。
            title (str)：通知标题。
            content (str)：通知正文。
            interval_seconds (int)：失败重试间隔秒数，默认 10。
            duration_minutes (int)：持续通知分钟数，默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存 token。
        调用位置：NotifierManager.create_from_config 根据配置实例化时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.token = token

    def send_message(self, title, message):
        """
        发送单次 PushPlus 通知。

        参数：
            title (str)：通知标题。
            message (str)：通知正文。
        返回值：无。
        内部逻辑：
            1. 指定 PushPlus 发送地址 http://www.pushplus.plus/send；
            2. 构造包含 token、title、content 的 JSON 负载；
            3. 通过 POST 发送请求。
        调用位置：外部直接触发单次通知或基类 run 循环中调用。
        """
        url = "http://www.pushplus.plus/send"
        headers = {"Content-Type": "application/json"}

        data = {"token": self.token, "content": message, "title": title}
        requests.post(url, headers=headers, data=json.dumps(data))
