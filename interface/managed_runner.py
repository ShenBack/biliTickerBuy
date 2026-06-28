"""
interface/managed_runner.py — 托管抢票子进程执行入口。

文件整体功能：
  1. 作为独立子进程被 execution.start_managed_buy 启动，读取 run_dir 下的配置与运行时选项。
  2. 在子进程中执行抢票任务，实时更新 status.json、写入 events.log、最终生成 result.json。
  3. 通过独立心跳线程定期刷新 status.json 的 updated_at，供父进程检测存活状态。
  4. 捕获 BaseException，确保异常退出时状态与结果文件仍然完整。

所属模块：接口层 (interface)
依赖文件：
  - app_cmd.config.BuyConfig  （由 main 延迟导入）
  - interface.config.RuntimeOptions  （读取 runtime.json）
  - task.buy.Buy              （由 main 延迟导入）

对外能力：
  - main(run_dir_arg) → 子进程主函数，由 subprocess.Popen 调用；
  - 直接运行本文件并传入 run_dir 路径即可启动托管抢票。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_json(path: Path) -> dict[str, Any]:
    """
    从 JSON 文件加载字典。

    核心作用：
      以 UTF-8 编码读取指定路径的 JSON 文件并返回解析结果。

    输入参数：
      - path : Path — JSON 文件路径。

    返回值：
      dict[str, Any] — 解析后的字典。

    调用位置：
      由 main 函数加载 run.json、config.json、runtime.json、status.json 时调用。
    """
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


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
      由 main 函数及 _heartbeat_loop 更新 status.json、result.json 时调用。
    """
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _append_log(path: Path, message: str) -> None:
    """
    向日志文件追加一行内容。

    核心作用：
      以追加模式打开 events.log 并写入单条消息，用于保存抢票过程中的事件文本。

    输入参数：
      - path    : Path — 日志文件路径（通常为 run_dir/events.log）。
      - message : str — 待写入的日志内容。

    返回值：无。

    调用位置：
      由 main 函数在收到 Buy.stream() 事件消息或异常时调用。
    """
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(message)
        handle.write("\n")


def _heartbeat_loop(
    status_path: Path,
    status: dict[str, Any],
    lock: threading.Lock,
    stop_event: threading.Event,
    interval_seconds: float = 2.0,
) -> None:
    """
    心跳线程主循环，定期刷新 status.json 的 updated_at。

    核心作用：
      1. 每隔 interval_seconds 检查一次停止事件。
      2. 若任务已结束（finished_at 非空）则退出循环。
      3. 否则更新 status["updated_at"] 并持久化，供父进程通过心跳超时判断子进程是否存活。

    输入参数：
      - status_path     : Path — status.json 文件路径。
      - status          : dict[str, Any] — 当前状态字典（共享，需加锁）。
      - lock            : threading.Lock — 保护 status 读写的锁。
      - stop_event      : threading.Event — 主线程设置的停止信号。
      - interval_seconds: float — 心跳刷新间隔（秒），默认 2.0。

    返回值：无。

    调用位置：
      由 main 函数在抢票主循环前启动为 daemon 线程。
    """
    while not stop_event.wait(interval_seconds):
        with lock:
            if status.get("finished_at") is not None:
                return
            status["updated_at"] = time.time()
            _dump_json(status_path, status)


