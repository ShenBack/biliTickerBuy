"""
interface/execution.py — 抢票任务执行与生命周期管理模块。

文件整体功能：
  提供抢票任务的多层次执行与管理能力，包括：
  1. 内存态任务（in-memory）：在同进程内通过后台线程运行抢票，适用于 API 或库调用。
  2. 托管运行（managed run）：通过子进程启动独立抢票进程，持久化状态、日志、结果到磁盘，
     支持心跳超时检测、自动对账、优雅取消。
  3. 同步运行（sync）：阻塞式运行抢票并直接返回结果，适用于脚本调用。
  4. 统一的状态机与支付字段提取：从 BuyStreamEvent 中解析支付二维码、订单 ID、订单详情页等。

所属模块：接口层 (interface)
依赖文件：
  - interface.config.RuntimeOptions / build_runtime_options / validate_config  (运行时配置)
  - interface.types.BuyTaskRecord                                          (内存任务记录类型)
  - app_cmd.config.BuyConfig                                               (抢票配置对象)
  - task.buy.Buy                                                           (抢票业务类)

对外能力（主要函数）：
  - start_buy(config, runtime_options)              → 在后台线程启动内存态抢票任务。
  - task_status(task_id)                            → 查询内存态任务状态。
  - run_buy_sync(config, runtime_options)           → 同步阻塞运行抢票并返回结果。
  - start_managed_buy(config, ..., run_id)          → 启动子进程托管抢票，持久化状态。
  - managed_task_status(run_id)                     → 查询托管运行状态（含对账）。
  - cancel_managed_buy(run_id)                      → 取消正在运行的托管任务。
  - delete_managed_buy(run_id, force)               → 删除托管运行目录（强制停止并清理）。
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from .config import RuntimeOptions, build_runtime_options, validate_config
from .types import BuyTaskRecord

# 内存态任务全局存储：task_id → BuyTaskRecord。
_TASKS: dict[str, BuyTaskRecord] = {}
# 保护 _TASKS 并发读写的线程锁。
_TASKS_LOCK = threading.Lock()


def _collect_payment_fields_from_event(event: Any) -> dict[str, Any]:
    """
    从 BuyStreamEvent 中提取支付相关字段。

    核心作用：
      1. 优先从 event.state 读取 payment_qr_url、order_id、order_detail_url、payment_code_url。
      2. 若 state 中无值，则尝试从 event.message 文本中按 "FIELD=value" 格式解析。

    输入参数：
      - event : Any — Buy.stream() 产生的事件对象。

    返回值：
      dict[str, Any] — 提取到的支付字段字典，仅包含有值的字段。

    调用位置：
      由 _run_buy_task 在处理每个事件时调用；run_buy_sync 也直接内联了类似逻辑。
    """
    result: dict[str, Any] = {}
    state = getattr(event, "state", None)
    for key in ("payment_qr_url", "order_id", "order_detail_url", "payment_code_url"):
        value = getattr(state, key, None) if state is not None else None
        if value not in (None, ""):
            result[key] = value

    message = getattr(event, "message", None)
    if isinstance(message, str):
        markers = {
            "PAYMENT_QR_URL=": "payment_qr_url",
            "ORDER_ID=": "order_id",
            "ORDER_DETAIL_URL=": "order_detail_url",
            "PAYMENT_CODE_URL=": "payment_code_url",
        }
        for prefix, field in markers.items():
            if message.startswith(prefix):
                result[field] = message.split("=", 1)[1]
                break
    return result


def _package_root() -> Path:
    """
    获取当前包根目录。

    核心作用：
      以 interface/__init__.py 所在目录向上回退两级，定位项目根目录。

    输入参数：无。

    返回值：
      Path — interface/__init__.py 的上两级目录（即项目根目录）。

    调用位置：
      由 _managed_runs_root、start_managed_buy 调用。
    """
    return Path(__file__).resolve().parents[1]


def _managed_runs_root(root: str | Path | None = None) -> Path:
    """
    获取托管运行根目录。

    核心作用：
      若未指定 root，默认使用项目根目录下的 btb_runs；不存在则自动创建。

    输入参数：
      - root : str | Path | None — 自定义托管运行根目录；为空时使用默认路径。

    返回值：
      Path — 托管运行目录路径。

    调用位置：
      由 start_managed_buy、managed_task_status、cancel_managed_buy、delete_managed_buy 调用。
    """
    target = Path(root) if root is not None else _package_root() / "btb_runs"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _dump_json(path: Path, payload: dict[str, Any]) -> None:
    """
    安全地将字典写入 JSON 文件。

    核心作用：
      先写入 .tmp 临时文件，再通过 os.replace 原子替换目标文件，避免写入中断导致文件损坏。

    输入参数：
      - path    : Path — 目标文件路径。
      - payload : dict[str, Any] — 待写入的字典。

    返回值：无。

    调用位置：
      由 start_managed_buy、_update_managed_status、cancel_managed_buy、managed_runner.py 等调用。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _load_json(path: Path) -> dict[str, Any]:
    """
    从 JSON 文件加载字典。

    核心作用：
      以 UTF-8 编码读取指定 JSON 文件并返回字典。

    输入参数：
      - path : Path — JSON 文件路径。

    返回值：
      dict[str, Any] — 解析后的字典。

    调用位置：
      由 _update_managed_status、_reconcile_managed_run、managed_task_status、cancel_managed_buy、delete_managed_buy 调用。
    """
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_text_tail(path: Path, *, max_lines: int = 20) -> str:
    """
    读取文本文件末尾若干行。

    核心作用：
      用于获取托管运行子进程 stdout/stderr 的尾部日志，辅助排查异常退出原因。

    输入参数：
      - path      : Path — 文本文件路径。
      - max_lines : int — 最大读取行数，默认 20。

    返回值：
      str — 末尾最多 max_lines 行的文本；文件不存在返回空字符串。

    调用位置：
      由 _build_managed_failure_result、_reconcile_managed_run 调用。
    """
    if not path.exists():
        return ""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = handle.readlines()
    return "".join(lines[-max_lines:]).strip()


