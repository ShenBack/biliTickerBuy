"""
文件整体功能：实现音频通知能力，抢票成功时播放本地音频文件。
所属模块：util.notifer
依赖文件：依赖 util.notifer.Notifier 中的 NotifierBase 基类。
对外能力：提供 AudioNotifier 类，调用 playsound3 播放指定音频作为通知。
"""

from util.notifer.Notifier import NotifierBase
import loguru


class AudioNotifier(NotifierBase):
    """
    音频通知器。

    类设计作用：在抢票成功等关键事件触发时，播放本地音频文件提醒用户。
    存储属性：
        audio_path (str)：待播放音频文件的路径。
        title (str)：推送标题，继承自 NotifierBase。
        content (str)：推送正文，继承自 NotifierBase。
        interval_seconds (int)：发送失败后的重试间隔。
        duration_minutes (int)：最大持续通知时长。
    承担业务：通过 playsound3 播放音频文件，音频只播放一次，不进入循环推送。
    """

    def __init__(
        self, audio_path, title="", content="", interval_seconds=10, duration_minutes=10
    ):
        """
        初始化音频通知器。

        参数：
            audio_path (str)：音频文件路径。
            title (str)：通知标题，默认空字符串。
            content (str)：通知正文，默认空字符串。
            interval_seconds (int)：重试间隔秒数，默认 10。
            duration_minutes (int)：持续通知时长（分钟），默认 10。
        返回值：无。
        内部逻辑：调用父类初始化通用属性，并保存音频路径。
        调用位置：NotifierManager.create_from_config 在检测到 audio_path 配置时调用。
        """
        super().__init__(title, content, interval_seconds, duration_minutes)
        self.audio_path = audio_path

    def send_message(self, title, message):
        """
        播放音频文件作为通知。

        参数：
            title (str)：通知标题（音频通知中不使用）。
            message (str)：通知正文（音频通知中不使用）。
        返回值：无。
        内部逻辑：动态导入 playsound3 并播放 self.audio_path，记录日志；异常时抛出。
        调用位置：run 方法中调用。
        """
        try:
            from playsound3 import playsound

            playsound(self.audio_path)
            loguru.logger.info(f"音频通知已播放: {self.audio_path}")
        except Exception as e:
            loguru.logger.error(f"音频播放失败: {e}")
            raise

    def run(self):
        """
        重写 run 方法，音频只播放一次，不需要循环。

        参数：无。
        返回值：无。
        内部逻辑：调用 send_message 播放音频，并记录完成日志；异常时记录错误。
        调用位置：NotifierBase.start 启动的守护线程中调用。
        """
        try:
            self.send_message(self.title, self.content)
            loguru.logger.info("音频通知播放完成")
        except Exception as e:
            loguru.logger.error(f"音频通知播放失败: {e}")
