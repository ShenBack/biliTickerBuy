"""
tab/go.py — 抢票操作与任务管理页面。

文件整体功能：
  提供 Gradio UI 的“操作抢票”标签页（go_start_tab），包含以下核心能力：
  1. 上传抢票配置文件（JSON），支持多文件同时上传，每个文件对应一个独立抢票任务。
  2. 配置预览：解析并渲染上传配置中的账号、票数、单价、购票人实名等信息。
  3. 代理状态监控：展示已配置代理的连通性、延时、使用状态、出口 IP 等。
  4. 抢票时间选择：通过原生 datetime-local 输入框设置抢票开始时间，支持自动填写。
  5. 任务启动：根据代理分配策略（均衡/队列）启动子进程执行抢票，自动限制并发数量。
  6. 任务面板集成：与 tab.log 联动，展示运行中任务的实时日志与停止按钮。

所属模块：UI 层 (tab)
依赖文件：
  - app_cmd.config.BuyConfig        (抢票配置对象)
  - tab.log                         (任务面板刷新与渲染)
  - task.buy                        (buy_new_terminal，启动终端抢票子进程)
  - util                            (ConfigDB / GlobalStatusInstance / LOG_DIR / time_service)
  - util.Constant                   (BEIJING_TZ / DEFAULT_REQUEST_INTERVAL 等常量)

对外能力：
  - go_start_tab() → 返回 (task_refresh_token, task_panel, load_go_start_configs, [interval_ui])，
    供 ticker.py 注册“操作抢票”标签页与初始化回调。
"""

import datetime
import html
import json
import os
import threading
import time
import uuid

import gradio as gr
from gradio import SelectData
from loguru import logger

from app_cmd.config.BuyConfig import BuyConfig
from tab.log import refresh_task_panel, render_task_manager_panel, visible_task_entries
from task.buy import buy_new_terminal
from util import (
    ConfigDB,
    GlobalStatusInstance,
    LOG_DIR,
    runtime_state_reader,
    runtime_state_writer,
    time_service,
)
from util.Constant import (
    BEIJING_TZ,
    DEFAULT_MAX_LOG_FILES,
    DEFAULT_MAX_RUN_DIRS,
    DEFAULT_REQUEST_INTERVAL,
    GO_UPLOADED_FILES_STATE_KEY,
)


def withTimeString(string):
    """
    为字符串添加当前北京时间前缀。

    输入参数：
      string : str — 原始文本。

    返回值：
      str — 形如 "2025-08-01 12:00:00: 原始文本" 的字符串。

    调用场景：
      抢票任务输出日志时常用，便于按北京时间定位事件。
    """
    return (
        f"{datetime.datetime.now(BEIJING_TZ).strftime('%Y-%m-%d %H:%M:%S')}: {string}"
    )


def _build_task_log_path(filename: str) -> str:
    """
    为任务生成唯一日志文件路径。

    核心作用：
      基于文件名和 UUID 短码构建安全的日志文件路径，避免同名冲突。

    输入参数：
      filename : str — 原始配置文件名。

    返回值：
      str — LOG_DIR 下的日志文件绝对路径。

    调用场景：
      launch_task() 中为每个新启动的抢票子进程分配独立日志文件。
    """
    filename_only = os.path.splitext(os.path.basename(filename))[0]
    safe_name = "".join(
        ch if ch.isalnum() or ch in "-_." else "_" for ch in filename_only
    )
    safe_name = safe_name.strip("._") or "task"
    return os.path.join(LOG_DIR, f"{safe_name}_{uuid.uuid4().hex[:8]}.log")


def _parse_sale_start(value) -> datetime.datetime | None:
    """
    解析 sale_start 字段为北京时间 datetime 对象。

    支持的输入：
      - int/float 时间戳（秒）
      - 字符串 "%Y-%m-%d %H:%M:%S" 或 "%Y-%m-%dT%H:%M:%S"

    输入参数：
      value : int | float | str — 原始 sale_start 值。

    返回值：
      datetime.datetime | None — 解析成功返回带 BEIJING_TZ 时区的 datetime；否则 None。

    调用场景：
      auto_fill_time() 中用于统一处理配置文件里的起售时间字段。
    """
    if isinstance(value, (int, float)):
        return datetime.datetime.fromtimestamp(value, tz=BEIJING_TZ)
    if isinstance(value, str) and value.strip():
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
            except ValueError:
                continue
    return None


