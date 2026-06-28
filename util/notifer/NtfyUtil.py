"""
文件整体功能：通过 ntfy 服务发送通知，支持单次发送与后台线程重复推送。
所属模块：util.notifer
依赖文件：
    - util.notifer.Notifier.NotifierBase（通知器基类）
    - loguru（日志输出）
    - requests（HTTP 请求）
对外能力：
    1. 提供 send_message 发送单次 ntfy 通知；
    2. 提供 send_repeat_message / stop_notification 启动与停止重复通知线程；
    3. 提供 NtfyNotifier 类，统一接入 NotifierBase 的调度体系。
"""
import base64
import threading
import time

import loguru
import requests
from util.notifer.Notifier import NotifierBase

# 维护所有运行中的通知线程
_active_notification_threads = {}  # type: ignore
_thread_lock = threading.Lock()


class RepeatedNotifier(threading.Thread):
    """
    ntfy 重复通知后台线程。

    类设计作用：在指定持续时长内，按固定间隔循环调用 ntfy 接口发送通知，
                直至超时或收到停止信号。
    存储属性：
        server_url (str)：ntfy 服务器完整 URL（含 topic）。
        content (str)：通知正文模板。
        title (str | None)：通知标题模板。
        username (str | None)：Basic 认证用户名。
        password (str | None)：Basic 认证密码。
        interval_seconds (int)：两次发送之间的间隔秒数。
        duration_minutes (int)：最大持续分钟数。
        daemon (bool)：守护线程标志，主程序退出时自动结束。
        stop_event (threading.Event)：用于外部停止线程的事件对象。
        thread_id (str)：线程唯一标识。
    承担业务：抢票成功后持续提醒用户，避免错过支付窗口。
    """

    def __init__(
        self,
        server_url,
        content,
        title=None,
        username=None,
        password=None,
        interval_seconds=10,
        duration_minutes=5,
        thread_id=None,
    ):
        """
        初始化重复通知线程。

        参数：
            server_url (str)：ntfy 服务器 URL，例如 https://ntfy.sh/mytopic。
            content (str)：通知正文内容。
            title (str | None)：通知标题，默认为 None。
            username (str | None)：认证用户名，无认证可留空。
            password (str | None)：认证密码，无认证可留空。
            interval_seconds (int)：发送间隔秒数，默认 10。
            duration_minutes (int)：持续分钟数，默认 5。
            thread_id (str | None)：线程 ID，为 None 时自动生成。
        返回值：无。
        内部逻辑：调用父类初始化，设置守护线程与停止事件。
        调用位置：send_repeat_message 在启动重复通知时实例化。
        """
        super().__init__()
        self.server_url = server_url
        self.content = content
        self.title = title
        self.username = username
        self.password = password
        self.interval_seconds = interval_seconds
        self.duration_minutes = duration_minutes
        self.daemon = True  # 设置为守护线程，当主程序退出时自动结束
        self.stop_event = threading.Event()
        self.thread_id = thread_id or f"ntfy_{threading.get_ident()}"

    def run(self):
        """
        线程主循环，实现定时重复发送通知。

        参数：无。
        返回值：无。
        内部逻辑：
            1. 计算结束时间戳；
            2. 在未到超时且未收到停止信号时，构造带计数与剩余时间的消息；
            3. 调用 send_message 发送；
            4. 以 0.1 秒为步长检查停止事件，达到间隔后再次发送；
            5. 结束时从全局线程字典移除自身。
        调用位置：threading 框架在线程 start() 后自动调用。
        """
        start_time = time.time()
        end_time = start_time + (self.duration_minutes * 60)
        count = 0

        while time.time() < end_time and not self.stop_event.is_set():
            try:
                count += 1
                # 构建消息内容，包含计数和剩余时间
                remaining_minutes = int((end_time - time.time()) / 60)
                remaining_seconds = int((end_time - time.time()) % 60)
                message = f"{self.content} [#{count}, 剩余 {remaining_minutes}分{remaining_seconds}秒]"

                # 每次使用普通方法发送
                send_message(
                    self.server_url,
                    message,
                    f"{self.title} ({count}/{self.duration_minutes * 60 // self.interval_seconds})"
                    if self.title
                    else None,
                    self.username,
                    self.password,
                )

                # 等待指定的间隔时间或直到收到停止信号
                for _ in range(
                    int(self.interval_seconds * 10)
                ):  # 分成更小的步骤检查停止事件
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)

            except Exception as e:
                loguru.logger.error(f"重复通知发送失败: {e}")
                time.sleep(self.interval_seconds)  # 发生错误时仍然等待

        # 线程结束时从活动线程列表中移除
        with _thread_lock:
            if self.thread_id in _active_notification_threads:
                del _active_notification_threads[self.thread_id]

        loguru.logger.info(f"重复通知线程结束，共发送了{count}条通知")


