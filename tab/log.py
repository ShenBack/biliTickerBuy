"""
tab/log.py — 任务与日志管理面板。

文件整体功能：
  提供抢票任务与日志文件的可视化管理能力：
  1. 同步 GlobalStatusInstance 中的任务状态，判断进程是否仍在运行。
  2. 从日志文件中识别“抢票完成”与“用户主动停止”等标记，更新任务状态。
  3. 提供终止任务、移除任务卡、清空日志文件等操作。
  4. 渲染任务卡片网格与日志文件列表，并支持打开独立日志查看页。
  5. 自动检测日志中的支付二维码 URL，并通过前端 JS 打开支付页面。

所属模块：
  UI 层 (tab)

依赖文件：
  - util.GlobalStatusInstance (任务状态管理)
  - util.LOG_DIR / log_file_name (日志目录与主日志文件名)
  - util.log.LogWeb.build_log_view_url (日志查看页 URL 构建)

对外能力：
  - render_task_manager_panel(task_panel) → 在指定容器中渲染任务管理面板，返回刷新令牌 State。
  - refresh_task_panel() / refresh_task_panel_with_payments() → 供 tab.go 调用刷新任务面板。
  - visible_task_entries() → 返回当前可见任务条目列表。
  - log_tab() → 构建独立的“日志文件列表”标签页。
"""

import ctypes
import gradio as gr
import html
import json
import os
import signal
import subprocess
import time
from datetime import datetime

from util import GlobalStatusInstance
from util import LOG_DIR
from util import log_file_name
from util.log.LogWeb import build_log_view_url

# 日志文件中用于识别任务完成/停止的标记字符串
TASK_COMPLETED_MARKER = "抢票完成后退出程序。。。。。"
TASK_STOPPED_MARKER = "BTB_TASK_STOPPED_BY_USER"

# 任务状态常量
TASK_STATUS_RUNNING = "运行中"
TASK_STATUS_STOPPED = "已主动结束"
TASK_STATUS_COMPLETED = "已完成"
TASK_STATUS_EXITED = "已结束"

# 前端 JS：当检测到支付二维码 URL 时自动打开新标签页，并使用 localStorage 去重
OPEN_PAYMENT_URLS_JS = """
(payload) => {
    if (!payload) {
        return;
    }
    let urls = [];
    try {
        urls = JSON.parse(payload);
    } catch (_err) {
        return;
    }
    const storageKey = "btb-opened-payment-urls";
    const opened = new Set(JSON.parse(window.localStorage.getItem(storageKey) || "[]"));
    let changed = false;
    for (const url of urls) {
        if (!url || opened.has(url)) {
            continue;
        }
        window.open(url, "_blank", "noopener,noreferrer");
        opened.add(url);
        changed = true;
    }
    if (changed) {
        window.localStorage.setItem(storageKey, JSON.stringify(Array.from(opened)));
    }
}
"""


def _status_class(status: str) -> str:
    """
    将任务状态映射为 CSS 类名。

    输入参数：
      status : str — 任务状态文本。

    返回值：
      str — 对应 CSS 类名，用于前端样式区分。

    调用场景：
      read_task_log_locations() / render_task_cards() / render_log_files() 中渲染卡片样式。
    """
    mapping = {
        TASK_STATUS_RUNNING: "is-running",
        TASK_STATUS_COMPLETED: "is-completed",
        TASK_STATUS_STOPPED: "is-stopped",
        TASK_STATUS_EXITED: "is-exited",
    }
    return mapping.get(status, "is-exited")


def _refresh_token() -> int:
    """
    生成基于纳秒时间戳的刷新令牌。

    返回值：
      int — time.time_ns() 生成的唯一整数，用于触发 Gradio @gr.render 重新渲染。

    调用场景：
      refresh_task_panel() / refresh_task_panel_with_payments() / refresh_log_panel() 中使用。
    """
    return time.time_ns()


def build_main_log_card() -> str:
    """
    构建主日志卡片的 HTML（当前为空占位）。

    返回值：
      str — 空字符串，预留扩展。

    调用场景：
      read_task_log_locations() 与 render_task_cards() 中作为页面顶部占位。
    """
    return ""