def _preview_value(value) -> str:
    """
    将任意值格式化为可展示字符串，空值显示为 "-"。

    输入参数：
      value : Any — 待展示值。

    返回值：
      str — 列表会拼接为 "、" 分隔；None/""/[] 返回 "-"。

    调用场景：
      _render_ticket_preview() 中格式化配置字段显示。
    """
    if value in (None, "", []):
        return "-"
    if isinstance(value, list):
        return "、".join(str(item) for item in value) if value else "-"
    return str(value)


def _format_price_cents(value) -> str:
    """
    将分单位价格格式化为人民币字符串。

    输入参数：
      value : int | float | str — 价格（单位：分）。

    返回值：
      str — 形如 "¥128.00"；解析失败回退到 _preview_value。

    调用场景：
      _render_ticket_preview() 中显示票价。
    """
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return _preview_value(value)
    return f"¥{amount / 100:.2f}"


def _format_buyer_identity(buyer_info) -> str:
    """
    格式化购票人身份信息为可展示文本。

    核心作用：
      将 buyer_info 列表中的姓名与证件类型映射为 "姓名（证件类型）" 的拼接文本。

    输入参数：
      buyer_info : list[dict] — 购票人信息列表，每项含 name 与 id_type。

    返回值：
      str — 多个购票人以 "、" 分隔；无有效数据返回 "-"。

    调用场景：
      _render_ticket_preview() 中展示实名购票人。
    """
    if not isinstance(buyer_info, list) or not buyer_info:
        return "-"

    id_type_map = {
        0: "身份证",
        1: "护照",
        2: "港澳居民来往内地通行证",
        3: "台湾居民来往大陆通行证",
        4: "外国人永久居留身份证",
    }
    items: list[str] = []
    for buyer in buyer_info:
        if not isinstance(buyer, dict):
            continue
        name = _preview_value(buyer.get("name"))
        id_type = id_type_map.get(buyer.get("id_type"), "未知证件")
        items.append(f"{name}（{id_type}）")
    return "、".join(items) if items else "-"


def _render_ticket_preview(config: dict) -> str:
    """
    渲染单个抢票配置的预览 HTML。

    核心作用：
      根据配置字典生成包含账号、票数、单价、详细信息和实名购票人的紧凑卡片 HTML。

    输入参数：
      config : dict — 抢票配置字典。

    返回值：
      str — HTML 字符串，供 Gradio HTML 组件展示。

    调用场景：
      upload() / file_select_handler() / _build_session_ticket_preview() 中刷新预览区。
    """
    items = [
        ("账号", _preview_value(config.get("username"))),
        ("票数", _preview_value(config.get("count"))),
        ("单价", _format_price_cents(config.get("pay_money"))),
    ]
    item_html = "".join(
        (
            '<div class="btb-mini-card">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{html.escape(value)}</span>"
            "</div>"
        )
        for label, value in items
    )
    return f"""
    <div class="btb-ticket-panel btb-ticket-panel--compact">
        <div class="btb-mini-grid btb-mini-grid--triple">{item_html}</div>
        <div class="btb-mini-card btb-ticket-panel__delivery">
            <strong>详细信息</strong>
            <span>{html.escape(_preview_value(config.get("detail") or "-"))}</span>
            <span>{html.escape(f"实名：{_format_buyer_identity(config.get('buyer_info'))}")}</span>
        </div>
    </div>
    """


@runtime_state_reader(GO_UPLOADED_FILES_STATE_KEY, kind="path_list")
def _get_session_upload_files() -> list[str]:
    """
    从运行时状态读取当前会话已上传的文件路径列表。

    返回值：
      list[str] — 上传文件路径列表；无数据默认返回空列表。

    调用场景：
      页面初始化与上传事件后恢复文件列表；upload_ui 的 value 与 _build_session_ticket_preview() 使用。
    """
    return []


def _build_session_ticket_preview() -> str:
    """
    构建当前会话的票务预览 HTML。

    核心作用：
      若存在已上传文件，读取第一个 JSON 配置并渲染预览；失败则展示错误信息。

    返回值：
      str — HTML 预览字符串。

    调用场景：
      ticket_ui 组件的 value 初始化回调。
    """
    files = _get_session_upload_files()
    if not files:
        return _render_ticket_preview({})
    try:
        with open(files[0], "r", encoding="utf-8") as file:
            content = json.load(file)
        return _render_ticket_preview(content)
    except Exception as e:
        return (
            f'<div class="btb-card-note">配置预览恢复失败：{html.escape(str(e))}</div>'
        )