def send_message(server_url, content, title=None, username=None, password=None):
    """
    向 ntfy 服务器发送单次通知。

    参数：
        server_url (str)：ntfy 服务器 URL，如 https://ntfy.sh/mytopic。
        content (str)：通知正文，支持任意文本。
        title (str | None)：通知标题，非 ASCII 字符会被替换为默认英文标题。
        username (str | None)：Basic 认证用户名，为 None 时不认证。
        password (str | None)：Basic 认证密码，为 None 时不认证。
    返回值：requests.Response，ntfy 服务器返回的响应对象。
    内部逻辑：
        1. 构造请求头，设置 Priority 为 5；
        2. 若标题非 ASCII，则替换为默认标题；
        3. 若提供用户名密码，生成 Basic Authorization；
        4. 以 UTF-8 编码发送正文。
    调用位置：RepeatedNotifier.run、NtfyNotifier.send_message、test_connection 中调用。
    """
    try:
        # 方法1: 不指定Content-Type，让服务器自动判断
        headers = {}

        # 设置最高优先级 (5)
        headers["Priority"] = "5"

        # 如果标题存在，处理中文标题
        if title:
            # 如果标题不是ASCII字符，则使用一个英文标题
            try:
                title.encode("ascii")
                headers["Title"] = title
            except UnicodeEncodeError:
                # 如果标题不是ASCII字符，则使用一个默认标题
                headers["Title"] = "Bili Ticket Notification"

        # 处理认证
        if username and password:
            auth = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {auth}"

        # 发送纯文本内容
        response = requests.post(
            server_url, headers=headers, data=content.encode("utf-8")
        )
        loguru.logger.info(f"Ntfy消息发送成功，状态码: {response.status_code}")
        return response
    except Exception as e:
        loguru.logger.error(f"Ntfy消息发送失败: {e}")
        raise


def send_repeat_message(
    server_url,
    content,
    title=None,
    username=None,
    password=None,
    interval_seconds=10,
    duration_minutes=5,
    thread_id=None,
):
    """
    在后台线程中重复发送 ntfy 通知。

    参数：
        server_url (str)：ntfy 服务器 URL。
        content (str)：通知正文内容。
        title (str | None)：通知标题。
        username (str | None)：认证用户名。
        password (str | None)：认证密码。
        interval_seconds (int)：发送间隔秒数，默认 10。
        duration_minutes (int)：持续分钟数，默认 5。
        thread_id (str | None)：线程 ID，为 None 时自动生成。
    返回值：str，启动的线程 ID，可用于后续停止通知。
    内部逻辑：
        1. 生成或复用 thread_id；
        2. 若同 ID 线程已存在，先调用 stop_notification 停止；
        3. 创建 RepeatedNotifier 并注册到全局字典；
        4. 启动线程并返回 thread_id。
    调用位置：抢票成功后需要持续提醒用户时调用。
    """
    thread_id = thread_id or f"ntfy_{time.time()}"

    # 如果已存在同ID的线程，先停止它
    stop_notification(thread_id)

    # 创建新的通知线程
    notifier = RepeatedNotifier(
        server_url,
        content,
        title,
        username,
        password,
        interval_seconds,
        duration_minutes,
        thread_id,
    )

    # 存储线程引用
    with _thread_lock:
        _active_notification_threads[thread_id] = notifier

    # 启动线程
    notifier.start()
    loguru.logger.info(
        f"启动重复通知线程 {thread_id}，间隔{interval_seconds}秒，持续{duration_minutes}分钟"
    )

    return thread_id


def stop_notification(thread_id):
    """
    停止指定 ID 的重复通知线程。

    参数：
        thread_id (str)：要停止的线程 ID。
    返回值：bool，若找到并发送停止信号则返回 True，否则 False。
    内部逻辑：在全局线程字典中查找 thread_id，设置其 stop_event。
    调用位置：用户手动停止通知或启动新线程覆盖旧线程时调用。
    """
    with _thread_lock:
        if thread_id in _active_notification_threads:
            _active_notification_threads[thread_id].stop_event.set()
            loguru.logger.info(f"已发送停止信号到通知线程 {thread_id}")
            return True
    return False