def main(run_dir_arg: str) -> int:
    """
    托管抢票子进程入口函数。

    核心作用：
      1. 从 run_dir 加载 run.json、config.json、runtime.json、status.json。
      2. 构造初始 running 状态并持久化 status.json。
      3. 启动心跳线程保持状态刷新。
      4. 使用 BuyConfig 与 Buy 启动抢票流，处理每条事件消息：
         - 写入 events.log；
         - 更新 last_message；
         - 检测“抢票成功”与“有重复订单”以确定最终状态；
         - 从 event.state 或特殊消息前缀中提取支付相关字段。
      5. 正常结束时写入 result.json；异常时写入 failed 结果。
      6. 停止心跳线程并返回进程退出码。

    输入参数：
      - run_dir_arg : str — 本次托管运行的目录路径，由 execution.start_managed_buy 传入。

    返回值：
      int — 0 表示成功/完成，1 表示异常退出。

    调用位置：
      由 execution.start_managed_buy 通过 subprocess.Popen 启动本文件时调用；
      也可通过命令行 `python -m interface.managed_runner <run_dir>` 直接调用。
    """
    from app_cmd.config.BuyConfig import BuyConfig
    from interface.config import RuntimeOptions
    from task.buy import Buy

    run_dir = Path(run_dir_arg)
    status_path = run_dir / "status.json"
    result_path = run_dir / "result.json"
    logs_path = run_dir / "events.log"

    metadata = _load_json(run_dir / "run.json")
    config = _load_json(run_dir / "config.json")
    runtime = RuntimeOptions.from_mapping(_load_json(run_dir / "runtime.json"))
    existing_status = _load_json(status_path)

    status = {
        "ok": True,
        "run_id": metadata["run_id"],
        "status": "running",
        "detail": config.get("detail", metadata["run_id"]),
        "pid": os.getpid(),
        "created_at": metadata["created_at"],
        "started_at": time.time(),
        "updated_at": time.time(),
        "finished_at": None,
        "payment_qr_url": None,
        "order_id": None,
        "order_detail_url": None,
        "payment_code_url": None,
        "error": None,
        "last_message": None,
        "heartbeat_timeout_seconds": existing_status.get("heartbeat_timeout_seconds"),
        "logs_path": str(logs_path),
        "result_path": str(result_path),
        "config_path": str(run_dir / "config.json"),
        "runtime_path": str(run_dir / "runtime.json"),
    }
    _dump_json(status_path, status)
    status_lock = threading.Lock()
    heartbeat_stop = threading.Event()
    heartbeat_thread = threading.Thread(
        target=_heartbeat_loop,
        args=(status_path, status, status_lock, heartbeat_stop),
        daemon=True,
        name="biliTickerBuy-heartbeat",
    )
    heartbeat_thread.start()

    buy_job = Buy(
        config=BuyConfig.from_runtime_options(
            json.dumps(config, ensure_ascii=False),
            runtime,
            show_qrcode=False,
        ),
    )

    final_status = "completed"
    try:
        for event in buy_job.stream():
            message = event.message
            if message is not None:
                _append_log(logs_path, message)
            with status_lock:
                status["updated_at"] = time.time()
                if message is not None:
                    status["last_message"] = message
                    if "抢票成功" in message:
                        final_status = "succeeded"
                    if "有重复订单" in message:
                        final_status = "duplicate_order"
                    payment_state = event.state
                    for key in (
                        "payment_qr_url",
                        "order_id",
                        "order_detail_url",
                        "payment_code_url",
                    ):
                        value = getattr(payment_state, key, None)
                        if value not in (None, ""):
                            status[key] = value
                    if message.startswith("PAYMENT_QR_URL="):
                        status["payment_qr_url"] = message.split("=", 1)[1]
                    elif message.startswith("ORDER_ID="):
                        status["order_id"] = message.split("=", 1)[1]
                    elif message.startswith("ORDER_DETAIL_URL="):
                        status["order_detail_url"] = message.split("=", 1)[1]
                    elif message.startswith("PAYMENT_CODE_URL="):
                        status["payment_code_url"] = message.split("=", 1)[1]
                _dump_json(status_path, status)
    except BaseException as exc:
        with status_lock:
            status["status"] = "failed"
            status["error"] = repr(exc)
            status["last_message"] = "RUNNER_EXCEPTION={0!r}".format(exc)
            status["updated_at"] = time.time()
            status["finished_at"] = time.time()
        _append_log(logs_path, "RUNNER_EXCEPTION={0!r}".format(exc))
        _dump_json(status_path, status)
        _dump_json(
            result_path,
            {
                "ok": False,
                "run_id": metadata["run_id"],
                "status": "failed",
                "error": repr(exc),
                "payment_qr_url": status["payment_qr_url"],
                "order_id": status["order_id"],
                "order_detail_url": status["order_detail_url"],
                "payment_code_url": status["payment_code_url"],
                "logs_path": str(logs_path),
                "last_message": status["last_message"],
            },
        )
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=3)
        return 1

    with status_lock:
        status["status"] = final_status
        status["updated_at"] = time.time()
        status["finished_at"] = time.time()
        _dump_json(status_path, status)
    _dump_json(
        result_path,
        {
            "ok": True,
            "run_id": metadata["run_id"],
            "status": final_status,
            "payment_qr_url": status["payment_qr_url"],
            "order_id": status["order_id"],
            "order_detail_url": status["order_detail_url"],
            "payment_code_url": status["payment_code_url"],
            "logs_path": str(logs_path),
            "last_message": status["last_message"],
        },
    )
    heartbeat_stop.set()
    heartbeat_thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: managed_runner.py <run_dir>")
    raise SystemExit(main(sys.argv[1]))