def _render_log_path(path: str) -> str:
    """
    渲染日志路径的 HTML 片段。

    输入参数：
      path : str — 日志文件绝对路径。

    返回值：
      str — 包含转义路径的 HTML 字符串。

    调用场景：
      read_task_log_locations() / render_task_cards() / render_log_files() 中展示日志路径。
    """
    return """
    <div class="btb-task-card__meta">
      日志路径
    </div>
    <div class="btb-task-card__path">
      <code>{path}</code>
    </div>
    """.format(path=html.escape(path))


def _render_log_view_action(path: str) -> str:
    """
    渲染“查看”日志链接的 HTML 片段。

    输入参数：
      path : str — 日志文件绝对路径。

    返回值：
      str — 指向日志查看页的 <a> 标签 HTML。

    调用场景：
      read_task_log_locations() 中生成任务卡片的查看按钮。
    """
    return """
    <a class="btb-task-link btb-task-button" href="{log_view_url}" target="_blank" rel="noopener noreferrer">查看</a>
    """.format(log_view_url=html.escape(build_log_view_url(path)))


def _list_log_files() -> list[str]:
    """
    列出日志目录下所有文件，按修改时间倒序排列。

    返回值：
      list[str] — 日志文件绝对路径列表；目录不存在或读取失败返回空列表。

    调用场景：
      clear_log_files() / render_log_files() 中获取日志文件列表。
    """
    try:
        items = [
            os.path.join(LOG_DIR, name)
            for name in os.listdir(LOG_DIR)
            if os.path.isfile(os.path.join(LOG_DIR, name))
        ]
    except OSError:
        return []
    return sorted(items, key=lambda path: os.path.getmtime(path), reverse=True)


def _find_task_entry_by_log_file(log_file: str):
    """
    根据日志文件路径查找对应的任务条目。

    输入参数：
      log_file : str — 日志文件路径。

    返回值：
      TaskEntry | None — 匹配的任务条目；未找到返回 None。

    调用场景：
      clear_log_files() / render_log_files() 中判断日志文件是否关联运行中任务。
    """
    normalized = os.path.abspath(log_file)
    for entry in visible_task_entries():
        if os.path.abspath(entry.log_file) == normalized:
            return entry
    return None


def clear_log_files():
    """
    一键清除日志文件。

    核心作用：
      1. 遍历日志目录中的文件。
      2. 跳过运行中任务的日志文件。
      3. 主应用日志文件（log_file_name）执行清空而不是删除。
      4. 删除其他历史日志，并同步 GlobalStatusInstance 中的任务记录。
      5. 通过 gr.Info / gr.Warning 反馈操作结果。

    返回值：
      tuple — refresh_log_panel() 返回的刷新令牌与面板更新。

    调用场景：
      log_tab() 中“一键清除”按钮点击时触发。
    """
    log_files = _list_log_files()
    if not log_files:
        gr.Info("当前没有可清除的日志文件。")
        return refresh_log_panel()

    removed_paths: list[str] = []
    skipped_running: list[str] = []
    truncated_files: list[str] = []

    for log_file in log_files:
        entry = _find_task_entry_by_log_file(log_file)
        if entry is not None and entry.status == TASK_STATUS_RUNNING:
            skipped_running.append(log_file)
            continue

        try:
            if os.path.basename(log_file) == log_file_name:
                with open(log_file, "w", encoding="utf-8"):
                    pass
                truncated_files.append(log_file)
                continue
            os.remove(log_file)
            removed_paths.append(log_file)
        except OSError:
            continue

    if removed_paths:
        GlobalStatusInstance.remove_task_logs_by_paths(removed_paths)

    if removed_paths or truncated_files:
        message_parts: list[str] = []
        if removed_paths:
            message_parts.append(f"已删除 {len(removed_paths)} 个日志文件")
        if truncated_files:
            message_parts.append(
                f"已清空 {len(truncated_files)} 个当前使用中的应用日志文件"
            )
        if skipped_running:
            message_parts.append(f"跳过 {len(skipped_running)} 个运行中任务日志")
        gr.Info("，".join(message_parts) + "。")
    elif skipped_running:
        gr.Warning("存在运行中的任务日志，已跳过清除。")
    else:
        gr.Warning("日志清除失败，请检查文件权限。")

    return refresh_log_panel()