def _heartbeat_timeout_seconds(status: dict[str, Any]) -> float:
    """
    从状态字典中提取心跳超时秒数。

    核心作用：
      若状态字典中未配置或配置无效，默认返回 30.0 秒。

    输入参数：
      - status : dict[str, Any] — 托管运行状态字典。

    返回值：
      float — 心跳超时秒数。

    调用位置：
      由 _reconcile_managed_run 在判断子进程心跳是否超时时调用。
    """
    value = status.get("heartbeat_timeout_seconds")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return 30.0


def _update_managed_status(
    run_dir: Path,
    **fields: Any,
) -> dict[str, Any]:
    """
    更新托管运行的 status.json。

    核心作用：
      读取现有状态，应用增量字段，自动更新 updated_at 为当前时间戳，再持久化。

    输入参数：
      - run_dir : Path — 托管运行目录。
      - **fields: Any — 需要更新的状态字段。

    返回值：
      dict[str, Any] — 更新后的完整状态字典。

    调用位置：
      由 _mark_managed_run_failed、_reconcile_managed_run、cancel_managed_buy、start_managed_buy 调用。
    """
    status_path = run_dir / "status.json"
    status = _load_json(status_path)
    status.update(fields)
    status["updated_at"] = time.time()
    _dump_json(status_path, status)
    return status


