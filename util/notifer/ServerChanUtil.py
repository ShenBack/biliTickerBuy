"""
文件整体功能：实现 Server 酱（ServerChan Turbo 与 ServerChan3）消息推送。
所属模块：util.notifer
依赖文件：
    - util.notifer.Notifier.NotifierBase（通知器基类）
    - requests（HTTP 请求）
    - json（JSON 序列化）
对外能力：
    1. 提供 ServerChanTurboNotifier，通过 sctapi.ftqq.com 发送 Turbo 版消息；
    2. 提供 ServerChan3Notifier，通过用户自定义 API 地址发送 ServerChan3 消息。
"""
import json
import requests

from util.notifer.Notifier import NotifierBase


class ServerChanTurboNotifier(NotifierBase):
    """
    Server 酱 Turbo 版推送器。

    类设计作用：通过 Server 酱 Turbo 官方接口（sctapi.ftqq.com）推送通知。
    存储属性：
        token (str)：Server 酱 Turbo 的 sendkey。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：抢票成功后将结果推送到用户微信。
    """

    def __init__(self, token, title, content, interval_seconds=10, duration_minutes=10):
        """
        初始化 ServerChanTurboNotifier。

        参数：
            token (str)：Server 酱 Turbo 的 sendkey。
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
        发送单次 Server 酱 Turbo 通知。

        参数：
            title (str)：通知标题。
            message (str)：通知正文。
        返回值：无。
        内部逻辑：
            1. 拼接 https://sctapi.ftqq.com/{token}.send 地址；
            2. 构造 JSON 负载，包含 title 与 desp；
            3. 通过 POST 发送请求。
        调用位置：外部直接触发单次通知或基类 run 循环中调用。
        """
        url = f"https://sctapi.ftqq.com/{self.token}.send"
        headers = {"Content-Type": "application/json"}

        data = {"desp": message, "title": title}
        requests.post(url, headers=headers, data=json.dumps(data))


class ServerChan3Notifier(NotifierBase):
    """
    Server 酱 3 版推送器。

    类设计作用：通过用户自定义的 ServerChan3 API 地址推送通知，
                适配新版 Server 酱的私有化或自定义部署场景。
    存储属性：
        api_url (str)：ServerChan3 的完整发送地址。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续推送时长。
    承担业务：抢票成功后将结果推送到用户微信或自定义端点。
    """

    def __init__(
        self, api_url, title, content, interval_seconds=10, duration_minutes=10
    ):
        """
        初始化 ServerChan3Notifier。

        参数：
            api_url (str)：ServerChan3 完整 API 地址。
            title (str)：通知标题。
            content (str)：通知正文。
            interval_seconds (int)：失败重试间隔秒数，默认 10。
            duration_minutes (int)：持续通知分钟数，默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存 api_url。
        调用位置：NotifierManager.create_from_config 根据配置实例化时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.api_url = api_url

    def send_message(self, title, message):
        """
        发送单次 ServerChan3 通知。

        参数：
            title (str)：通知标题。
            message (str)：通知正文。
        返回值：无。
        内部逻辑：
            1. 构造 JSON 负载，包含 title 与 desp；
            2. 通过 POST 请求发送到 self.api_url。
        调用位置：外部直接触发单次通知或基类 run 循环中调用。
        """
        headers = {"Content-Type": "application/json"}
        data = {"title": title, "desp": message}
        requests.post(self.api_url, headers=headers, data=json.dumps(data))
