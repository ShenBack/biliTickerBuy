"""
文件整体功能：配置并初始化 Loguru 日志系统。
所属模块：util.log
依赖文件：无外部业务依赖，使用 loguru 第三方库。
对外能力：提供 loguru_config 函数，统一设置文件日志与终端日志的级别、格式、保留策略等。
"""

import os
import sys
from loguru import logger


def loguru_config(
    log_dir: str,
    log_file_name: str,
    file_colorize: bool = False,
    enable_console: bool = True,
    file_level: str | None = None,
    console_level: str | None = None,
    retention_days: int | None = None,
) -> str:
    """
    配置 Loguru 日志系统。

    参数：
        log_dir (str)：日志文件存放目录。
        log_file_name (str)：日志文件名称。
        file_colorize (bool)：是否在文件日志中写入颜色控制符，默认关闭。
        enable_console (bool)：是否启用终端输出，默认开启。
        file_level (str | None)：文件日志级别，None 则读取 BTB_LOG_LEVEL 环境变量，默认 DEBUG。
        console_level (str | None)：终端日志级别，None 则读取 BTB_CONSOLE_LOG_LEVEL 环境变量，默认 INFO。
        retention_days (int | None)：日志保留天数，None 则读取 BTB_LOG_RETENTION_DAYS 环境变量，默认 7。
    返回值：str，最终日志文件的完整路径。
    内部逻辑：
        1. 移除 Loguru 默认 sink；
        2. 解析文件/终端日志级别与保留天数；
        3. 添加按天轮转的文本日志 sink；
        4. 若 enable_console 为 True，再添加 stderr 终端 sink。
    调用位置：util/__init__.py 在模块导入时调用，完成全局日志初始化。
    """
    logger.remove()

    resolved_file_level = (
        file_level or os.environ.get("BTB_LOG_LEVEL") or "DEBUG"
    ).upper()
    resolved_console_level = (
        console_level or os.environ.get("BTB_CONSOLE_LOG_LEVEL") or "INFO"
    ).upper()
    resolved_retention_days = retention_days
    if resolved_retention_days is None:
        try:
            resolved_retention_days = int(os.environ.get("BTB_LOG_RETENTION_DAYS", "7"))
        except ValueError:
            resolved_retention_days = 7

    logger.add(
        os.path.join(log_dir, log_file_name),
        level=resolved_file_level,
        encoding="utf-8",
        rotation="1 day",
        colorize=file_colorize,
        retention=f"{max(1, int(resolved_retention_days))} days",
        format="<green>[{time:YYYY-MM-DD:HH:mm:ss.SSS}]</green>|<level>{level}</level>|<level>{message}</level>",
    )

    if enable_console:
        logger.add(
            sys.stderr,
            level=resolved_console_level,
            colorize=True,
            format="<green>[{time:MM-DD:HH:mm:ss.SSS}]</green>|<level>{level}</level>|<level>{message}</level>",
        )
    return os.path.join(log_dir, log_file_name)
