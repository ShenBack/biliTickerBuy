"""
文件整体功能：util 包初始化与全局运行时状态管理。
所属模块：util（包入口）
依赖文件：
    - util.Storage.KVDatabase
    - util.log.LogConfig
    - util.TimeUtil
    - util.ErrorCodes
    - util.request.BiliRequest
对外能力：
    1. 计算并暴露 EXE_PATH、TEMP_PATH、LOG_DIR、CONFIG_DB_PATH、GLOBAL_COOKIE_PATH 等路径常量；
    2. 初始化全局日志配置；
    3. 创建全局 ConfigDB、main_request、time_service；
    4. 提供 TaskLogEntry、RuntimeStateStore、GlobalStatus 等运行时状态数据结构；
    5. 提供 runtime_state_reader / runtime_state_writer 装饰器，用于读写全局运行时状态。
"""

from dataclasses import dataclass, field
from functools import wraps
import os
import re
import sys
import time
from typing import Any, Callable
import loguru
from util.Storage.KVDatabase import KVDatabase
from util.log.LogConfig import loguru_config
from util.TimeUtil import TimeUtil
from util.ErrorCodes import ERRNO_DICT
from util.request.BiliRequest import BiliRequest


def get_application_path() -> str:
    """
    获取应用程序根目录。

    参数：无。
    返回值：str，应用根目录的绝对路径。
    内部逻辑：
        - 若处于 PyInstaller 打包环境（sys.frozen 为 True），优先使用 sys._MEIPASS；
        - 否则取当前文件的上级目录作为项目根目录。
    调用位置：RandomMessages 等需要定位资源文件的地方调用。
    """
    if getattr(sys, "frozen", False):
        application_path = getattr(
            sys,
            "_MEIPASS",
            os.path.abspath(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
        )
    else:
        application_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    return application_path


def get_exec_path() -> str:
    """
    获取程序执行目录。

    参数：无。
    返回值：str，可执行文件或脚本所在的目录绝对路径。
    内部逻辑：
        - 若通过 python main.py 运行且 argv[0] 以 .py 结尾，返回项目根目录；
        - 否则返回 sys.executable 所在目录，适配打包后的 exe 场景。
    调用位置：本文件初始化 EXE_PATH 时调用。
    """
    if len(sys.argv[0]) > 0 and sys.argv[0].endswith(
        ".py"
    ):  # sometime, argv[0] of `python main.py` is main.py
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    else:
        return os.path.dirname(os.path.realpath(sys.executable))


EXE_PATH: str = get_exec_path()  # 应用目录


def get_application_tmp_path() -> str:
    """
    获取并确保应用临时目录存在。

    参数：无。
    返回值：str，应用临时目录的绝对路径。
    内部逻辑：在 EXE_PATH 下创建 tmp 目录（若不存在）。
    调用位置：本文件初始化 TEMP_PATH 时调用。
    """
    os.makedirs(os.path.join(EXE_PATH, "tmp"), exist_ok=True)
    return os.path.join(EXE_PATH, "tmp")


TEMP_PATH: str = get_application_tmp_path()  # 临时目录
os.environ["GRADIO_TEMP_DIR"] = TEMP_PATH
LOG_DIR: str = os.environ.get("BTB_LOG_DIR", os.path.join(EXE_PATH, "btb_logs"))
os.makedirs(LOG_DIR, exist_ok=True)
log_file_name = os.environ.get("BTB_APP_LOG_NAME", "app.log")
log_file_name = re.sub(r"[^\w.\-]", "_", log_file_name) or "app.log"
loguru_config(LOG_DIR, log_file_name, enable_console=True, file_colorize=False)

__all__ = [
    "TEMP_PATH",
    "EXE_PATH",
    "ERRNO_DICT",
    "ConfigDB",
    "GLOBAL_COOKIE_PATH",
    "main_request",
    "set_main_request",
    "time_service",
    "LOG_DIR",
    "GlobalStatusInstance",
    "runtime_state_reader",
    "runtime_state_writer",
]
loguru.logger.debug(f"设置路径EXE_PATH={EXE_PATH}")
CONFIG_DB_PATH = os.environ.get(
    "BTB_CONFIG_PATH", os.path.join(EXE_PATH, "config.json")
)
GLOBAL_COOKIE_PATH = os.environ.get(
    "BTB_COOKIES_PATH", os.path.join(EXE_PATH, "cookies.json")
)
ConfigDB = KVDatabase(CONFIG_DB_PATH)
if ConfigDB.get("cookies_path") is None:
    ConfigDB.insert("cookies_path", GLOBAL_COOKIE_PATH)
main_request = BiliRequest(cookies_config_path=ConfigDB.get("cookies_path"))


def set_main_request(request):
    """
    替换全局 main_request 实例。

    参数：
        request (BiliRequest)：新的 BiliRequest 实例。
    返回值：无。
    内部逻辑：通过 global 关键字覆盖模块级 main_request 变量。
    调用位置：需要在运行时切换全局请求实例的场景（如登录后重建 session）。
    """
    global main_request
    main_request = request


time_service = TimeUtil()
time_service.set_timeoffset(time_service.compute_timeoffset())


@dataclass
class TaskLogEntry:
    """
    单个任务日志条目。

    类设计作用：记录一次抢票任务的元数据，用于任务列表展示与状态跟踪。
    存储属性：
        title (str)：任务标题。
        mode (str)：任务模式。
        log_file (str)：日志文件路径。
        created_at (float)：创建时间戳。
        pid (int | None)：进程 ID，可选。
        status (str)：任务状态，默认 "运行中"。
        finished_at (float | None)：结束时间戳，可选。
        payment_qr_url (str | None)：支付二维码 URL，可选。
    承担业务：作为 GlobalStatus.task_logs 列表的元素，支撑任务管理界面。
    """

    title: str
    mode: str
    log_file: str
    created_at: float
    pid: int | None = None
    status: str = "运行中"
    finished_at: float | None = None
    payment_qr_url: str | None = None


@dataclass
class RuntimeStateStore:
    """
    运行时状态存储器。

    类设计作用：以字典形式保存 Gradio / 任务间共享的运行时数据，支持普通值和文件路径列表。
    存储属性：
        values (dict[str, Any])：底层键值存储字典。
    承担业务：为 GlobalStatus 提供运行时状态读写能力，包括路径列表的校验与截断。
    """

    values: dict[str, Any] = field(default_factory=dict)

    def set(self, key: str, value: Any) -> None:
        """
        设置普通键值。

        参数：
            key (str)：状态键名。
            value (Any)：要保存的值。
        返回值：无。
        内部逻辑：直接写入 self.values 字典。
        调用位置：GlobalStatus.state_set 及装饰器 runtime_state_writer 中调用。
        """
        self.values[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取普通键值。

        参数：
            key (str)：状态键名。
            default (Any)：键不存在时返回的默认值。
        返回值：Any，保存的值或 default。
        内部逻辑：使用 dict.get 取值。
        调用位置：GlobalStatus.state_get 及装饰器 runtime_state_reader 中调用。
        """
        return self.values.get(key, default)

    def delete(self, key: str) -> None:
        """
        删除指定键。

        参数：
            key (str)：状态键名。
        返回值：无。
        内部逻辑：调用 dict.pop 删除键，键不存在时静默处理。
        调用位置：GlobalStatus.state_delete 中调用。
        """
        self.values.pop(key, None)

    def set_path_list(self, key: str, files: list[str] | None, limit: int = 50) -> None:
        """
        设置文件路径列表，会自动过滤无效路径并按限制截断。

        参数：
            key (str)：状态键名。
            files (list[str] | None)：文件路径列表，None 则视为空列表。
            limit (int)：最大保留路径数，默认 50。
        返回值：无。
        内部逻辑：遍历 files，跳过非字符串或文件不存在的路径，最后保留前 limit 项。
        调用位置：GlobalStatus.state_set_path_list 及 runtime_state_writer 装饰器中调用。
        """
        normalized: list[str] = []
        for file in files or []:
            if not file or not isinstance(file, str):
                continue
            if not os.path.exists(file):
                continue
            normalized.append(file)
        self.values[key] = normalized[:limit]

    def get_path_list(self, key: str, limit: int = 50) -> list[str]:
        """
        获取文件路径列表，会再次校验文件是否存在。

        参数：
            key (str)：状态键名。
            limit (int)：最大返回路径数，默认 50。
        返回值：list[str]，经过校验和截断后的有效路径列表。
        内部逻辑：从 values 中取值，过滤非字符串和不存在文件，截断后回写并返回副本。
        调用位置：GlobalStatus.state_get_path_list 及 runtime_state_reader 装饰器中调用。
        """
        files = self.values.get(key, [])
        if not isinstance(files, list):
            return []
        normalized = [
            file for file in files if isinstance(file, str) and os.path.exists(file)
        ][:limit]
        self.values[key] = normalized
        return list(normalized)


@dataclass
class GlobalStatus:
    """
    全局状态单例数据结构。

    类设计作用：在整个进程内维护当前任务、任务日志、运行时状态及代理使用情况，
                供 Gradio 界面和任务逻辑共享访问。
    存储属性：
        nowTask (str)：当前任务标识，默认 "none"。
        task_logs (list[TaskLogEntry])：任务日志条目列表。
        runtime_state (RuntimeStateStore)：运行时状态存储实例。
        proxy_usage (dict[str, list[str]])：代理到任务名称列表的映射。
    承担业务：
        - 注册/更新/查询任务日志；
        - 代理使用情况的注册与注销；
        - 运行时状态的读写代理。
    """

    nowTask: str = "none"
    task_logs: list[TaskLogEntry] = field(default_factory=list)
    runtime_state: RuntimeStateStore = field(default_factory=RuntimeStateStore)
    proxy_usage: dict[str, list[str]] = field(default_factory=dict)  # proxy -> [task_names]

    def state_set(self, key: str, value: Any) -> None:
        """
        设置运行时状态的普通键值。

        参数：
            key (str)：状态键名。
            value (Any)：要保存的值。
        返回值：无。
        内部逻辑：委托给 self.runtime_state.set。
        调用位置：需要修改全局运行时状态的地方调用。
        """
        self.runtime_state.set(key, value)

    def state_get(self, key: str, default: Any = None) -> Any:
        """
        获取运行时状态的普通键值。

        参数：
            key (str)：状态键名。
            default (Any)：键不存在时的默认值。
        返回值：Any，保存的值或 default。
        内部逻辑：委托给 self.runtime_state.get。
        调用位置：需要读取全局运行时状态的地方调用。
        """
        return self.runtime_state.get(key, default)

    def state_delete(self, key: str) -> None:
        """
        删除运行时状态的指定键。

        参数：
            key (str)：状态键名。
        返回值：无。
        内部逻辑：委托给 self.runtime_state.delete。
        调用位置：需要删除全局运行时状态的地方调用。
        """
        self.runtime_state.delete(key)

    def state_set_path_list(
        self, key: str, files: list[str] | None, limit: int = 50
    ) -> None:
        """
        设置运行时状态中的文件路径列表。

        参数：
            key (str)：状态键名。
            files (list[str] | None)：文件路径列表。
            limit (int)：最大保留路径数，默认 50。
        返回值：无。
        内部逻辑：委托给 self.runtime_state.set_path_list。
        调用位置：如 set_uploaded_config_files 等封装方法中调用。
        """
        self.runtime_state.set_path_list(key, files, limit=limit)

    def state_get_path_list(self, key: str, limit: int = 50) -> list[str]:
        """
        获取运行时状态中的文件路径列表。

        参数：
            key (str)：状态键名。
            limit (int)：最大返回路径数，默认 50。
        返回值：list[str]，有效路径列表。
        内部逻辑：委托给 self.runtime_state.get_path_list。
        调用位置：如 get_uploaded_config_files 等封装方法中调用。
        """
        return self.runtime_state.get_path_list(key, limit=limit)

    def set_uploaded_config_files(self, files: list[str] | None) -> None:
        """
        设置已上传的配置文件列表。

        参数：
            files (list[str] | None)：配置文件路径列表。
        返回值：无。
        内部逻辑：使用 GO_UPLOADED_FILES_STATE_KEY 作为键写入运行时状态。
        调用位置：配置上传完成后由界面或任务逻辑调用。
        """
        self.state_set_path_list("go.uploaded_config_files", files)

    def get_uploaded_config_files(self) -> list[str]:
        """
        获取已上传的配置文件列表。

        参数：无。
        返回值：list[str]，有效的已上传配置文件路径列表。
        内部逻辑：使用 GO_UPLOADED_FILES_STATE_KEY 作为键读取运行时状态。
        调用位置：需要展示或加载已上传配置文件的地方调用。
        """
        return self.state_get_path_list("go.uploaded_config_files")

    def register_task_log(
        self, title: str, mode: str, log_file: str, pid: int | None = None
    ) -> None:
        """
        注册一条新的任务日志。

        参数：
            title (str)：任务标题。
            mode (str)：任务模式。
            log_file (str)：日志文件路径。
            pid (int | None)：进程 ID，可选。
        返回值：无。
        内部逻辑：在 task_logs 列表头部插入新条目，并限制列表长度最多 50 条。
        调用位置：抢票任务启动时调用。
        """
        self.task_logs.insert(
            0,
            TaskLogEntry(
                title=title,
                mode=mode,
                log_file=log_file,
                created_at=time.time(),
                pid=pid,
                status="运行中",
            ),
        )
        self.task_logs = self.task_logs[:50]

    def get_task_logs(self) -> list[TaskLogEntry]:
        """
        获取所有任务日志。

        参数：无。
        返回值：list[TaskLogEntry]，任务日志列表的副本。
        内部逻辑：返回 list(self.task_logs)。
        调用位置：任务管理界面需要展示任务列表时调用。
        """
        return list(self.task_logs)

    def get_task_log(self, pid: int) -> TaskLogEntry | None:
        """
        根据进程 ID 获取任务日志。

        参数：
            pid (int)：进程 ID。
        返回值：TaskLogEntry | None，匹配的任务日志或 None。
        内部逻辑：遍历 task_logs 查找 pid 匹配的条目。
        调用位置：需要根据进程 ID 定位任务时调用。
        """
        for entry in self.task_logs:
            if entry.pid == pid:
                return entry
        return None

    def remove_task_log(self, pid: int) -> None:
        """
        根据进程 ID 移除任务日志。

        参数：
            pid (int)：进程 ID。
        返回值：无。
        内部逻辑：过滤掉 pid 匹配的条目。
        调用位置：任务结束后清理任务列表时调用。
        """
        self.task_logs = [entry for entry in self.task_logs if entry.pid != pid]

    def remove_task_logs_by_paths(self, log_files: list[str] | set[str]) -> None:
        """
        根据日志文件路径集合移除任务日志。

        参数：
            log_files (list[str] | set[str])：要移除的日志文件路径集合。
        返回值：无。
        内部逻辑：将输入路径转换为绝对路径集合，过滤掉 log_file 匹配的任务日志。
        调用位置：批量清理日志时调用。
        """
        normalized = {os.path.abspath(path) for path in log_files}
        self.task_logs = [
            entry
            for entry in self.task_logs
            if os.path.abspath(entry.log_file) not in normalized
        ]

    def update_task_log_status(self, pid: int, status: str) -> None:
        """
        更新指定任务日志的状态。

        参数：
            pid (int)：进程 ID。
            status (str)：新的状态字符串。
        返回值：无。
        内部逻辑：查找对应条目并更新 status；若状态不再是 "运行中"，则记录 finished_at。
        调用位置：任务状态变更时调用。
        """
        for entry in self.task_logs:
            if entry.pid == pid:
                entry.status = status
                if status != "运行中":
                    entry.finished_at = time.time()
                return

    def register_proxy_usage(self, proxy: str, task_name: str) -> None:
        """
        注册代理被某个任务使用。

        参数：
            proxy (str)：代理地址。
            task_name (str)：使用代理的任务名称。
        返回值：无。
        内部逻辑：在 proxy_usage 字典中为 proxy 维护任务名称列表并去重。
        调用位置：任务启动并绑定代理时调用。
        """
        if proxy not in self.proxy_usage:
            self.proxy_usage[proxy] = []
        if task_name not in self.proxy_usage[proxy]:
            self.proxy_usage[proxy].append(task_name)

    def unregister_proxy_usage(self, proxy: str, task_name: str) -> None:
        """
        注销代理与任务的关联。

        参数：
            proxy (str)：代理地址。
            task_name (str)：任务名称。
        返回值：无。
        内部逻辑：从 proxy_usage[proxy] 中移除 task_name，若为空则删除该代理键。
        调用位置：任务结束或代理切换时调用。
        """
        if proxy in self.proxy_usage:
            self.proxy_usage[proxy] = [t for t in self.proxy_usage[proxy] if t != task_name]
            if not self.proxy_usage[proxy]:
                del self.proxy_usage[proxy]

    def get_proxy_usage(self, proxy: str) -> list[str]:
        """
        获取使用指定代理的任务列表。

        参数：
            proxy (str)：代理地址。
        返回值：list[str]，使用该代理的任务名称列表。
        内部逻辑：从 proxy_usage 字典中取值，不存在返回空列表。
        调用位置：代理管理相关界面或日志中调用。
        """
        return self.proxy_usage.get(proxy, [])

    def get_all_proxy_usage(self) -> dict[str, list[str]]:
        """
        获取所有代理的使用情况。

        参数：无。
        返回值：dict[str, list[str]]，代理到任务名称列表的映射副本。
        内部逻辑：返回 self.proxy_usage 的副本。
        调用位置：需要展示全局代理使用情况时调用。
        """
        return dict(self.proxy_usage)


GlobalStatusInstance = GlobalStatus()


def runtime_state_reader(
    key: str,
    *,
    kind: str = "value",
    default: Any = None,
    limit: int = 50,
):
    """
    装饰器工厂：从全局运行时状态读取值并作为函数返回值的后备。

    参数：
        key (str)：要读取的运行时状态键名。
        kind (str)：读取类型，"value" 表示普通值，"path_list" 表示文件路径列表。
        default (Any)：状态不存在或为空时返回的默认值。
        limit (int)：path_list 类型下的最大返回数量。
    返回值：Callable，返回的装饰器函数。
    内部逻辑：
        包装目标函数，先调用原函数获得 fallback；
        若 kind 为 path_list 则读取路径列表，否则读取普通值；
        当读取到的值为 None 或空时返回 fallback，否则返回读取值。
    调用位置：用于 Gradio 回调函数，使界面组件能从全局状态恢复值。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            fallback = func(*args, **kwargs)
            if kind == "path_list":
                files = GlobalStatusInstance.state_get_path_list(key, limit=limit)
                return files if files else (fallback or [])
            value = GlobalStatusInstance.state_get(key, default)
            return fallback if value is None else value

        return wrapper

    return decorator


def runtime_state_writer(
    key: str,
    *,
    kind: str = "value",
    arg_index: int = 0,
    limit: int = 50,
    value_getter: Callable[[tuple[Any, ...], dict[str, Any], Any], Any] | None = None,
):
    """
    装饰器工厂：将函数输入或返回值写入全局运行时状态。

    参数：
        key (str)：要写入的运行时状态键名。
        kind (str)：写入类型，"value" 表示普通值，"path_list" 表示文件路径列表。
        arg_index (int)：当未提供 value_getter 时，从被装饰函数的位置参数中取第 arg_index 个作为写入值。
        limit (int)：path_list 类型下的最大保存数量。
        value_getter (Callable | None)：自定义取值函数，签名 (args, kwargs, result) -> value；
                                        若提供则优先使用。
    返回值：Callable，返回的装饰器函数。
    内部逻辑：
        包装目标函数，先执行原函数获得 result；
        根据 value_getter 或 arg_index 确定要保存的值；
        按 kind 写入 GlobalStatusInstance 对应状态。
    调用位置：用于 Gradio 回调函数，使界面输入能持久化到全局状态。
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            if value_getter is not None:
                value = value_getter(args, kwargs, result)
            elif len(args) > arg_index:
                value = args[arg_index]
            else:
                value = None

            if kind == "path_list":
                GlobalStatusInstance.state_set_path_list(key, value, limit=limit)
            else:
                GlobalStatusInstance.state_set(key, value)
            return result

        return wrapper

    return decorator