def is_task_running(pid: int | None) -> bool:
    """
    判断指定 PID 的进程是否仍在运行。

    核心作用：
      跨平台检测进程状态：Windows 使用 OpenProcess + GetExitCodeProcess；
      Linux/macOS 读取 /proc/{pid}/stat 并结合 ps/os.kill 判断。

    输入参数：
      pid : int | None — 进程 ID。

    返回值：
      bool — True 表示进程仍在运行；False 表示已退出或无法访问。

    调用场景：
      sync_task_statuses() / terminate_task() 中判断任务进程状态。
    """
    if not pid:
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
    proc_stat_path = "/proc/{0}/stat".format(pid)
    if os.path.exists(proc_stat_path):
        try:
            with open(proc_stat_path, "r", encoding="utf-8") as handle:
                stat_fields = handle.read().split()
            if len(stat_fields) >= 3 and stat_fields[2] == "Z":
                return False
        except OSError:
            return False
    if _is_posix_zombie_process(pid):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_posix_zombie_process(pid: int) -> bool:
    """
    使用 ps 命令判断 POSIX 系统下进程是否为僵尸进程。

    输入参数：
      pid : int — 进程 ID。

    返回值：
      bool — True 表示是僵尸进程。

    调用场景：
      is_task_running() 中在 /proc 检查之后使用。
    """
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=1,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    stat = result.stdout.strip().splitlines()
    if not stat:
        return False
    return stat[0].strip().startswith("Z")


def _send_posix_signal(pid: int, sig: signal.Signals) -> str:
    """
    向 POSIX 进程或进程组发送信号。

    核心作用：
      先尝试向进程组发送信号，失败再尝试向进程本身发送。

    输入参数：
      pid : int — 目标进程 ID。
      sig : signal.Signals — 要发送的信号（如 SIGTERM / SIGKILL）。

    返回值：
      str — "sent" / "not_found" / "permission_denied" / "failed" 之一。

    调用场景：
      terminate_task() 中在 POSIX 系统下停止任务进程。
    """
    try:
        pgid = os.getpgid(pid)
    except ProcessLookupError:
        return "not_found"
    except PermissionError:
        pgid = pid
    except OSError:
        pgid = pid

    saw_permission_error = False
    for sender in (lambda: os.killpg(pgid, sig), lambda: os.kill(pid, sig)):
        try:
            sender()
            return "sent"
        except ProcessLookupError:
            return "not_found"
        except PermissionError:
            saw_permission_error = True
        except OSError:
            continue

    return "permission_denied" if saw_permission_error else "failed"


def terminate_task(pid: int) -> str:
    """
    终止指定 PID 的抢票任务进程。

    核心作用：
      Windows 下使用 taskkill（先普通终止，超时后强制终止）；
      POSIX 下先发送 SIGTERM，超时后发送 SIGKILL。

    输入参数：
      pid : int — 要终止的进程 ID。

    返回值：
      str — 操作结果提示文本。

    调用场景：
      stop_task() / remove_task() 中停止运行中任务。
    """
    if not is_task_running(pid):
        return "任务进程已结束。"

    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return "停止任务进程失败。"

        deadline = time.time() + 3
        while time.time() < deadline:
            if not is_task_running(pid):
                return "已停止任务进程。"
            time.sleep(0.1)

        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        except OSError:
            return "强制停止任务进程失败。"

        return "已发送停止任务请求"

    terminate_result = _send_posix_signal(pid, signal.SIGTERM)
    if terminate_result == "not_found":
        return "任务进程已结束。"
    if terminate_result == "permission_denied":
        return "停止任务进程失败：没有权限。"
    if terminate_result == "failed":
        return "停止任务进程失败。"

    deadline = time.time() + 3
    while time.time() < deadline:
        if not is_task_running(pid):
            return "已停止任务进程。"
        time.sleep(0.1)

    kill_result = _send_posix_signal(pid, signal.SIGKILL)
    if kill_result == "not_found":
        return "已停止任务进程。"
    if kill_result == "permission_denied":
        return "强制停止任务进程失败：没有权限。"
    if kill_result == "failed":
        return "强制停止任务进程失败。"

    return "已强制停止任务进程。"