def test_connection(server_url, username=None, password=None):
    """
    测试 ntfy 服务连接是否正常。

    参数：
        server_url (str)：ntfy 服务器 URL。
        username (str | None)：认证用户名。
        password (str | None)：认证密码。
    返回值：tuple[bool, str]，第一个元素表示是否成功，第二个元素为提示信息。
    内部逻辑：
        1. 构造测试标题与认证头；
        2. 发送一条测试消息；
        3. 根据 HTTP 状态码返回测试结果；
        4. 捕获网络异常并返回失败信息。
    调用位置：用户在配置页面点击“测试连接”时调用。
    """
    try:
        headers = {
            "Title": "Test Connection",
        }

        # 处理认证
        if username and password:
            auth = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {auth}"

        # 方法1: 直接发送纯文本，不指定Content-Type
        response = requests.post(
            server_url,
            headers=headers,
            data="这是一个测试连接消息，如果收到说明连接正常。".encode("utf-8"),
            timeout=10,
        )

        if response.status_code in [200, 201, 202]:
            return True, "测试连接成功，已发送测试消息"
        else:
            return (
                False,
                f"测试连接失败，状态码: {response.status_code}, 响应: {response.text}",
            )

    except requests.RequestException as e:
        return False, f"连接失败: {str(e)}"
    except Exception as e:
        return False, f"测试过程中发生错误: {str(e)}"


class NtfyNotifier(NotifierBase):
    """
    Ntfy 通知器，统一接入 NotifierBase 调度体系。

    类设计作用：将 ntfy 推送包装成与 Bark、PushPlus 等渠道一致的接口，
                由 NotifierManager 统一启动与停止。
    存储属性：
        url (str)：ntfy 服务器完整 URL。
        username (str | None)：认证用户名。
        password (str | None)：认证密码。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送间隔，继承自 NotifierBase。
        duration_minutes (int)：持续时长，继承自 NotifierBase。
        stop_event (threading.Event)：继承自 NotifierBase，控制循环停止。
    承担业务：在 run 方法中循环发送带计数与剩余时间的 ntfy 通知。
    """

    def __init__(
        self,
        url,
        username=None,
        password=None,
        title="",
        content="",
        interval_seconds=15,
        duration_minutes=5,
    ):
        """
        初始化 NtfyNotifier。

        参数：
            url (str)：ntfy 服务器 URL。
            username (str | None)：认证用户名。
            password (str | None)：认证密码。
            title (str)：通知标题。
            content (str)：通知正文。
            interval_seconds (int)：发送间隔秒数，默认 15。
            duration_minutes (int)：持续分钟数，默认 5。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存 url、username、password。
        调用位置：NotifierManager.create_from_config 根据配置实例化时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.url = url
        self.username = username
        self.password = password

    def send_message(self, title, message):
        """
        发送单次 ntfy 通知。

        参数：
            title (str)：通知标题。
            message (str)：通知正文。
        返回值：无。
        内部逻辑：调用 send_message 模块函数，传入 url、message、title 与认证信息。
        调用位置：外部直接触发单次通知或 run 方法内部调用。
        """
        send_message(self.url, message, title, self.username, self.password)

    def run(self):
        """
        重写 run 方法，实现 Ntfy 特有的重复通知逻辑。

        参数：无。
        返回值：无。
        内部逻辑：
            1. 计算结束时间戳；
            2. 在未到超时且未收到停止信号时循环；
            3. 构造带计数与剩余时间的消息；
            4. 调用 self.send_message 发送；
            5. 以 0.1 秒为步长等待，随时响应 stop_event。
        调用位置：NotifierManager.start 启动通知线程时调用。
        """
        start_time = time.time()
        end_time = start_time + (self.duration_minutes * 60)
        count = 0

        while time.time() < end_time and not self.stop_event.is_set():
            try:
                count += 1
                # 构建消息内容，包含计数和剩余时间
                remaining_minutes = int((end_time - time.time()) / 60)
                remaining_seconds = int((end_time - time.time()) % 60)
                message = f"{self.content} [#{count}, 剩余 {remaining_minutes}分{remaining_seconds}秒]"

                # 使用send_message方法发送
                self.send_message(
                    f"{self.title} ({count}/{self.duration_minutes * 60 // self.interval_seconds})"
                    if self.title
                    else "Bili Ticket Notification",
                    message,
                )

                # 等待指定的间隔时间或直到收到停止信号
                for _ in range(
                    int(self.interval_seconds * 10)
                ):  # 分成更小的步骤检查停止事件
                    if self.stop_event.is_set():
                        break
                    time.sleep(0.1)

            except Exception as e:
                loguru.logger.error(f"Ntfy重复通知发送失败: {e}")
                time.sleep(self.interval_seconds)  # 发生错误时仍然等待

        loguru.logger.info(f"Ntfy重复通知完成，共发送了{count}条通知")