def _pid_is_running(pid: int | None) -> bool:
    """
    判断指定 PID 的进程是否仍在运行。

    核心作用：
      - Windows：通过 OpenProcess + GetExitCodeProcess 判断退出码是否为 STILL_ACTIVE (259)。
      - Unix-like：通过 os.kill(pid, 0) 判断进程是否存在。

    输入参数：
      - pid : int | None — 进程 PID。

    返回值：
      bool — True 表示进程仍在运行；非法 PID 返回 False。

    调用位置：
      由 _reconcile_managed_run、cancel_managed_buy、delete_managed_buy 调用。
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        still_active = 259

        handle = ctypes.windll.kernel32.OpenProcess(
            process_query_limited_information | synchronize,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, SystemError):
        return False
    return True


def _terminate_pid(pid: int) -> None:
    """
    强制终止指定 PID 的进程。

    核心作用：
      - Windows：使用 taskkill /T /F 终止进程及其子树。
      - Unix-like：发送 SIGTERM 信号。

    输入参数：
      - pid : int — 待终止的进程 PID。

    返回值：无。

    调用位置：
      由 cancel_managed_buy 在强制停止子进程时调用。
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    os.kill(pid, signal.SIGTERM)


def _build_managed_failure_result(
    run_dir: Path,
    run_id: str,
    *,
    error: str,
    status: dict[str, Any],
) -> dict[str, Any]:
    """
    构建托管运行失败时的 result.json 内容。

    核心作用：
      聚合错误信息、状态中的支付字段、以及 stdout/stderr 尾部日志，便于排查问题。

    输入参数：
      - run_dir : Path — 托管运行目录。
      - run_id  : str — 运行唯一标识。
      - error   : str — 错误描述。
      - status  : dict[str, Any] — 当前状态字典。

    返回值：
      dict[str, Any] — 失败结果字典（含 ok=False, status=failed 等）。

    调用位置：
      由 _mark_managed_run_failed 调用。
    """
    stdout_tail = _read_text_tail(run_dir / "stdout.log")
    stderr_tail = _read_text_tail(run_dir / "stderr.log")
    result = {
        "ok": False,
        "run_id": run_id,
        "status": "failed",
        "error": error,
        "payment_qr_url": status.get("payment_qr_url"),
        "order_id": status.get("order_id"),
        "order_detail_url": status.get("order_detail_url"),
        "payment_code_url": status.get("payment_code_url"),
        "logs_path": status.get("logs_path"),
        "last_message": status.get("last_message"),
    }
    if stdout_tail:
        result["stdout_tail"] = stdout_tail
    if stderr_tail:
        result["stderr_tail"] = stderr_tail
    return result


def _mark_managed_run_failed(
    run_dir: Path,
    *,
    run_id: str,
    error: str,
) -> dict[str, Any]:
    """
    将托管运行标记为失败并持久化结果。

    核心作用：
      更新 status.json 为 failed 状态，生成 result.json，并返回更新后的状态。

    输入参数：
      - run_dir : Path — 托管运行目录。
      - run_id  : str — 运行唯一标识。
      - error   : str — 错误描述。

    返回值：
      dict[str, Any] — 更新后的状态字典。

    调用位置：
      由 _reconcile_managed_run、start_managed_buy 在检测到异常退出时调用。
    """
    status = _update_managed_status(
        run_dir,
        status="failed",
        finished_at=time.time(),
        error=error,
        last_message=error,
    )
    result = _build_managed_failure_result(run_dir, run_id, error=error, status=status)
    _dump_json(run_dir / "result.json", result)
    return status