def sync_task_statuses() -> list:
    """
    同步所有任务状态。

    核心作用：
      1. 获取 GlobalStatusInstance 中的任务条目。
      2. 尝试从日志末尾提取支付二维码 URL 并回填到条目。
      3. 根据日志标记（完成/停止）或进程运行状态更新任务状态。

    返回值：
      list — 更新后的任务条目列表。

    调用场景：
      visible_task_entries() 中调用，是任务面板刷新时的状态来源。
    """
    entries = GlobalStatusInstance.get_task_logs()
    for entry in entries:
        payment_qr_url = extract_payment_qr_url(entry.log_file)
        if payment_qr_url:
            entry.payment_qr_url = payment_qr_url
        if not entry.pid:
            continue
        if entry.status == TASK_STATUS_STOPPED:
            continue
        if log_contains_marker(entry.log_file, TASK_STOPPED_MARKER):
            GlobalStatusInstance.update_task_log_status(entry.pid, TASK_STATUS_STOPPED)
            continue
        if log_contains_marker(entry.log_file, TASK_COMPLETED_MARKER):
            GlobalStatusInstance.update_task_log_status(
                entry.pid, TASK_STATUS_COMPLETED
            )
            continue
        if is_task_running(entry.pid):
            GlobalStatusInstance.update_task_log_status(entry.pid, TASK_STATUS_RUNNING)
        elif entry.status == TASK_STATUS_RUNNING:
            GlobalStatusInstance.update_task_log_status(entry.pid, TASK_STATUS_EXITED)
    return GlobalStatusInstance.get_task_logs()


def visible_task_entries():
    """
    获取当前可见任务条目（已同步状态）。

    返回值：
      list — 可见任务条目列表。

    调用场景：
      render_task_manager_panel() / read_task_log_locations() / go_start_tab() 中调用。
    """
    return sync_task_statuses()


def log_contains_marker(log_file: str, marker: str) -> bool:
    """
    检查日志文件末尾是否包含指定标记字符串。

    输入参数：
      log_file : str — 日志文件路径。
      marker   : str — 要查找的标记字符串。

    返回值：
      bool — True 表示找到标记。

    调用场景：
      sync_task_statuses() 中检测任务是否完成或被用户停止。
    """
    try:
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 8192))
            content = handle.read().decode("utf-8", errors="replace")
        return marker in content
    except OSError:
        return False


def extract_payment_qr_url(log_file: str) -> str | None:
    """
    从日志文件末尾提取支付二维码 URL。

    核心作用：
      扫描日志最后 16KB，查找 "PAYMENT_QR_URL=" 标记并返回其后的 URL。

    输入参数：
      log_file : str — 日志文件路径。

    返回值：
      str | None — 支付二维码 URL；未找到返回 None。

    调用场景：
      sync_task_statuses() 中回填任务条目的 payment_qr_url。
    """
    try:
        with open(log_file, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 16384))
            content = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return None

    marker = "PAYMENT_QR_URL="
    for line in reversed(content.splitlines()):
        if marker in line:
            return line.split(marker, 1)[1].strip()
    return None


def refresh_task_panel():
    """
    刷新任务面板（仅刷新令牌与可见性）。

    返回值：
      tuple — (新刷新令牌, 任务面板可见性 update)。

    调用场景：
      tab.go 中 go_btn 点击后的 then 链式回调。
    """
    return _refresh_token(), gr.update(visible=bool(visible_task_entries()))


def refresh_task_panel_with_payments():
    """
    刷新任务面板并返回检测到的支付 URL 列表。

    返回值：
      tuple — (新刷新令牌, 任务面板可见性 update, 支付 URL JSON 字符串)。

    调用场景：
      render_task_manager_panel() 中手动刷新按钮与自动刷新定时器触发。
    """
    entries = visible_task_entries()
    urls = [entry.payment_qr_url for entry in entries if entry.payment_qr_url]
    return (
        _refresh_token(),
        gr.update(visible=bool(entries)),
        json.dumps(urls, ensure_ascii=False),
    )


