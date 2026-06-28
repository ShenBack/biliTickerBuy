"""
app_cmd/ticker.py — Gradio Web UI 主入口。

文件整体功能：
  提供 ticker_cmd() 函数，构建并启动基于 Gradio 的 Web 界面，包含以下功能标签页：
  - 账号登录（login_tab）
  - 生成配置（setting_tab）
  - 操作抢票（go_start_tab）
  - 高级设置（go_settings_tab）
  - 日志查看（log_tab）
  - 分享选票（_share_tab，含局域网分享与 Cloudflare 隧道）
  - BWS预约（bws_tab）

  此外，本文件还负责：
  1. 加载应用图标、CSS 样式、页头 HTML。
  2. 注入前端 JavaScript（移动端下拉框优化、时间选择器增强）。
  3. 配置日志系统并附加日志路由到 FastAPI 后端。
  4. 处理 Docker 环境适配、allowed_paths 安全路径配置。
  5. 主循环保持进程运行，直到收到 Ctrl+C 后优雅关闭。

所属模块：
  命令层 (app_cmd)

依赖文件：
  - app_cmd.cli_args.TickerCliArgs    (UI 启动参数)
  - app_version                       (get_app_version)
  - tab.go                            (go_start_tab，抢票操作页)
  - tab.config                        (go_settings_tab，高级设置页)
  - tab.log                           (log_tab / refresh_log_panel / refresh_task_panel)
  - tab.settings                      (login_tab / setting_tab，登录与配置生成)
  - tab.share                         (start_share_server / start_cloudflare_tunnel)
  - tab.bws                           (bws_tab)
  - util.log.LogWeb                   (attach_log_routes，日志 HTTP 路由)
  - util.log.LogConfig                (loguru_config，日志配置)
  - util                              (ConfigDB / GLOBAL_COOKIE_PATH / LOG_DIR / TEMP_PATH / get_application_path)

对外能力：
  - ticker_cmd(args: TickerCliArgs) → 启动 Gradio 服务并阻塞运行。
  - exit_app_ui() → 触发 Gradio 信息提示并在 2 秒后强制退出进程。
  - shutdown_app_process() → 终止所有运行中的抢票子进程，防止主进程退出后子进程泄漏。
"""

import base64
import os
import threading
import time

import gradio as gr
import loguru

from app_cmd.cli_args import TickerCliArgs
from app_version import get_app_version
from util import get_application_path


def exit_app_ui():
    """
    触发 Gradio 界面退出提示并强制结束进程。

    核心作用：
      在 UI 中点击退出按钮后，先通过 gr.Info 弹出提示，再延迟 2 秒调用 os._exit(0)，
      确保前端有时间接收并展示提示信息。

    输入参数：无。

    返回值：无。

    内部关键执行逻辑：
      1. 记录 "程序退出" 日志。
      2. 启动 2 秒定时器，超时后调用 os._exit(0) 强制结束进程。
      3. 立即通过 gr.Info 提示用户程序即将退出。

    调用场景：
      Gradio UI 中"退出程序"按钮的回调函数。
    """
    loguru.logger.info("程序退出")
    threading.Timer(2.0, lambda: os._exit(0)).start()
    gr.Info("程序将在弹出提示后退出")


def shutdown_app_process():
    """
    关闭所有运行中的抢票子进程。

    核心作用：
      在收到 Ctrl+C 或需要退出 UI 主进程时，遍历 GlobalStatusInstance 中记录的任务，
      对仍处于“运行中”状态的任务调用 terminate_task() 发送终止信号，避免子进程泄漏。

    输入参数：无。

    返回值：无。

    内部关键执行逻辑：
      1. 从 tab.log 延迟导入 visible_task_entries 与 terminate_task，避免循环导入。
      2. 遍历所有可见任务条目。
      3. 对 status 为 "运行中" 的任务调用 terminate_task(pid)。
      4. 记录被终止的任务数量或异常情况。

    调用场景：
      ticker_cmd() 中捕获 KeyboardInterrupt 后，在 demo.close() 之前调用。
    """
    try:
        from tab.log import visible_task_entries, terminate_task

        entries = visible_task_entries()
        terminated = 0
        for entry in entries:
            if entry.status == "运行中":
                terminate_task(entry.pid)
                terminated += 1
        if terminated:
            loguru.logger.info(f"已终止 {terminated} 个运行中的抢票子进程")
    except Exception as exc:
        loguru.logger.error(f"终止抢票子进程时出错: {exc}")