def _reconcile_managed_run(run_dir: Path, status: dict[str, Any]) -> dict[str, Any]:
    """
    对托管运行状态进行对账（reconcile）。

    核心作用：
      1. 若状态已为终态（succeeded/completed/duplicate_order/failed/cancelled），直接返回。
      2. 若 PID 仍在运行：
         - 检查心跳超时（updated_at + heartbeat_timeout_seconds），超时则标记失败。
         - 否则保持当前状态。
      3. 若 PID 已退出：
         - 若 result.json 存在且为终态，同步支付字段并更新状态。
         - 否则根据 stdout/stderr 尾部日志推断失败原因并标记失败。

    输入参数：
      - run_dir : Path — 托管运行目录。
      - status  : dict[str, Any] — 当前 status.json 中的状态字典。

    返回值：
      dict[str, Any] — 对账后的最新状态字典。

    调用位置：
      由 managed_task_status 在查询状态时调用。
    """
    current_status = status.get("status")
    if current_status in {
        "succeeded",
        "completed",
        "duplicate_order",
        "failed",
        "cancelled",
    }:
        return status

    pid = status.get("pid")
    if _pid_is_running(pid):
        updated_at = status.get("updated_at")
        heartbeat_timeout = _heartbeat_timeout_seconds(status)
        if (
            isinstance(updated_at, (int, float))
            and time.time() - float(updated_at) > heartbeat_timeout
        ):
            return _mark_managed_run_failed(
                run_dir,
                run_id=status["run_id"],
                error="managed runner heartbeat timed out after {0:.1f}s".format(
                    heartbeat_timeout
                ),
            )
        return status

    result_path = run_dir / "result.json"
    if result_path.exists():
        result = _load_json(result_path)
        terminal_status = result.get("status")
        if terminal_status in {
            "succeeded",
            "completed",
            "duplicate_order",
            "failed",
            "cancelled",
        }:
            fields: dict[str, Any] = {
                "status": terminal_status,
                "finished_at": status.get("finished_at") or time.time(),
            }
            if result.get("payment_qr_url"):
                fields["payment_qr_url"] = result["payment_qr_url"]
            if result.get("order_id") not in (None, ""):
                fields["order_id"] = result["order_id"]
            if result.get("order_detail_url"):
                fields["order_detail_url"] = result["order_detail_url"]
            if result.get("payment_code_url"):
                fields["payment_code_url"] = result["payment_code_url"]
            if result.get("last_message"):
                fields["last_message"] = result["last_message"]
            if terminal_status == "failed" and result.get("error"):
                fields["error"] = result["error"]
            return _update_managed_status(run_dir, **fields)

    error = "managed runner exited before writing final status"
    stderr_tail = _read_text_tail(run_dir / "stderr.log")
    stdout_tail = _read_text_tail(run_dir / "stdout.log")
    if stderr_tail:
        error = "{0}; stderr: {1}".format(error, stderr_tail)
    elif stdout_tail:
        error = "{0}; stdout: {1}".format(error, stdout_tail)
    return _mark_managed_run_failed(run_dir, run_id=status["run_id"], error=error)


def _append_log(task_id: str, message: str) -> None:
    """
    向内存态任务追加日志。

    核心作用：
      在加锁状态下向 BuyTaskRecord.logs 追加消息，并限制单任务日志最多保留 200 条。

    输入参数：
      - task_id : str — 内存任务 ID。
      - message : str — 日志内容。

    返回值：无。

    调用位置：
      由 _run_buy_task 在收到 Buy.stream() 事件时调用。
    """
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        record.logs.append(message)
        if len(record.logs) > 200:
            record.logs = record.logs[-200:]


def _update_task(task_id: str, **fields: Any) -> None:
    """
    更新内存态任务的字段。

    核心作用：
      在加锁状态下批量设置 BuyTaskRecord 的属性。

    输入参数：
      - task_id  : str — 内存任务 ID。
      - **fields : Any — 待更新的字段名与值。

    返回值：无。

    调用位置：
      由 _run_buy_task、start_buy 调用。
    """
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        for key, value in fields.items():
            setattr(record, key, value)