def refresh_log_panel():
    """
    刷新日志文件列表面板。

    返回值：
      tuple — (新刷新令牌, 日志面板可见性 update)。

    调用场景：
      clear_log_files() 中清除操作后刷新日志列表。
    """
    return _refresh_token(), gr.update(visible=True)


def stop_task(pid: int):
    """
    停止指定 PID 的任务并刷新任务面板。

    核心作用：
      调用 terminate_task() 终止进程，向日志写入停止标记，并更新状态为已主动结束。

    输入参数：
      pid : int — 要停止的任务进程 ID。

    返回值：
      tuple — refresh_task_panel() 返回的刷新结果。

    调用场景：
      render_task_manager_panel() 中运行中任务卡的“终止任务”按钮点击时触发。
    """
    entry = GlobalStatusInstance.get_task_log(pid)
    message = terminate_task(pid)
    if entry and entry.log_file:
        append_stop_log(entry.log_file, entry.title, message)
    GlobalStatusInstance.update_task_log_status(pid, TASK_STATUS_STOPPED)
    if "强制" in message:
        gr.Warning(message)
    else:
        gr.Info(message)
    return refresh_task_panel()


def remove_task(pid: int):
    """
    移除指定 PID 的任务卡片。

    核心作用：
      若任务仍在运行则先终止，然后从 GlobalStatusInstance 中移除任务记录。

    输入参数：
      pid : int — 要移除的任务进程 ID。

    返回值：
      tuple — refresh_task_panel() 返回的刷新结果。

    调用场景：
      render_task_manager_panel() 中任务卡的“移除”按钮点击时触发。
    """
    entry = GlobalStatusInstance.get_task_log(pid)
    if entry is None:
        gr.Warning("任务记录不存在。")
        return refresh_task_panel()

    if entry.status == TASK_STATUS_RUNNING:
        message = terminate_task(pid)
        if entry.log_file:
            append_stop_log(entry.log_file, entry.title, message)

    GlobalStatusInstance.remove_task_log(pid)
    gr.Info("已移除任务卡。")
    return refresh_task_panel()