def go_start_tab():
    """
    构建“操作抢票”标签页。

    核心作用：
      1. 创建 Gradio 组件：文件上传区、配置预览、代理状态表格、抢票时间选择器、
         抢票间隔输入框、开始抢票按钮、任务管理面板。
      2. 绑定交互事件：上传/选中文件时预览配置、自动填写起售时间、
         刷新代理状态、点击开始抢票后启动子进程并刷新任务面板。
      3. 支持两种代理分配策略：
         - balanced（默认）：将代理均匀分配给每个任务。
         - queue：队列模式，每个 worker 对应一个代理槽，顺序消费任务。
      4. 限制并发数量：运行中任务数 + 新启动任务数不得超过可用代理总数。

    返回值：
      tuple — (task_refresh_token, task_panel, load_go_start_configs, [interval_ui])，
              供 ticker.py 注册页面与初始化加载回调使用。

    调用场景：
      ticker_cmd() 中注册“操作抢票”标签页时调用。
    """
    auto_fill_time_default = ConfigDB.get("autoFillTime")
    if auto_fill_time_default is None:
        auto_fill_time_default = True

    def get_proxy_status():
        """
        获取代理状态信息并渲染为 HTML 表格。

        核心作用：
          1. 读取 ConfigDB 中 https_proxy 配置，拆分为代理列表。
          2. 使用 ProxyTester 逐个测试代理连通性与延时。
          3. 查询 GlobalStatusInstance 获取代理当前被哪些任务占用。
          4. 汇总为 HTML 表格展示。

        返回值：
          str — HTML 表格或提示文本。

        调用场景：
          proxy_status_ui 初始 value 与 refresh_proxy_btn 点击回调。
        """
        try:
            from util.proxy.ProxyTester import ProxyTester
            from util import GlobalStatusInstance
            https_proxys = ConfigDB.get("https_proxy") or ""
            if not https_proxys.strip():
                return '<div class="btb-card-note">未配置代理，使用直连</div>'

            proxy_list = [p.strip() for p in https_proxys.split(",") if p.strip()]
            if not proxy_list:
                return '<div class="btb-card-note">未配置代理，使用直连</div>'

            tester = ProxyTester(timeout=5)
            results = []
            for proxy in proxy_list:
                result = tester.test_single_proxy(proxy)
                # 获取代理使用情况
                usage = GlobalStatusInstance.get_proxy_usage(proxy)
                result["usage"] = usage
                results.append(result)

            html_parts = ['<div class="btb-proxy-status">']
            html_parts.append('<h4>代理状态</h4>')
            html_parts.append('<table style="width:100%; border-collapse:collapse;">')
            html_parts.append('<tr style="border-bottom:1px solid #3b4252;">')
            html_parts.append('<th style="text-align:left; padding:8px;">代理</th>')
            html_parts.append('<th style="text-align:left; padding:8px;">连通性</th>')
            html_parts.append('<th style="text-align:left; padding:8px;">延时</th>')
            html_parts.append('<th style="text-align:left; padding:8px;">使用状态</th>')
            html_parts.append('<th style="text-align:left; padding:8px;">出口IP</th>')
            html_parts.append('</tr>')

            for r in results:
                status_color = "#a3be8c" if r["status"] == "success" else "#bf616a"
                status_text = "正常" if r["status"] == "success" else "不可用"
                latency = f"{r['response_time']}ms" if r["response_time"] else "-"
                ip_info = r.get("ip_info", "-") or "-"
                usage = r.get("usage", [])
                if usage:
                    usage_text = f"使用中 ({len(usage)}个任务)"
                    usage_color = "#ebcb8b"
                else:
                    usage_text = "空闲"
                    usage_color = "#a3be8c"

                html_parts.append('<tr style="border-bottom:1px solid #2e3440;">')
                html_parts.append(f'<td style="padding:8px;">{r["proxy"]}</td>')
                html_parts.append(f'<td style="padding:8px; color:{status_color};">{status_text}</td>')
                html_parts.append(f'<td style="padding:8px;">{latency}</td>')
                html_parts.append(f'<td style="padding:8px; color:{usage_color};">{usage_text}</td>')
                html_parts.append(f'<td style="padding:8px; font-size:0.9em;">{ip_info}</td>')
                html_parts.append('</tr>')

            html_parts.append('</table>')
            html_parts.append('</div>')
            return "".join(html_parts)
        except Exception as e:
            return f'<div class="btb-card-note">获取代理状态失败: {e}</div>'
    with gr.Column(elem_classes="btb-page-section"):
        with gr.Column(elem_classes="btb-card btb-card-sky btb-layout-card"):
            with gr.Row(elem_classes="!items-stretch !gap-3"):
                upload_ui = gr.Files(
                    label="每一个上传的文件都会启动一个抢票程序",
                    file_count="multiple",
                    value=_get_session_upload_files,
                    scale=5,
                )
                with gr.Column(scale=4):
                    ticket_ui = gr.HTML(
                        value=_build_session_ticket_preview,
                        visible=True,
                    )
            # 代理状态显示区域
            proxy_status_ui = gr.HTML(
                value=get_proxy_status,
                label="代理状态",
            )
            refresh_proxy_btn = gr.Button(
                "刷新代理状态",
                elem_classes="btb-soft-button",
                scale=0,
                min_width=150,
            )
            refresh_proxy_btn.click(
                fn=get_proxy_status,
                inputs=None,
                outputs=proxy_status_ui,
            )
            with gr.Column(elem_classes="btb-card btb-card-sky btb-layout-card"):
                gr.HTML(
                    """
                    <div class="btb-card-head">
                        <div>
                            <h4>选择抢票时间</h3>
                            <p>
                                这里的时间按<strong>北京时间（UTC+8）</strong>填写。
                            </p>
                        </div>
                    </div>
                    """,
                    label="选择抢票的时间",
                )
                gr.HTML(
                    """
                    <div class="btb-time-picker-card">
                        <label class="btb-time-picker-card__label" for="datetime">
                            抢票开始时间
                        </label>
                        <input
                            type="datetime-local"
                            id="datetime"
                            name="datetime"
                            step="1"
                            class="btb-native-datetime-input"
                        >
                    </div>
                    """
                )
            with gr.Row(elem_classes="!justify-end"):
                auto_fill_time_btn = gr.Button(
                    "自动填写抢票时间",
                    elem_classes="btb-soft-button",
                    scale=0,
                    min_width=220,
                )

        with gr.Row(elem_classes="btb-inline-actions !justify-end"):
            interval_ui = gr.Number(
                label="抢票间隔",
                value=None,
                minimum=1,
                info="默认抢票请求间隔（单位：毫秒）",
            )

    @runtime_state_writer(GO_UPLOADED_FILES_STATE_KEY, kind="path_list")
    def upload(filepath):
        """
        文件上传回调：读取 JSON 配置并渲染预览。

        输入参数：
          filepath : list[str] — 上传文件路径列表。

        返回值：
          gradio.update — 更新 ticket_ui 的 value 与 visible。

        调用场景：
          upload_ui.upload 事件触发。
        """
        try:
            with open(filepath[0], "r", encoding="utf-8") as file:
                content = json.load(file)
            return gr.update(value=_render_ticket_preview(content), visible=True)
        except Exception as e:
            return gr.update(
                value=(
                    '<div class="btb-card-note">配置预览失败：'
                    f"{html.escape(str(e))}</div>"
                ),
                visible=True,
            )

    def file_select_handler(select_data: SelectData, files):
        """
        文件列表选中回调：预览当前选中的配置文件。

        输入参数：
          select_data : SelectData — Gradio 选择事件数据，含 index。
          files       : list — 当前文件列表。

        返回值：
          str — HTML 预览字符串。

        调用场景：
          upload_ui.select 事件触发。
        """
        file_label = files[select_data.index]
        try:
            with open(file_label, "r", encoding="utf-8") as file:
                content = json.load(file)
            return _render_ticket_preview(content)
        except Exception as e:
            return (
                f'<div class="btb-card-note">配置预览失败：{html.escape(str(e))}</div>'
            )

    def auto_fill_time(files):
        """
        自动填写抢票时间：取所有配置中最晚的 sale_start。

        核心作用：
          1. 遍历上传文件，解析每个配置的 sale_start。
          2. 若 sale_start 无效，抛出 gr.Error。
          3. 若已过时，提示无需填写并返回空字符串。
          4. 若多个配置 sale_start 不一致，取最晚时间并警告用户。

        输入参数：
          files : list[str] — 上传的配置文件路径列表。

        返回值：
          str — datetime-local 格式字符串（YYYY-MM-DDTHH:MM:SS），或空字符串。

        调用场景：
          auto_fill_time_btn 点击与 upload_ui.upload 的 then 链式回调中触发。
        """
        if not files:
            gr.Warning("请先上传至少一个抢票配置文件。")
            return ""

        sale_start_items: list[tuple[str, datetime.datetime]] = []
        adjusted_now = datetime.datetime.fromtimestamp(
            time.time() + time_service.get_timeoffset(),
            tz=BEIJING_TZ,
        )

        for filepath in files:
            with open(filepath, "r", encoding="utf-8") as file:
                config = json.load(file)

            sale_start = _parse_sale_start(
                config.get("sale_start", config.get("saleStart"))
            )
            if sale_start is None:
                raise gr.Error("缺少有效的 sale_start，请重新生成该配置。")
            sale_start_items.append((os.path.basename(filepath), sale_start))

        latest_sale_start = max(sale_start for _, sale_start in sale_start_items)
        unique_sale_starts = sorted({sale_start for _, sale_start in sale_start_items})
        if latest_sale_start <= adjusted_now:
            gr.Warning("已经过起售时间，不需要填写抢票时间。\n")
            return ""

        autofill_value = latest_sale_start.strftime("%Y-%m-%dT%H:%M:%S")
        if len(unique_sale_starts) == 1:
            gr.Info("已自动填写抢票时间。\n")
            return autofill_value

        gr.Warning(
            "抢票的起始时间不一样，已自动填写为最晚的起售时间，确保所有票档届时都已开始抢票。\n"
        )
        return autofill_value

    def split_proxies(https_proxy_list: list[str], task_num: int) -> list[list[str]]:
        """
        将代理列表按任务数轮询拆分。

        核心作用：
          实现均衡分配策略：第 i 个代理分配给第 i % task_num 个任务。

        输入参数：
          https_proxy_list : list[str] — 可用代理列表（含 "none" 直连）。
          task_num         : int — 任务数量。

        返回值：
          list[list[str]] — 每个子列表对应该任务分配到的代理。

        调用场景：
          start_go() 的 balanced 模式中使用。
        """
        assigned_proxies: list[list[str]] = [[] for _ in range(task_num)]
        for i, proxy in enumerate(https_proxy_list):
            assigned_proxies[i % task_num].append(proxy)
        return assigned_proxies

    def launch_task(
        filename: str,
        *,
        config: BuyConfig,
    ):
        """
        启动单个抢票子进程。

        核心作用：
          1. 读取配置文件内容。
          2. 生成唯一日志路径。
          3. 调用 buy_new_terminal() 启动子进程。
          4. 在 GlobalStatusInstance 注册任务日志与 PID。

        输入参数：
          filename : str — 抢票配置文件路径。
          config   : BuyConfig — 已设置好覆盖项的抢票配置对象。

        返回值：
          subprocess.Popen — 已启动的子进程对象。

        调用场景：
          start_go() 的 balanced 与 queue 模式中调用。
        """
        with open(filename, "r", encoding="utf-8") as file:
            content = file.read()
        filename_only = os.path.basename(filename)
        logger.info(f"启动 {filename_only}")
        log_file_path = _build_task_log_path(filename_only)
        logger.info(f"任务 {filename_only} 的日志文件：{log_file_path}")
        proc = buy_new_terminal(
            config=config.with_overrides(tickets_info=content),
            log_file_path=log_file_path,
        )
        GlobalStatusInstance.register_task_log(
            title=filename_only,
            mode="终端",
            log_file=log_file_path,
            pid=proc.pid,
        )
        return proc

    def start_go(files, time_start, interval):
        """
        “开始抢票”按钮主回调。

        核心作用：
          1. 校验文件、解析抢票间隔并持久化到 ConfigDB。
          2. 读取代理配置，检查并发限制（运行中任务 + 新任务 <= 可用代理数）。
          3. 若启用 autoCleanupLogs，先清理过期日志与运行目录。
          4. 根据 proxyAssignmentStrategy 选择启动模式：
             - queue：启动若干 daemon worker 线程，顺序消费文件列表。
             - balanced（默认）：将代理均匀拆分后，为每个文件启动独立子进程。
          5. 刷新任务面板。

        输入参数：
          files      : list[str] — 上传的配置文件路径列表。
          time_start : str — 用户设置的抢票开始时间（datetime-local 字符串）。
          interval   : int | str | None — 抢票间隔毫秒数。

        返回值：
          gradio.update — 更新任务面板可见性。

        调用场景：
          go_btn 按钮点击时触发。
        """
        if not files:
            gr.Warning("未提交抢票配置。")
            return gr.update(visible=False)

        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = DEFAULT_REQUEST_INTERVAL
        interval = max(1, interval)
        ConfigDB.insert("requestInterval", interval)

        https_proxys = ConfigDB.get("https_proxy") or ""
        https_proxy_list = ["none"] + https_proxys.split(",")
        assigned_proxies: list[list[str]] = []
        assigned_proxies_next_idx = 0
        # 从配置文件加载
        buy_config = BuyConfig.from_config_db(
            time_start=time_start,
            interval=interval,
        )
        proxy_assignment_strategy = str(
            ConfigDB.get("proxyAssignmentStrategy") or "balanced"
        ).lower()
        queue_concurrency_limit = ConfigDB.get_as_int("queueConcurrencyLimit", 0)
        log_retention_days = buy_config.log_retention_days
        auto_cleanup_logs = ConfigDB.get("autoCleanupLogs")
        if auto_cleanup_logs is None:
            auto_cleanup_logs = True
        if auto_cleanup_logs:
            from util.Storage.CleanupUtil import cleanup_runtime_artifacts

            cleanup_runtime_artifacts(
                logs_dir=LOG_DIR,
                runs_dir=os.path.join(os.path.dirname(LOG_DIR), "btb_runs"),
                retention_days=log_retention_days,
                max_log_files=ConfigDB.get_as_int("maxLogFiles", DEFAULT_MAX_LOG_FILES),
                max_run_dirs=ConfigDB.get_as_int("maxRunDirs", DEFAULT_MAX_RUN_DIRS),
            )

        # 检查代理数量限制
        available_proxies = len(https_proxy_list)  # 包含 "none" 直连
        running_tasks = [t for t in GlobalStatusInstance.get_task_logs() if t.status == "运行中"]
        running_task_count = len(running_tasks)
        max_concurrent_tasks = available_proxies  # 每个终端需要一个代理

        if running_task_count >= max_concurrent_tasks:
            gr.Warning(f"代理数量不足！当前有 {running_task_count} 个运行中的任务，但只有 {available_proxies} 个可用代理。请先停止部分任务后再启动新任务。")
            return gr.update(visible=True)

        # 检查要启动的任务数量是否超过限制
        tasks_to_start = len(files)
        if running_task_count + tasks_to_start > max_concurrent_tasks:
            allowed_tasks = max_concurrent_tasks - running_task_count
            gr.Warning(f"代理数量不足！只能再启动 {allowed_tasks} 个任务（当前运行 {running_task_count} 个，代理总数 {available_proxies} 个）。")
            files = files[:allowed_tasks]

        if proxy_assignment_strategy == "queue":
            worker_count = len(https_proxy_list)
            if queue_concurrency_limit > 0:
                worker_count = min(worker_count, queue_concurrency_limit)
            worker_count = max(1, min(worker_count, len(files)))
            pending_files = list(files)
            pending_lock = threading.Lock()

            def queue_worker(proxy_slot: str):
                """
                队列模式工作线程：按顺序消费待抢票文件。

                输入参数：
                  proxy_slot : str — 该 worker 固定使用的代理（或 "none"）。

                返回值：
                  无。

                调用场景：
                  start_go() 的 queue 模式中由 daemon 线程启动。
                """
                while True:
                    with pending_lock:
                        if not pending_files:
                            return
                        current_file = pending_files.pop(0)
                    try:
                        proc = launch_task(
                            current_file,
                            config=buy_config.with_overrides(
                                https_proxys=proxy_slot,
                            ),
                        )
                        proc.wait()
                    except Exception as exc:
                        logger.exception(exc)

            for worker_idx in range(worker_count):
                threading.Thread(
                    target=queue_worker,
                    args=(https_proxy_list[worker_idx % len(https_proxy_list)],),
                    name=f"btb-queue-worker-{worker_idx + 1}",
                    daemon=True,
                ).start()
            gr.Info("抢票任务已按队列模式启动。")
            return gr.update(visible=True)

        for idx, filename in enumerate(files):
            if assigned_proxies == []:
                left_task_num = len(files) - idx
                assigned_proxies = split_proxies(https_proxy_list, left_task_num)
            launch_task(
                filename,
                config=buy_config.with_overrides(
                    https_proxys=",".join(assigned_proxies[assigned_proxies_next_idx]),
                ),
            )
            assigned_proxies_next_idx += 1
        gr.Info("抢票任务已启动，下面可以直接查看日志链接或停止任务。")
        return gr.update(visible=True)

    @runtime_state_writer(GO_UPLOADED_FILES_STATE_KEY, kind="path_list")
    def sync_uploaded_files(files):
        """
        同步上传文件列表到运行时状态。

        输入参数：
          files : list[str] — 当前上传文件列表。

        返回值：
          None（仅触发 runtime_state_writer 副作用）。

        调用场景：
          upload_ui.change 事件触发。
        """
        return None

    @runtime_state_writer(
        GO_UPLOADED_FILES_STATE_KEY,
        kind="path_list",
        value_getter=lambda args, kwargs, result: [],
    )
    def clear_uploaded_files(_files):
        """
        清空上传文件回调：重置预览为空。

        输入参数：
          _files : list — 当前文件列表（未使用）。

        返回值：
          gradio.update — 重置 ticket_ui 为空白预览。

        调用场景：
          upload_ui.clear 事件触发。
        """
        return gr.update(value=_render_ticket_preview({}), visible=True)

    upload_ui.upload(fn=upload, inputs=upload_ui, outputs=ticket_ui)
    upload_ui.change(fn=sync_uploaded_files, inputs=upload_ui, outputs=None)

    def maybe_auto_fill_time(files):
        """
        根据 autoFillTime 配置决定是否自动填写抢票时间。

        输入参数：
          files : list[str] — 上传的配置文件路径列表。

        返回值：
          str — 自动填写的时间字符串，或空字符串。

        调用场景：
          upload_ui.upload 的 then 链式回调中触发。
        """
        if not ConfigDB.get("autoFillTime"):
            return ""
        return auto_fill_time(files)

    upload_ui.clear(
        fn=clear_uploaded_files,
        inputs=upload_ui,
        outputs=ticket_ui,
    )
    upload_ui.select(file_select_handler, upload_ui, ticket_ui)

    go_btn = gr.Button(
        "开始抢票",
        elem_classes="btb-strong-button",
    )
    with gr.Column(
        visible=bool(visible_task_entries()),
        elem_classes="btb-card btb-card-sky btb-layout-card",
    ) as task_panel:
        task_refresh_token = render_task_manager_panel(task_panel)

    _time_tmp = gr.Textbox(visible=False)
    _auto_fill_time_tmp = gr.Textbox(visible=False)
    auto_fill_time_btn.click(
        fn=auto_fill_time,
        inputs=upload_ui,
        outputs=_auto_fill_time_tmp,
    ).then(
        fn=None,
        inputs=_auto_fill_time_tmp,
        outputs=_time_tmp,
        js="""
        (value) => {
            const input = document.getElementById("datetime");
            if (input) {
                input.value = value || "";
            }
            return value || "";
        }
        """,
    )
    upload_ui.upload(
        fn=maybe_auto_fill_time,
        inputs=upload_ui,
        outputs=_auto_fill_time_tmp,
    ).then(
        fn=None,
        inputs=_auto_fill_time_tmp,
        outputs=_time_tmp,
        js="""
        (value) => {
            const input = document.getElementById("datetime");
            if (input) {
                input.value = value || "";
            }
            return value || "";
        }
        """,
    )
    go_btn.click(
        fn=None,
        inputs=None,
        outputs=_time_tmp,
        js='(x) => document.getElementById("datetime").value',
    )

    go_btn.click(
        fn=start_go,
        inputs=[upload_ui, _time_tmp, interval_ui],
        outputs=task_panel,
    ).then(
        fn=refresh_task_panel,
        inputs=None,
        outputs=[task_refresh_token, task_panel],
    )

    def load_go_start_configs():
        """
        加载“操作抢票”页初始化配置。

        返回值：
          gradio.update — 更新抢票间隔输入框为数据库中保存的值。

        调用场景：
          ticker.py 在页面加载时调用。
        """
        return gr.update(
            value=ConfigDB.get_as_int("requestInterval", DEFAULT_REQUEST_INTERVAL)
        )

    return task_refresh_token, task_panel, load_go_start_configs, [interval_ui]