def _run_buy_task(
    task_id: str,
    config: dict[str, Any],
    runtime_options: RuntimeOptions,
) -> None:
    """
    在后台线程中执行抢票任务。

    核心作用：
      1. 构造 BuyConfig 与 Buy 实例。
      2. 遍历 buy_job.stream() 产生的 BuyStreamEvent，提取消息与支付字段。
      3. 若消息中包含 "抢票成功"，标记 succeeded=True。
      4. 异常时记录日志并更新任务状态为 failed。

    输入参数：
      - task_id       : str — 内存任务 ID。
      - config        : dict[str, Any] — 规范化后的票务配置。
      - runtime_options : RuntimeOptions — 运行时选项。

    返回值：无（通过 _update_task 更新全局 _TASKS）。

    调用位置：
      由 start_buy 启动的 daemon 线程调用。
    """
    from app_cmd.config.BuyConfig import BuyConfig
    from task.buy import Buy

    buy_job = Buy(
        config=BuyConfig.from_runtime_options(
            json.dumps(config, ensure_ascii=False),
            runtime_options,
        ),
    )
    _update_task(task_id, status="running", started_at=time.time())
    succeeded = False
    try:
        for event in buy_job.stream():
            message = event.message
            if message is None:
                continue
            _append_log(task_id, message)
            if "抢票成功" in message:
                succeeded = True
            payment_fields = _collect_payment_fields_from_event(event)
            if payment_fields:
                _update_task(task_id, **payment_fields)

        _update_task(
            task_id,
            status="succeeded" if succeeded else "completed",
            finished_at=time.time(),
        )
    except Exception as exc:
        _append_log(task_id, "task exception: {0!r}".format(exc))
        _update_task(
            task_id,
            status="failed",
            finished_at=time.time(),
            error=repr(exc),
        )


def start_buy(
    config_or_path: str | Path | dict[str, Any],
    *,
    runtime_options: dict[str, Any] | RuntimeOptions | None = None,
) -> dict[str, Any]:
    """
    启动内存态抢票任务（后台线程异步执行）。

    核心作用：
      1. 校验配置（validate_config）。
      2. 构建运行时选项（build_runtime_options）并与传入选项合并。
      3. 创建 BuyTaskRecord，存入全局 _TASKS。
      4. 启动 daemon 线程执行 _run_buy_task。

    输入参数：
      - config_or_path  : str | Path | dict[str, Any] — 票务配置或配置文件路径。
      - runtime_options : dict[str, Any] | RuntimeOptions | None — 运行时选项覆盖项。

    返回值：
      dict[str, Any] — {ok: bool, validation: dict, task: dict | None}。

    调用位置：
      由外部 API、库调用入口启动后台抢票任务时调用。
    """
    validation = validate_config(config_or_path)
    if not validation.ok:
        return {"ok": False, "validation": validation.to_dict(), "task": None}

    assert validation.normalized_config is not None
    runtime = build_runtime_options()
    runtime = runtime.merged_with(runtime_options)

    task_id = uuid.uuid4().hex
    record = BuyTaskRecord(
        task_id=task_id,
        status="pending",
        detail=validation.normalized_config.get("detail", "unknown-task"),
        created_at=time.time(),
    )
    with _TASKS_LOCK:
        _TASKS[task_id] = record

    thread = threading.Thread(
        target=_run_buy_task,
        args=(task_id, validation.normalized_config, runtime),
        daemon=True,
        name="biliTickerBuy-task-{0}".format(task_id[:8]),
    )
    thread.start()

    return {
        "ok": True,
        "validation": validation.to_dict(),
        "task": record.to_dict(),
    }


def task_status(task_id: str) -> dict[str, Any]:
    """
    查询内存态任务状态。

    核心作用：
      在加锁状态下从全局 _TASKS 中读取任务记录并返回其字典形式。

    输入参数：
      - task_id : str — 内存任务 ID。

    返回值：
      dict[str, Any] — {ok: bool, task: dict}；若任务不存在返回 {ok: False, error: ...}。

    调用位置：
      由外部状态查询接口调用。
    """
    with _TASKS_LOCK:
        record = _TASKS.get(task_id)
        if record is None:
            return {"ok": False, "error": "task not found", "task_id": task_id}
        return {"ok": True, "task": record.to_dict()}