def _get_lan_ip() -> str:
    """
    获取本机局域网 IP 地址。

    核心作用：
      通过 UDP 连接公网 DNS（8.8.8.8:80）获取本地出口 IP；
      若失败则回退到 127.0.0.1。

    输入参数：无。

    返回值：
      str
        局域网 IP 字符串；获取失败时返回 "127.0.0.1"。

    内部关键执行逻辑：
      1. 创建 UDP 套接字并连接 8.8.8.8:80。
      2. 读取 getsockname()[0] 得到本地 IP。
      3. 关闭套接字并返回 IP；异常时返回 127.0.0.1。

    调用场景：
      _share_tab() 中构造默认访问地址时调用。
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _share_tab(server_name: str | None = None):
    """
    构建"分享选票"标签页。

    核心作用：
      提供项目 ID 选择、访问地址与端口配置，并支持两种分享方式：
      1. 局域网分享：启动本地 HTTP 服务，生成内网访问链接。
      2. Cloudflare 公网隧道：通过 Cloudflare 内网穿透生成公网 URL。

    输入参数：
      server_name : str | None
        Gradio 服务监听地址（当前未直接使用，保留扩展）。

    返回值：无（纯 Gradio 组件构建函数）。

    内部关键执行逻辑：
      1. 获取本机局域网 IP。
      2. 渲染项目 ID、访问地址、端口等输入组件。
      3. 绑定"启动分享服务"与"启动 Cloudflare 公网隧道"按钮点击事件。

    调用场景：
      ticker_cmd() 中构建 Gradio Tabs 时调用。
    """
    from tab.share import start_share_server

    lan_ip = _get_lan_ip()

    with gr.Column(elem_classes="btb-card btb-layout-card"):
        gr.HTML(
            """
            <div class="btb-card-head">
                <div>
                    <div class="btb-card-head__eyebrow">Share</div>
                    <h3>分享选票</h3>
                    <p>输入项目ID后生成链接，发送给他人扫码登录、选票，配置会自动发送到飞书。</p>
                </div>
            </div>
            """
        )
        share_project_id = gr.Dropdown(
            label="项目ID",
            info="可从活动链接中提取，如 ?id=84096",
            interactive=True,
            choices=[
                "1001701 2026BML",
                "1001653 2026BW",
            ],
            value="1001701 2026BML",
            allow_custom_value=True,
        )
        share_host = gr.Textbox(
            label="访问地址（留空自动使用局域网IP）",
            placeholder=f"留空则使用 {lan_ip}，可填写公网域名或IP",
            info="同局域网直接用自动IP；跨网络需填写公网IP或域名，或使用内网穿透工具",
            value="",
        )
        share_port = gr.Number(
            label="端口号",
            value=7862,
            precision=0,
            minimum=1024,
            maximum=65535,
        )
        share_btn = gr.Button("启动分享服务（局域网）", elem_classes="btb-strong-button")
        cf_btn = gr.Button("启动 Cloudflare 公网隧道", elem_classes="btb-soft-button")
        share_result = gr.Textbox(label="分享链接", interactive=False, lines=3)

        def _parse_pid(pid):
            """
            解析用户输入的项目 ID。

            核心作用：
              从 "1001701 2026BML" 这类字符串中提取第一个空格前的数字作为项目 ID。

            输入参数：
              pid : Any
                用户输入值，通常为字符串或下拉框选项。

            返回值：
              int
                解析后的项目 ID。

            内部关键执行逻辑：
              1. 将输入转为字符串并去除首尾空格。
              2. 按空格分割后取第一个元素。
              3. 使用 int() 转为整数。

            异常：
              gr.Error — 输入无效时抛出 Gradio 错误提示。

            调用场景：
              on_share_start() 与 on_cf_start() 内部调用。
            """
            try:
                return int(str(pid).strip().split()[0])
            except (TypeError, ValueError, IndexError):
                raise gr.Error("请输入有效的项目ID")

        def on_share_start(pid, custom_host, port):
            """
            "启动分享服务"按钮回调。

            核心作用：
              解析项目 ID，启动本地分享服务器，生成访问 URL 并自动打开浏览器。

            输入参数：
              pid         : Any
                用户选择或输入的项目 ID。
              custom_host : str | None
                用户自定义访问地址；为空时使用本机局域网 IP。
              port        : int | float | None
                用户配置的端口号；为空时使用默认 7862。

            返回值：
              str
                生成的局域网分享链接，例如 http://192.168.x.x:7862。

            内部关键执行逻辑：
              1. 调用 _parse_pid 解析项目 ID。
              2. 调用 start_share_server 启动本地 HTTP 服务。
              3. 构造 URL 并通过 webbrowser 自动打开。
              4. 返回 URL 供 share_result 组件展示。

            调用场景：
              share_btn.click() 的 fn 回调。
            """
            pid_int = _parse_pid(pid)
            port_int = int(port) if port else 7862
            actual_port = start_share_server(pid_int, port_int)
            host = (custom_host or "").strip() or lan_ip
            url = f"http://{host}:{actual_port}"
            gr.Info(f"分享服务已启动，端口 {actual_port}", duration=5)
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
            return url

        def on_cf_start(pid, port):
            """
            "启动 Cloudflare 公网隧道"按钮回调。

            核心作用：
              先启动本地分享服务，再尝试通过 Cloudflare 内网穿透生成公网 URL。

            输入参数：
              pid  : Any
                用户选择或输入的项目 ID。
              port : int | float | None
                用户配置的端口号；为空时使用默认 7862。

            返回值：
              str
                Cloudflare 公网隧道 URL。

            内部关键执行逻辑：
              1. 解析项目 ID 并启动本地分享服务。
              2. 调用 start_cloudflare_tunnel 建立公网隧道。
              3. 返回隧道 URL；失败时抛出 gr.Error。

            异常：
              gr.Error — 隧道启动失败时抛出错误提示。

            调用场景：
              cf_btn.click() 的 fn 回调。
            """
            from tab.share import start_cloudflare_tunnel
            pid_int = _parse_pid(pid)
            port_int = int(port) if port else 7862
            start_share_server(pid_int, port_int)
            gr.Info("正在启动 Cloudflare 隧道，请等待...", duration=5)
            try:
                tunnel_url = start_cloudflare_tunnel(port_int)
                return tunnel_url
            except Exception as exc:
                raise gr.Error(str(exc))

        share_btn.click(
            on_share_start,
            inputs=[share_project_id, share_host, share_port],
            outputs=share_result,
        )
        cf_btn.click(
            on_cf_start,
            inputs=[share_project_id, share_port],
            outputs=share_result,
        )


def ticker_cmd(args: TickerCliArgs):
    """
    Gradio Web UI 启动主函数。

    核心作用：
      1. 初始化日志系统。
      2. 加载图标、CSS、页头 HTML。
      3. 构建 Gradio Blocks 界面，注册全部标签页与交互组件。
      4. 注入自定义前端 JavaScript（时间选择器增强、移动端下拉框只读优化）。
      5. 处理 Docker 环境检测与 allowed_paths 安全路径配置。
      6. 启动 Gradio 服务（share/inbrowser/server_name/server_port 等参数）。
      7. 附加日志路由到 FastAPI 应用。
      8. 进入主循环保持运行，捕获 Ctrl+C 后优雅关闭。

    输入参数：
      args : TickerCliArgs
        UI 启动参数（端口、是否分享、服务器地址等）。

    返回值：无。

    内部关键执行逻辑：
      1. 配置 loguru 日志并确定日志目录。
      2. 读取 assets/icon.ico 与 assets/style.css，构造页头 HTML。
      3. 使用 gr.Blocks 组装各标签页组件。
      4. 通过 demo.load() 与 advanced_tab.select() 注册初始化回调。
      5. 检测 Docker 环境，计算 allowed_paths 白名单。
      6. 调用 demo.launch() 启动服务，随后 attach_log_routes 挂载日志路由。
      7. 阻塞主循环，直到 KeyboardInterrupt 后关闭服务与进程。

    调用场景：
      main.py 中通过命令行子命令 "ticker" 调用。
    """
    from tab.go import go_start_tab
    from tab.config import go_settings_tab
    from tab.log import log_tab, refresh_log_panel, refresh_task_panel
    from tab.settings import login_tab, setting_tab
    from tab.bws import bws_tab
    from util.log.LogWeb import attach_log_routes
    from util import ConfigDB, GLOBAL_COOKIE_PATH, LOG_DIR, TEMP_PATH
    from util.log.LogConfig import loguru_config

    loguru_config(LOG_DIR, "app.log", enable_console=True, file_colorize=False)
    assets_dir = os.path.join(get_application_path(), "assets")
    icon_path = os.path.join(assets_dir, "icon.ico")
    css_path = os.path.join(assets_dir, "style.css")
    icon_url = ""
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as icon_file:
            icon_url = "data:image/x-icon;base64," + base64.b64encode(
                icon_file.read()
            ).decode("ascii")

    app_version = get_app_version()
    hide_header = ConfigDB.get("hideHeader")
    if hide_header is None:
        hide_header = False

    header = f"""
    <section class="btb-hero">
        <div class="btb-hero__eyebrow">BiliTickerBuy · v{app_version}</div>
        <div class="btb-hero__grid">
            <div>
                <h1>biliTickerBuy</h1>  
            </div>
            <div class="btb-hero__logo" aria-label="biliTickerBuy logo">
                <img class="btb-hero__logo-image" src="{icon_url}" alt="biliTickerBuy icon">
                
            </div>
        </div>
        <div class="btb-hero__notice">
            <span class="btb-hero__notice-mark">!</span>
            <span>
                此项目完全开源免费。开源地址：
                <a href="https://github.com/mikumifa/biliTickerBuy" target="_blank">https://github.com/mikumifa/biliTickerBuy</a>。
                请勿用于盈利，否则后果自负。
            </span>
        </div>
    </section>
    """

    def refresh_all_task_panels():
        """
        刷新所有任务与日志面板。

        核心作用：
          聚合调用 refresh_task_panel()、refresh_log_panel() 和 load_go_start_configs()，
          返回所有需要更新的 Gradio 组件值，供 demo.load() 在页面加载时批量刷新。

        输入参数：无。

        返回值：
          tuple
            (go_refresh_token, go_panel_update, log_refresh_token, log_panel_update, go_start_updates)。

        内部关键执行逻辑：
          1. 调用 refresh_task_panel() 获取抢票任务刷新令牌与面板更新。
          2. 调用 refresh_log_panel() 获取日志刷新令牌与面板更新。
          3. 调用 load_go_start_configs() 获取抢票页初始化数据。
          4. 将上述结果打包为元组返回。

        调用场景：
          Gradio demo.load() 初始化回调。
        """
        go_refresh_token, go_panel_update = refresh_task_panel()
        log_refresh_token, log_panel_update = refresh_log_panel()
        go_start_updates = load_go_start_configs()
        return (
            go_refresh_token,
            go_panel_update,
            log_refresh_token,
            log_panel_update,
            go_start_updates,
        )

    with gr.Blocks(
        title="biliTickerBuy",
    ) as demo:
        launch_head = """
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;700&family=Roboto:wght@400;500;700&family=Noto+Sans+SC:wght@400;500;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
        <style>
          :root { --font-sans: "Google Sans", "Roboto", "Noto Sans SC", "PingFang SC", system-ui, sans-serif; --font-mono: "Google Sans Mono", "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
          body, button, input, textarea, select { font-family: var(--font-sans) !important; }
        </style>
        <script src="https://cdn.jsdelivr.net/npm/@tailwindcss/browser@4"></script>
        <script>
        (function(){
            function isMobileLike() {
                return window.matchMedia('(max-width: 768px)').matches ||
                    window.matchMedia('(pointer: coarse)').matches;
            }
            function enhanceDropdownNoKeyboard() {
                if (!isMobileLike()) return;
                var inputs = document.querySelectorAll(
                    '.gradio-container [data-testid="dropdown"] input,' +
                    '.gradio-container .gradio-dropdown input,' +
                    '.gradio-container .dropdown input,' +
                    '.gradio-container .choices input'
                );
                inputs.forEach(function(input) {
                    if (input.dataset.mobileNoKeyboard === '1') return;
                    input.dataset.mobileNoKeyboard = '1';
                    input.readOnly = true;
                    input.setAttribute('readonly', 'readonly');
                    input.setAttribute('inputmode', 'none');
                    input.setAttribute('autocomplete', 'off');
                    input.setAttribute('autocorrect', 'off');
                    input.setAttribute('autocapitalize', 'off');
                    input.setAttribute('spellcheck', 'false');
                });
            }
            function watchDropdownEnhance() {
                enhanceDropdownNoKeyboard();
                var observer = new MutationObserver(function() {
                    enhanceDropdownNoKeyboard();
                });
                observer.observe(document.body, {childList: true, subtree: true});
            }
            function enhance(){
                var root=document.getElementById('btb-time-start');
                if(!root){setTimeout(enhance,300);return;}
                var input=root.querySelector('input[type="text"],textarea');
                if(!input){setTimeout(enhance,300);return;}
                if(root.dataset.enhanced) return;
                root.dataset.enhanced='1';
                var ghost=document.createElement('input');
                ghost.type='datetime-local';ghost.step='1';
                ghost.className='btb-picker-ghost';ghost.tabIndex=-1;
                var btn=document.createElement('button');
                btn.type='button';btn.className='btb-picker-trigger';
                btn.title='打开日历选择器';
                btn.innerHTML='<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/></svg>';
                var wrapper=document.createElement('div');
                wrapper.className='btb-picker-wrap';
                wrapper.style.position='relative';
                wrapper.style.display='block';
                wrapper.style.width='100%';
                input.parentNode.insertBefore(wrapper,input);
                wrapper.appendChild(input);
                wrapper.appendChild(ghost);
                wrapper.appendChild(btn);
                btn.addEventListener('click',function(e){
                    e.preventDefault();e.stopPropagation();
                    if(input.value){
                        try{ghost.value=input.value.trim().replace(' ','T');}catch(ex){}
                    }
                    ghost.showPicker();
                });
                ghost.addEventListener('input',function(){
                    var v=this.value;if(!v)return;
                    var dt=v.replace('T',' ');
                    if(dt.length===16)dt+=':00';
                    var setter=Object.getOwnPropertyDescriptor(
                        Object.getPrototypeOf(input),'value'
                    ).set;
                    setter.call(input,dt);
                    input.dispatchEvent(new Event('input',{bubbles:true}));
                });
            }
            if(document.readyState==='loading')
                document.addEventListener('DOMContentLoaded',function(){enhance();watchDropdownEnhance();});
            else {setTimeout(enhance,500);setTimeout(watchDropdownEnhance,300);}
        })();
        </script>
        """
        with gr.Column(elem_classes="btb-app-shell"):
            header_ui = gr.HTML(header, visible=not hide_header)
            with gr.Tabs(elem_id="btb-main-tabs", elem_classes="btb-top-tabs"):
                with gr.Tab("账号登录", id="login", elem_id="btb-tab-login"):
                    load_login_tab, login_tab_load_outputs = login_tab()
                with gr.Tab("生成配置", id="config", elem_id="btb-tab-config"):
                    setting_tab()
                with gr.Tab("操作抢票", id="go", elem_id="btb-tab-go"):
                    (
                        go_task_refresh_token,
                        go_task_panel,
                        load_go_start_configs,
                        go_start_load_outputs,
                    ) = go_start_tab()
                with gr.Tab(
                    "高级设置",
                    id="advanced",
                    elem_id="btb-tab-advanced",
                ) as advanced_tab:
                    (
                        load_go_settings_configs,
                        go_settings_load_outputs,
                    ) = go_settings_tab(header_ui)
                with gr.Tab("日志查看", id="logs", elem_id="btb-tab-logs"):
                    log_task_refresh_token, log_task_panel = log_tab()
                with gr.Tab("分享选票", id="share", elem_id="btb-tab-share"):
                    _share_tab(args.server_name)
                with gr.Tab("BWS预约", id="bws", elem_id="btb-tab-bws"):
                    bws_tab()
        demo.load(
            fn=refresh_all_task_panels,
            outputs=[
                go_task_refresh_token,
                go_task_panel,
                log_task_refresh_token,
                log_task_panel,
                *go_start_load_outputs,
            ],
        )
        demo.load(
            fn=load_login_tab,
            outputs=login_tab_load_outputs,
            show_progress="hidden",
            queue=False,
        )
        advanced_tab.select(
            fn=load_go_settings_configs,
            inputs=None,
            outputs=go_settings_load_outputs,
            show_progress="hidden",
            queue=False,
        )

    is_docker = os.path.exists("/.dockerenv") or os.environ.get("BTB_DOCKER") == "1"
    allowed_paths: list[str] = []
    for candidate in [
        os.environ.get("BTB_CONFIG_PATH"),
        GLOBAL_COOKIE_PATH,
        LOG_DIR,
        TEMP_PATH,
    ]:
        if not candidate:
            continue
        target = candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
        if target and os.path.exists(target) and target not in allowed_paths:
            allowed_paths.append(target)

    demo.launch(
        share=args.share or is_docker,
        inbrowser=not is_docker,
        server_name=args.server_name,
        server_port=args.port,
        root_path=args.root_path,
        allowed_paths=allowed_paths,
        prevent_thread_lock=True,
        footer_links=[],
        css_paths=css_path,
        head=launch_head,
    )
    attach_log_routes(demo.app)
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        loguru.logger.info("收到 Ctrl+C，正在关闭主进程...")
        shutdown_app_process()
        demo.close()
        return
