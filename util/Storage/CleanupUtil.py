"""
文件整体功能：清理运行期间产生的日志文件和运行目录，按保留天数与数量上限进行回收。
所属模块：util.Storage
依赖文件：无外部业务依赖，使用 os、shutil、time、pathlib 标准库。
对外能力：提供 cleanup_runtime_artifacts 函数，用于周期性清理日志目录和运行目录中的过期文件。
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path


def _trim_old_paths(paths: list[Path], *, max_count: int) -> list[Path]:
    """
    按修改时间截取超出数量限制的旧路径。

    参数：
        paths (list[Path])：待处理的路径列表。
        max_count (int)：允许保留的最大数量。
    返回值：list[Path]，需要删除的超出限制的旧路径列表。
    内部逻辑：按 st_mtime 降序排序，返回第 max_count 项之后的所有路径。
    调用位置：cleanup_runtime_artifacts 函数内部在按保留天数清理后调用。
    """
    if max_count <= 0 or len(paths) <= max_count:
        return []
    sorted_paths = sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)
    return sorted_paths[max_count:]


def _remove_path(path: Path) -> None:
    """
    删除单个文件或目录。

    参数：
        path (Path)：待删除的文件或目录路径。
    返回值：无。
    内部逻辑：若是目录则使用 shutil.rmtree 递归删除；若是文件则调用 unlink，
              兼容不同 Python 版本的 missing_ok 参数。
    调用位置：cleanup_runtime_artifacts 函数内部在删除过期日志和运行目录时调用。
    """
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        if path.exists():
            path.unlink()


def cleanup_runtime_artifacts(
    *,
    logs_dir: str | Path,
    runs_dir: str | Path,
    retention_days: int = 7,
    max_log_files: int = 200,
    max_run_dirs: int = 100,
) -> dict[str, int]:
    """
    清理运行产物（日志文件与运行目录）。

    参数：
        logs_dir (str | Path)：日志文件所在目录。
        runs_dir (str | Path)：运行目录所在目录。
        retention_days (int)：保留天数，默认 7 天，早于该时间的文件会被删除。
        max_log_files (int)：日志文件最大保留数量，默认 200。
        max_run_dirs (int)：运行目录最大保留数量，默认 100。
    返回值：dict[str, int]，包含 removed_logs、removed_runs、logs_dir_exists、runs_dir_exists 的统计字典。
    内部逻辑：
        1. 计算截止时间戳 cutoff；
        2. 对日志目录先删除过期文件，再按数量上限删除最旧的文件；
        3. 对运行目录执行同样的两步清理；
        4. 返回清理统计信息。
    调用位置：程序启动或定时任务中调用，用于防止日志和运行目录无限增长。
    """
    now = time.time()
    cutoff = now - max(1, int(retention_days)) * 86400
    logs_root = Path(logs_dir)
    runs_root = Path(runs_dir)
    removed_logs = 0
    removed_runs = 0

    if logs_root.exists():
        log_files = [path for path in logs_root.iterdir() if path.is_file()]
        for path in list(log_files):
            if path.stat().st_mtime < cutoff:
                _remove_path(path)
                removed_logs += 1
        remaining_logs = [path for path in logs_root.iterdir() if path.is_file()]
        for path in _trim_old_paths(
            remaining_logs, max_count=max(1, int(max_log_files))
        ):
            _remove_path(path)
            removed_logs += 1

    if runs_root.exists():
        run_dirs = [path for path in runs_root.iterdir() if path.is_dir()]
        for path in list(run_dirs):
            if path.stat().st_mtime < cutoff:
                _remove_path(path)
                removed_runs += 1
        remaining_runs = [path for path in runs_root.iterdir() if path.is_dir()]
        for path in _trim_old_paths(
            remaining_runs, max_count=max(1, int(max_run_dirs))
        ):
            _remove_path(path)
            removed_runs += 1

    return {
        "removed_logs": removed_logs,
        "removed_runs": removed_runs,
        "logs_dir_exists": int(os.path.exists(logs_root)),
        "runs_dir_exists": int(os.path.exists(runs_root)),
    }