def run_buy_sync(
    config_or_path: str | Path | dict[str, Any],
    *,
    runtime_options: dict[str, Any] | RuntimeOptions | None = None,
) -> dict[str, Any]:
    """
    同步阻塞运行抢票任务并返回完整结果。

    核心作用：
      在当前线程直接执行 Buy.stream()，收集所有日志消息与支付字段，
      最终返回状态、日志和支付信息。

    输入参数：
      - config_or_path  : str | Path | dict[str, Any] — 票务配置或配置文件路径。
      - runtime_options : dict[str, Any] | RuntimeOptions | None — 运行时选项覆盖项。

    返回值：
      dict[str, Any] — {
        ok: bool,
        validation: dict,
        status: str,
        logs: list[str],
        payment_qr_url, order_id, order_detail_url, payment_code_url
      }。

    调用位置：
      由脚本调用或需要同步等待抢票结果的入口调用。
    """
    from app_cmd.config.BuyConfig import BuyConfig
    from task.buy import Buy

    validation = validate_config(config_or_path)
    if not validation.ok:
        return {
            "ok": False,
            "validation": validation.to_dict(),
            "logs": [],
            "payment_qr_url": None,
        }

    assert validation.normalized_config is not None
    runtime = build_runtime_options()
    runtime = runtime.merged_with(runtime_options)

    buy_job = Buy(
        config=BuyConfig.from_runtime_options(
            json.dumps(validation.normalized_config, ensure_ascii=False),
            runtime,
        ),
    )

    logs: list[str] = []
    payment_qr_url: str | None = None
    order_id: int | str | None = None
    order_detail_url: str | None = None
    payment_code_url: str | None = None
    succeeded = False
    for event in buy_job.stream():
        message = event.message
        if message is None:
            continue
        logs.append(message)
        if "抢票成功" in message:
            succeeded = True
        payment_fields = _collect_payment_fields_from_event(event)
        if "payment_qr_url" in payment_fields:
            payment_qr_url = payment_fields["payment_qr_url"]
        if "order_id" in payment_fields:
            order_id = payment_fields["order_id"]
        if "order_detail_url" in payment_fields:
            order_detail_url = payment_fields["order_detail_url"]
        if "payment_code_url" in payment_fields:
            payment_code_url = payment_fields["payment_code_url"]

    return {
        "ok": True,
        "validation": validation.to_dict(),
        "status": "succeeded" if succeeded else "completed",
        "logs": logs,
        "payment_qr_url": payment_qr_url,
        "order_id": order_id,
        "order_detail_url": order_detail_url,
        "payment_code_url": payment_code_url,
    }