def append_stop_log(log_file: str, title: str, message: str) -> None:
    """
    向日志文件追加停止标记与停止信息。

    输入参数：
      log_file : str — 日志文件路径。
      title    : str — 任务标题。
      message  : str — 停止结果消息。

    返回值：
      无。

    调用场景：
      stop_task() / remove_task() 中记录用户主动停止操作。
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(log_file, "a", encoding="utf-8", errors="replace") as handle:
            handle.write("{0}\n".format(TASK_STOPPED_MARKER))
            handle.write(
                "\n[{0}] 已停止任务: {1} ({2})\n".format(timestamp, title, message)
            )
    except OSError:
        pass


def read_task_log_locations():
    """
    读取当前任务列表并渲染为 HTML 任务卡片网格。

    返回值：
      str — 完整 HTML 字符串，包含每个任务的状态、创建时间、日志路径和查看链接。

    调用场景：
      当前未被 render_task_manager_panel() 直接调用，保留作为独立的任务卡片渲染入口。
    """
    entries = visible_task_entries()
    if not entries:
        return build_main_log_card()

    items: list[str] = [build_main_log_card(), '<div class="btb-task-grid">']
    for entry in entries:
        created_at = datetime.fromtimestamp(entry.created_at).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        status_class = _status_class(entry.status)
        items.append(
            """
            <article class="btb-task-card {status_class}">
              <div class="btb-task-card__head">
                <div class="btb-task-card__title">{title}</div>
                <span class="btb-task-status {status_class}">{status}</span>
              </div>
              <div class="btb-task-card__meta">创建于 {created_at}</div>
              {log_path}
              <div class="btb-task-card__actions">
                {log_action}
              </div>
            </article>
            """.format(
                created_at=html.escape(created_at),
                title=html.escape(entry.title),
                status=html.escape(entry.status),
                status_class=html.escape(status_class),
                log_path=_render_log_path(entry.log_file),
                log_action=_render_log_view_action(entry.log_file),
            )
        )
    items.append("</div>")
    return "".join(items)


def render_task_manager_panel(task_panel):
    """
    在指定 Gradio 容器中渲染任务管理面板。

    核心作用：
      1. 创建刷新令牌 State、支付 URL 总线、自动刷新 Timer。
      2. 使用 @gr.render 根据刷新令牌动态渲染任务卡片。
      3. 每张卡片显示任务标题、状态、创建时间、日志路径、查看链接，
         运行中任务额外显示“终止任务”按钮，所有卡片显示“移除”按钮。
      4. 绑定手动刷新、定时刷新与支付 URL 变更触发前端打开支付页面的逻辑。

    输入参数：
      task_panel : gr.Column — 任务面板容器组件。

    返回值：
      gr.State — 刷新令牌状态组件，供外部触发重新渲染。

    调用场景：
      tab.go 的 go_start_tab() 中构建任务管理区域时调用。
    """
    refresh_token = gr.State(_refresh_token())
    payment_url_bus = gr.Textbox(visible=False)
    auto_refresh_timer = gr.Timer(value=2.0)
    with gr.Row(elem_classes="btb-task-toolbar-row"):
        gr.HTML("""<div class="btb-card-head"><div><h3>抢票任务管理</h3></div></div>""")
        refresh_btn = gr.Button(
            "刷新",
            elem_classes="btb-soft-button btb-task-button",
            scale=0,
            min_width=84,
        )

    @gr.render(inputs=refresh_token)
    def render_task_cards(_refresh_value):
        """
        根据刷新令牌渲染任务卡片列表。

        输入参数：
          _refresh_value : int — 刷新令牌值（仅用于触发重新渲染）。

        返回值：
          无（直接渲染 Gradio 组件到 task_panel）。

        调用场景：
          render_task_manager_panel() 中由 @gr.render 装饰器自动调用。
        """
        gr.HTML(build_main_log_card())
        with gr.Column(elem_classes="btb-task-grid"):
            for entry in visible_task_entries():
                status_class = _status_class(entry.status)
                with gr.Column(elem_classes=f"btb-task-card {status_class}"):
                    gr.HTML(
                        """
                        <div class="btb-task-card__head">
                          <div class="btb-task-card__title">{title}</div>
                          <span class="btb-task-status {status_class}">{status}</span>
                        </div>
                        <div class="btb-task-card__meta">创建于 {created_at}</div>
                        {log_path}
                        """.format(
                            title=html.escape(entry.title),
                            status=html.escape(entry.status),
                            status_class=html.escape(status_class),
                            created_at=html.escape(
                                datetime.fromtimestamp(entry.created_at).strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                )
                            ),
                            log_path=_render_log_path(entry.log_file),
                        )
                    )
                    with gr.Row(elem_classes="btb-task-card__actions"):
                        gr.Button(
                            "查看",
                            elem_classes="btb-soft-button btb-task-button btb-task-button--view",
                            link=build_log_view_url(entry.log_file),
                            link_target="_blank",
                            scale=0,
                            min_width=84,
                        )
                        if entry.status == TASK_STATUS_RUNNING and entry.pid:
                            stop_btn = gr.Button(
                                "终止任务",
                                elem_classes="btb-soft-button btb-task-button btb-task-button--stop",
                                scale=0,
                                min_width=92,
                            )
                            stop_btn.click(
                                fn=lambda pid=entry.pid: stop_task(pid),
                                outputs=[refresh_token, task_panel],
                            )
                        remove_btn = gr.Button(
                            "移除",
                            elem_classes="btb-soft-button btb-task-button btb-task-button--remove",
                            scale=0,
                            min_width=84,
                        )
                        remove_btn.click(
                            fn=lambda pid=entry.pid: remove_task(pid),
                            outputs=[refresh_token, task_panel],
                        )

    refresh_btn.click(
        fn=refresh_task_panel_with_payments,
        inputs=None,
        outputs=[refresh_token, task_panel, payment_url_bus],
    )
    auto_refresh_timer.tick(
        fn=refresh_task_panel_with_payments,
        inputs=None,
        outputs=[refresh_token, task_panel, payment_url_bus],
        show_progress="hidden",
    )
    payment_url_bus.change(
        fn=None,
        inputs=payment_url_bus,
        outputs=None,
        js=OPEN_PAYMENT_URLS_JS,
    )
    return refresh_token


def log_tab():
    """
    构建“日志文件列表”独立标签页。

    核心作用：
      1. 列出 LOG_DIR 下所有日志文件，按修改时间倒序展示。
      2. 若日志文件关联了任务条目，则显示任务标题与状态；否则仅显示文件名与更新时间。
      3. 提供“刷新”与“一键清除”按钮。

    返回值：
      tuple — (refresh_token, task_panel)，供 ticker.py 注册标签页。

    调用场景：
      ticker_cmd() 中注册“日志”标签页时调用。
    """
    refresh_token = gr.State(_refresh_token())
    with gr.Column(elem_classes="btb-card btb-card-sky btb-layout-card") as task_panel:
        with gr.Row(elem_classes="btb-task-toolbar-row"):
            gr.HTML(
                """
                <div class="btb-card-head">
                    <div>
                        <h3>日志文件列表</h3>
                        <p>这里显示日志目录中的文件路径，可以自行去文件系统中查看。</p>
                    </div>
                </div>
                """
            )
            refresh_btn = gr.Button(
                "刷新",
                elem_classes="btb-soft-button btb-task-button",
                scale=0,
                min_width=84,
            )
            clear_btn = gr.Button(
                "一键清除",
                elem_classes="btb-soft-button btb-task-button btb-task-button--remove",
                scale=0,
                min_width=92,
            )

        @gr.render(inputs=refresh_token)
        def render_log_files(_refresh_value):
            """
            根据刷新令牌渲染日志文件列表。

            输入参数：
              _refresh_value : int — 刷新令牌值（仅用于触发重新渲染）。

            返回值：
              无（直接渲染 Gradio 组件到 task_panel）。

            调用场景：
              log_tab() 中由 @gr.render 装饰器自动调用。
            """
            log_files = _list_log_files()
            if not log_files:
                gr.HTML(
                    """
                    <div class="btb-card-note">
                        当前日志目录里还没有文件。
                    </div>
                    """
                )
                return

            with gr.Column(elem_classes="btb-task-grid"):
                for log_file in log_files:
                    entry = _find_task_entry_by_log_file(log_file)
                    title = (
                        entry.title if entry is not None else os.path.basename(log_file)
                    )
                    status = entry.status if entry is not None else "日志文件"
                    status_class = (
                        _status_class(entry.status)
                        if entry is not None
                        else "is-exited"
                    )
                    created_at = datetime.fromtimestamp(
                        os.path.getmtime(log_file)
                    ).strftime("%Y-%m-%d %H:%M:%S")

                    with gr.Column(elem_classes=f"btb-task-card {status_class}"):
                        gr.HTML(
                            """
                            <div class="btb-task-card__head">
                              <div class="btb-task-card__title">{title}</div>
                              <span class="btb-task-status {status_class}">{status}</span>
                            </div>
                            <div class="btb-task-card__meta">更新时间 {created_at}</div>
                            {log_path}
                            """.format(
                                title=html.escape(title),
                                status=html.escape(status),
                                status_class=html.escape(status_class),
                                created_at=html.escape(created_at),
                                log_path=_render_log_path(log_file),
                            )
                        )
                        gr.Button(
                            "查看",
                            elem_classes="btb-soft-button btb-task-button btb-task-button--view",
                            link=build_log_view_url(log_file),
                            link_target="_blank",
                            scale=0,
                            min_width=84,
                        )

        refresh_btn.click(
            fn=lambda: _refresh_token(),
            inputs=None,
            outputs=[refresh_token],
        )
        clear_btn.click(
            fn=clear_log_files,
            inputs=None,
            outputs=[refresh_token, task_panel],
        )

    return refresh_token, task_panel
