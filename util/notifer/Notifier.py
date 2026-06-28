"""
文件整体功能：定义通知推送基类、配置数据类与管理器，统一各推送渠道的创建与生命周期。
所属模块：util.notifer
依赖文件：依赖 app_cmd.config.NotifierConfig 中的 NotifierConfig 数据类。
对外能力：
    1. 提供 NotifierBase 抽象基类，规范 send_message / run / start / stop 接口；
    2. 提供 NotifierConfig 数据类（兼容 app_cmd.config.NotifierConfig）；
    3. 提供 NotifierManager 统一管理多个推送器，支持从配置批量创建与测试。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import threading
import loguru
import time

from app_cmd.config.NotifierConfig import NotifierConfig


class NotifierBase(ABC):
    """
    推送器基类。

    类设计作用：定义所有推送通知渠道的公共接口与生命周期。
    存储属性：
        title (str)：推送标题。
        content (str)：推送正文。
        interval_seconds (int)：发送失败后的重试间隔；子类循环推送时也可作为循环间隔。
        duration_minutes (int)：允许持续推送的总时长，默认 10 分钟。
        stop_event (threading.Event)：用于控制推送线程停止的事件。
        thread (threading.Thread)：执行 run 方法的守护线程。
    承担业务：启动守护线程执行 run 方法，默认实现为发送成功一次即退出，
              失败则按 interval_seconds 重试直到超时或成功；子类可重写 run 实现循环推送。
    """

    def __init__(
        self,
        title: str,
        content: str,
        interval_seconds=10,
        duration_minutes=10,  # B站订单保存上限
    ):
        """
        初始化推送器基类。

        参数：
            title (str)：推送标题。
            content (str)：推送正文。
            interval_seconds (int)：失败重试间隔（秒），默认 10。
            duration_minutes (int)：持续推送时长（分钟），默认 10。
        返回值：无。
        内部逻辑：保存标题、正文、间隔与时长，创建 stop_event 并启动守护线程。
        调用位置：各推送器子类 __init__ 中通过 super().__init__ 调用。
        """
        super().__init__()
        self.title = title
        self.content = content
        self.interval_seconds = interval_seconds
        self.duration_minutes = duration_minutes
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        """
        线程运行函数，实现间隔发送通知。

        参数：无。
        返回值：无。
        内部逻辑：
            1. 计算结束时间；
            2. 在 duration_minutes 内循环调用 send_message；
            3. 发送成功则退出循环；发送失败则等待 interval_seconds 后重试；
            4. 超时或 stop_event 被设置时退出。
        调用位置：由守护线程自动调用，也可由子类重写。
        """
        start_time = time.time()
        end_time = start_time + (self.duration_minutes * 60)
        count = 0

        while time.time() < end_time and not self.stop_event.is_set():
            try:
                # 构建消息内容，包含剩余时间
                remaining_minutes = int((end_time - time.time()) / 60)
                remaining_seconds = int((end_time - time.time()) % 60)
                message = f"{self.content} [#{count}, 剩余 {remaining_minutes}分{remaining_seconds}秒]"

                # 使用send_message方法发送
                self.send_message(self.title, message)
                # 确认发送成功后停止发送
                break

            except Exception as e:
                loguru.logger.error(f"通知发送失败: {e}")
                time.sleep(self.interval_seconds)  # 发生错误时等待重试

        loguru.logger.info("通知发送成功")

    def start(self):
        """
        启动推送线程。

        参数：无。
        返回值：无。
        内部逻辑：若线程未存活，则清空 stop_event、重建线程并启动。
        调用位置：NotifierManager.start_all、start_notifier 或业务代码中调用。
        """
        if not self.thread.is_alive():
            self.stop_event.clear()
            self.thread = threading.Thread(target=self.run, daemon=True)
            self.thread.start()

    def stop(self):
        """
        停止推送线程。

        参数：无。
        返回值：无。
        内部逻辑：设置 stop_event，并等待线程最多 3 秒结束。
        调用位置：NotifierManager.stop_all、stop_notifier 或程序退出时调用。
        """
        self.stop_event.set()
        self.thread.join(timeout=3)

    @abstractmethod
    def send_message(self, title, message):
        """
        发送消息，子类必须实现。

        参数：
            title (str)：消息标题。
            message (str)：消息正文。
        返回值：无。
        内部逻辑：子类实现具体渠道（Bark、Server酱等）的 HTTP 推送逻辑。
        调用位置：run 方法中调用。
        """
        pass


@dataclass
class NotifierConfig:
    """
    推送配置统一管理数据类。

    类设计作用：集中声明所有推送渠道所需的配置字段，便于从配置数据库加载与传递。
    存储属性：
        serverchan_key (Optional[str])：Server酱 Turbo 的 key。
        serverchan3_api_url (Optional[str])：Server酱³ 的 API URL。
        pushplus_token (Optional[str])：PushPlus 的 token。
        bark_token (Optional[str])：Bark 的 key 或 URL。
        ntfy_url (Optional[str])：ntfy 服务 URL。
        ntfy_username (Optional[str])：ntfy 用户名。
        ntfy_password (Optional[str])：ntfy 密码。
        meow_nickname (Optional[str])：MeoW 用户标识。
        feishu_webhook (Optional[str])：飞书机器人 Webhook。
        audio_path (Optional[str])：音频通知文件路径。
        notify_proxy_exhausted (bool)：代理耗尽时是否通知。
    承担业务：作为 NotifierManager.create_from_config 与 test_all_notifiers 的输入，
              实现推送配置的集中管理。
    """

    serverchan_key: Optional[str] = None
    serverchan3_api_url: Optional[str] = None
    pushplus_token: Optional[str] = None
    bark_token: Optional[str] = None
    ntfy_url: Optional[str] = None
    ntfy_username: Optional[str] = None
    ntfy_password: Optional[str] = None
    meow_nickname: Optional[str] = None
    feishu_webhook: Optional[str] = None
    audio_path: Optional[str] = None
    notify_proxy_exhausted: bool = False

    @classmethod
    def from_config_db(cls):
        """
        从全局 ConfigDB 加载推送配置。

        参数：无。
        返回值：NotifierConfig 实例，字段值来自 ConfigDB。
        内部逻辑：从 util.ConfigDB 中读取各渠道配置键，构造并返回 NotifierConfig。
        调用位置：NotifierManager.test_all_notifiers 及业务代码初始化推送配置时调用。
        """
        from util import ConfigDB

        return cls(
            serverchan_key=ConfigDB.get("serverchanKey"),
            serverchan3_api_url=ConfigDB.get("serverchan3ApiUrl"),
            pushplus_token=ConfigDB.get("pushplusToken"),
            bark_token=ConfigDB.get("barkToken"),
            ntfy_url=ConfigDB.get("ntfyUrl"),
            ntfy_username=ConfigDB.get("ntfyUsername"),
            ntfy_password=ConfigDB.get("ntfyPassword"),
            meow_nickname=ConfigDB.get("meowNickname"),
            feishu_webhook=ConfigDB.get("feishuWebhook"),
            audio_path=ConfigDB.get("audioPath"),
            notify_proxy_exhausted=bool(ConfigDB.get("notifyProxyExhausted") or False),
        )


class NotifierManager:
    """
    推送器管理器。

    类设计作用：统一管理多个 NotifierBase 子类实例，负责注册、启动、停止、join 与测试。
    存储属性：
        notifier_dict (dict[str, NotifierBase])：名称到推送器实例的映射。
    承担业务：根据 NotifierConfig 批量创建各渠道推送器，控制它们的生命周期，
              并提供一键测试所有已配置渠道的能力。
    """

    def __init__(self):
        """
        初始化推送器管理器。

        参数：无。
        返回值：无。
        内部逻辑：创建空的 notifier_dict。
        调用位置：需要集中管理推送器的场景调用。
        """
        self.notifier_dict: dict[str, NotifierBase] = {}

    def register_notifier(self, name: str, notifier: NotifierBase):
        """
        注册推送器到管理器中。

        参数：
            name (str)：推送器名称（唯一键）。
            notifier (NotifierBase)：推送器实例。
        返回值：无。
        内部逻辑：若 name 已存在则记录错误并忽略，否则加入字典并记录成功日志。
        调用位置：create_from_config 批量创建推送器时调用。
        """
        if name in self.notifier_dict:
            loguru.logger.error(f"推送器添加失败: 已存在名为{name}的推送器")
        else:
            self.notifier_dict[name] = notifier
            loguru.logger.info(f"成功添加推送器: {name}")

    def remove_notifier(self, name: str):
        """
        从管理器中移除指定名称的推送器。

        参数：
            name (str)：推送器名称。
        返回值：无。
        内部逻辑：若 name 不存在则记录错误，否则从字典移除并记录成功日志。
        调用位置：动态调整推送器列表时调用。
        """
        if name not in self.notifier_dict:
            loguru.logger.error(f"推送器删除失败: 不存在名为{name}的推送器")
        else:
            self.notifier_dict.pop(name)
            loguru.logger.info(f"成功删除推送器: {name}")

    def start_all(self):
        """
        启动所有已注册推送器。

        参数：无。
        返回值：无。
        内部逻辑：遍历 notifier_dict，对每个推送器调用 start()。
        调用位置：抢票成功后需要同时触发所有通知渠道时调用。
        """
        for notifer in self.notifier_dict.values():
            notifer.start()

    def join_all(self, timeout: float = 15.0):
        """
        等待所有推送线程结束（或超时）。

        参数：
            timeout (float)：每个线程的最大等待秒数，默认 15。
        返回值：无。
        内部逻辑：遍历所有推送器，对其 thread 调用 join(timeout)。
                  由于推送线程是 daemon 线程，解释器退出时不会等待它们，
                  此方法给 HTTP 推送请求一个完成窗口，避免通知被中断。
        调用位置：抢票成功后、程序退出前调用。
        """
        for notifer in self.notifier_dict.values():
            notifer.thread.join(timeout=timeout)

    def stop_all(self):
        """
        停止所有已注册推送器。

        参数：无。
        返回值：无。
        内部逻辑：遍历 notifier_dict，对每个推送器调用 stop()。
        调用位置：需要中断所有通知时调用。
        """
        for notifer in self.notifier_dict.values():
            notifer.stop()

    def start_notifier(self, name: str):
        """
        启动指定名称的推送器。

        参数：
            name (str)：推送器名称。
        返回值：无。
        内部逻辑：从字典获取推送器并调用 start()，不存在则记录错误。
        调用位置：需要单独启动某个渠道时调用。
        """
        notifer = self.notifier_dict.get(name)
        if notifer:
            notifer.start()
        else:
            loguru.logger.error(f"推送器启动失败: 不存在名为{name}的推送器")

    def stop_notifier(self, name: str):
        """
        停止指定名称的推送器。

        参数：
            name (str)：推送器名称。
        返回值：无。
        内部逻辑：从字典获取推送器并调用 stop()，不存在则记录错误。
        调用位置：需要单独停止某个渠道时调用。
        """
        notifer = self.notifier_dict.get(name)
        if notifer:
            notifer.stop()
        else:
            loguru.logger.error(f"推送器停止失败: 不存在名为{name}的推送器")

    def list_notifiers(self):
        """
        返回当前已注册的推送器名称列表。

        参数：无。
        返回值：list[str]，推送器名称列表。
        内部逻辑：返回 list(self.notifier_dict.keys())。
        调用位置：管理界面展示已配置推送器时调用。
        """
        return list(self.notifier_dict.keys())

    @staticmethod
    def create_from_config(
        config: NotifierConfig,
        title: str,
        content: str,
        interval_seconds: int = 10,
        duration_minutes: int = 10,
        include_audio: bool = True,
    ) -> "NotifierManager":
        """
        通过配置创建 NotifierManager，统一的工厂方法。

        参数：
            config (NotifierConfig)：推送配置数据类。
            title (str)：通知标题。
            content (str)：通知正文。
            interval_seconds (int)：失败重试间隔（秒），默认 10。
            duration_minutes (int)：持续通知时长（分钟），默认 10。
            include_audio (bool)：是否包含音频通知，默认 True。
        返回值：NotifierManager，已根据配置注册相应推送器的管理器实例。
        内部逻辑：
            1. 创建空的 NotifierManager；
            2. 检查 config 中各渠道字段，非空则动态导入对应推送器类并实例化；
            3. 捕获导入与创建异常并记录日志；
            4. 返回管理器。
        调用位置：抢票成功后根据用户配置创建通知管理器时调用。
        """
        manager = NotifierManager()

        # ServerChan Turbo
        if config.serverchan_key:
            try:
                from util.notifer.ServerChanUtil import ServerChanTurboNotifier

                notifier = ServerChanTurboNotifier(
                    token=config.serverchan_key,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("ServerChanTurbo", notifier)
            except ImportError as e:
                loguru.logger.error(f"ServerChanTurbo导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"ServerChanTurbo创建失败: {e}")

        # ServerChan3
        if config.serverchan3_api_url:
            try:
                from util.notifer.ServerChanUtil import ServerChan3Notifier

                notifier = ServerChan3Notifier(
                    api_url=config.serverchan3_api_url,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("ServerChan3", notifier)
            except ImportError as e:
                loguru.logger.error(f"ServerChan3导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"ServerChan3创建失败: {e}")

        # PushPlus
        if config.pushplus_token:
            try:
                from util.proxy.PushPlusUtil import PushPlusNotifier

                notifier = PushPlusNotifier(
                    token=config.pushplus_token,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("PushPlus", notifier)
            except ImportError as e:
                loguru.logger.error(f"PushPlus导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"PushPlus创建失败: {e}")

        # Bark
        if config.bark_token:
            try:
                from util.notifer.BarkUtil import BarkNotifier

                notifier = BarkNotifier(
                    token=config.bark_token,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Bark", notifier)
            except ImportError as e:
                loguru.logger.error(f"Bark导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Bark创建失败: {e}")

        # Ntfy
        if config.ntfy_url:
            try:
                from util.notifer.NtfyUtil import NtfyNotifier

                notifier = NtfyNotifier(
                    url=config.ntfy_url,
                    username=config.ntfy_username,
                    password=config.ntfy_password,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Ntfy", notifier)
            except ImportError as e:
                loguru.logger.error(f"Ntfy导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Ntfy创建失败: {e}")

        # MeoW
        if config.meow_nickname:
            try:
                from util.notifer.MeoWUtil import MeoWNotifier

                notifier = MeoWNotifier(
                    nickname=config.meow_nickname,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("MeoW", notifier)
            except ImportError as e:
                loguru.logger.error(f"MeoW导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"MeoW创建失败: {e}")

        # Feishu
        if config.feishu_webhook:
            try:
                from util.FeishuUtil import FeishuNotifier

                notifier = FeishuNotifier(
                    webhook_url=config.feishu_webhook,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Feishu", notifier)
            except ImportError as e:
                loguru.logger.error(f"Feishu导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Feishu创建失败: {e}")

        # Audio
        if include_audio and config.audio_path:
            try:
                from util.notifer.AudioUtil import AudioNotifier

                notifier = AudioNotifier(
                    audio_path=config.audio_path,
                    title=title,
                    content=content,
                    interval_seconds=interval_seconds,
                    duration_minutes=duration_minutes,
                )
                manager.register_notifier("Audio", notifier)
            except ImportError as e:
                loguru.logger.error(f"Audio导入失败: {e}")
            except Exception as e:
                loguru.logger.error(f"Audio创建失败: {e}")

        return manager

    @staticmethod
    def test_all_notifiers(include_audio: bool = True) -> str:
        """
        测试所有已配置的推送渠道。

        参数：
            include_audio (bool)：是否测试音频通知，默认 True。
        返回值：str，各渠道测试结果的拼接文本。
        内部逻辑：
            1. 从 ConfigDB 加载 NotifierConfig；
            2. 使用 create_from_config 创建测试管理器；
            3. 遍历各渠道配置，对已创建的推送器调用 send_message；
            4. 收集成功/失败/未配置的结果并返回。
        调用位置：用户在设置界面点击“测试推送”时调用。
        """
        config = NotifierConfig.from_config_db()
        results = []

        # 使用统一的工厂方法创建测试管理器
        test_manager = NotifierManager.create_from_config(
            config=config,
            title="抢票提醒",
            content="测试推送",
            include_audio=include_audio,
        )

        # 测试每个已配置的推送渠道
        test_cases = [
            ("ServerChanTurbo", config.serverchan_key, "Server酱ᵀᵘʳᵇᵒ"),
            ("ServerChan3", config.serverchan3_api_url, "Server酱³"),
            ("PushPlus", config.pushplus_token, "PushPlus"),
            ("Bark", config.bark_token, "Bark"),
            ("Ntfy", config.ntfy_url, "Ntfy"),
            ("MeoW", config.meow_nickname, "MeoW"),
            ("Feishu", config.feishu_webhook, "飞书"),
        ]
        if include_audio:
            test_cases.append(("Audio", config.audio_path, "音频通知"))

        for notifier_name, config_value, display_name in test_cases:
            if not config_value:
                results.append(f"⚠️ {display_name}: 未配置")
                continue

            if notifier_name in test_manager.notifier_dict:
                try:
                    notifier = test_manager.notifier_dict[notifier_name]
                    notifier.send_message(
                        "🎫 抢票测试", f"这是一条{display_name}测试推送消息"
                    )
                    results.append(f"✅ {display_name}: 测试推送已发送")
                except Exception as e:
                    results.append(f"❌ {display_name}: 推送失败 - {str(e)}")
            else:
                results.append(f"❌ {display_name}: 创建失败")

        return "\n".join(results)