def start_managed_buy(
    config_or_path: str | Path | dict[str, Any],
    *,
    runtime_options: dict[str, Any] | RuntimeOptions | None = None,
    run_id: str | None = None,
    runs_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    启动托管抢票任务（子进程独立运行，状态持久化到磁盘）。

    核心作用：
      1. 校验配置并合并运行时选项（默认 show_qrcode=False）。
      2. 在 managed runs 目录下创建以 run_id 命名的子目录。
      3. 写入 run.json、config.json、runtime.json、status.json。
      4. 通过 subprocess.Popen 启动 managed_runner.py 子进程：
         - Windows 使用 DETACHED_PROCESS / CREATE_NEW_PROCESS_GROUP / CREATE_NO_WINDOW 避免弹窗。
         - 重定向 stdout/stderr 到日志文件。
      5. 若子进程立即退出，自动标记失败并返回错误。

    输入参数：
      - config_or_path  : str | Path | dict[str, Any] — 票务配置或配置文件路径。
      - runtime_options : dict[str, Any] | RuntimeOptions | None — 运行时选项覆盖项。
      - run_id          : str | None — 指定运行 ID；为空时生成 UUID。
      - runs_root       : str | Path | None — 自定义托管运行根目录。

    返回值：
      dict[str, Any] — {ok: bool, validation: dict, run: dict | None, error: str | None}。

    调用位置：
      由外部需要独立、持久化抢票进程的入口调用。
    """
    validation = validate_config(config_or_path)
    if not validation.ok:
        return {"ok": False, "validation": validation.to_dict(), "run": None}

    assert validation.normalized_config is not None
    runtime = build_runtime_options(show_qrcode=False)
    runtime = runtime.merged_with(runtime_options)

    managed_root = _managed_runs_root(runs_root)
    assigned_run_id = run_id or uuid.uuid4().hex
    run_dir = managed_root / assigned_run_id
    if run_dir.exists():
        return {
            "ok": False,
            "validation": validation.to_dict(),
            "run": None,
            "error": "run_id already exists",
            "run_id": assigned_run_id,
        }
    run_dir.mkdir(parents=True, exist_ok=False)

    run_metadata = {
        "run_id": assigned_run_id,
        "created_at": time.time(),
    }
    _dump_json(run_dir / "run.json", run_metadata)
    _dump_json(run_dir / "config.json", validation.normalized_config)
    _dump_json(run_dir / "runtime.json", runtime.to_dict())

    status = {
        "ok": True,
        "run_id": assigned_run_id,
        "status": "pending",
        "detail": validation.normalized_config.get("detail", assigned_run_id),
        "pid": None,
        "created_at": run_metadata["created_at"],
        "started_at": None,
        "updated_at": run_metadata["created_at"],
        "finished_at": None,
        "payment_qr_url": None,
        "order_id": None,
        "order_detail_url": None,
        "payment_code_url": None,
        "error": None,
        "last_message": None,
        "heartbeat_timeout_seconds": max(float(runtime.interval) / 1000.0 * 20.0, 30.0),
        "logs_path": str(run_dir / "events.log"),
        "result_path": str(run_dir / "result.json"),
        "config_path": str(run_dir / "config.json"),
        "runtime_path": str(run_dir / "runtime.json"),
    }
    _dump_json(run_dir / "status.json", status)

    runner_path = _package_root() / "interface" / "managed_runner.py"
    command = [sys.executable, str(runner_path), str(run_dir)]
    env = os.environ.copy()
    env.setdefault("BTB_APP_LOG_NAME", "app-{0}.log".format(assigned_run_id))

    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
        startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)

    stdout_handle = open(run_dir / "stdout.log", "a", encoding="utf-8")
    stderr_handle = open(run_dir / "stderr.log", "a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(_package_root()),
            stdout=stdout_handle,
            stderr=stderr_handle,
            env=env,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()

    status["pid"] = process.pid
    status["updated_at"] = time.time()
    _dump_json(run_dir / "status.json", status)

    time.sleep(0.2)
    returncode = process.poll()
    if returncode is not None:
        failed_status = _mark_managed_run_failed(
            run_dir,
            run_id=assigned_run_id,
            error="managed runner exited immediately with code {0}".format(returncode),
        )
        return {
            "ok": False,
            "validation": validation.to_dict(),
            "run": failed_status,
            "error": failed_status.get("error"),
        }

    return {
        "ok": True,
        "validation": validation.to_dict(),
        "run": {
            "run_id": assigned_run_id,
            "run_dir": str(run_dir),
            "status_path": str(run_dir / "status.json"),
            "result_path": str(run_dir / "result.json"),
            "logs_path": str(run_dir / "events.log"),
            "pid": process.pid,
        },
    }


def managed_task_status(
    run_id: str,
    *,
    runs_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    查询托管运行状态（自动对账）。

    核心作用：
      读取 status.json 后调用 _reconcile_managed_run 进行 PID 与心跳对账，
      若 result.json 存在则一并附加到返回中。

    输入参数：
      - run_id   : str — 运行唯一标识。
      - runs_root: str | Path | None — 自定义托管运行根目录。

    返回值：
      dict[str, Any] — {ok: bool, run: dict}；若不存在返回 {ok: False, error: ...}。

    调用位置：
      由外部托管任务状态查询接口调用。
    """
    run_dir = _managed_runs_root(runs_root) / run_id
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return {"ok": False, "error": "managed run not found", "run_id": run_id}
    status = _reconcile_managed_run(run_dir, _load_json(status_path))
    result_path = run_dir / "result.json"
    if result_path.exists():
        status["result"] = _load_json(result_path)
    return {"ok": True, "run": status}


def cancel_managed_buy(
    run_id: str,
    *,
    runs_root: str | Path | None = None,
) -> dict[str, Any]:
    """
    取消正在运行的托管抢票任务。

    核心作用：
      1. 若任务已处于终态，直接返回提示。
      2. 若子进程仍在运行，调用 _terminate_pid 强制终止。
      3. 更新 status.json 为 cancelled，并写入 result.json 记录取消结果。

    输入参数：
      - run_id   : str — 运行唯一标识。
      - runs_root: str | Path | None — 自定义托管运行根目录。

    返回值：
      dict[str, Any] — {ok: bool, run: dict, cancelled: bool, process_was_running: bool | None}。

    调用位置：
      由外部取消任务接口调用；delete_managed_buy 在 force 模式下也可能调用。
    """
    run_dir = _managed_runs_root(runs_root) / run_id
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return {"ok": False, "error": "managed run not found", "run_id": run_id}

    status = _load_json(status_path)
    current_status = status.get("status")
    if current_status in {
        "succeeded",
        "completed",
        "duplicate_order",
        "failed",
        "cancelled",
    }:
        return {
            "ok": True,
            "run": status,
            "cancelled": current_status == "cancelled",
            "message": "run already finished",
        }

    pid = status.get("pid")
    process_was_running = _pid_is_running(pid)
    if process_was_running:
        assert pid is not None
        _terminate_pid(pid)

    updated_status = _update_managed_status(
        run_dir,
        status="cancelled",
        finished_at=time.time(),
        error=None,
        last_message="cancelled by API",
    )

    result_path = run_dir / "result.json"
    _dump_json(
        result_path,
        {
            "ok": False,
            "run_id": run_id,
            "status": "cancelled",
            "payment_qr_url": updated_status.get("payment_qr_url"),
            "order_id": updated_status.get("order_id"),
            "order_detail_url": updated_status.get("order_detail_url"),
            "payment_code_url": updated_status.get("payment_code_url"),
            "logs_path": updated_status.get("logs_path"),
            "last_message": "cancelled by API",
        },
    )
    return {
        "ok": True,
        "run": updated_status,
        "cancelled": True,
        "process_was_running": process_was_running,
    }


def delete_managed_buy(
    run_id: str,
    *,
    runs_root: str | Path | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    删除托管运行目录（清理历史任务）。

    核心作用：
      1. 校验 run_dir 确实位于 managed_root 下，防止目录穿越。
      2. 若进程仍在运行且未 force，返回错误提示。
      3. 若 force=True 且进程仍在运行，先 cancel 再删除。
      4. 使用 shutil.rmtree 彻底删除运行目录。

    输入参数：
      - run_id   : str — 运行唯一标识。
      - runs_root: str | Path | None — 自定义托管运行根目录。
      - force    : bool — 是否强制停止并删除仍在运行的任务，默认 False。

    返回值：
      dict[str, Any] — {ok: bool, run_id: str, deleted: bool, force: bool, cancelled: dict | None}。

    调用位置：
      由外部任务清理接口调用。
    """
    managed_root = _managed_runs_root(runs_root)
    run_dir = managed_root / run_id
    if not run_dir.exists():
        return {"ok": False, "error": "managed run not found", "run_id": run_id}

    try:
        run_dir.relative_to(managed_root)
    except ValueError:
        return {"ok": False, "error": "run dir escapes managed root", "run_id": run_id}

    status_path = run_dir / "status.json"
    status = _load_json(status_path) if status_path.exists() else {}
    pid = status.get("pid")
    process_is_running = _pid_is_running(pid)

    if process_is_running and not force:
        return {
            "ok": False,
            "error": "managed run is still running; cancel it first or pass force=True",
            "run_id": run_id,
            "run": status,
        }

    cancelled = None
    if process_is_running and force:
        cancelled = cancel_managed_buy(run_id, runs_root=runs_root)

    shutil.rmtree(run_dir)
    return {
        "ok": True,
        "run_id": run_id,
        "deleted": True,
        "force": force,
        "cancelled": cancelled,
    }
