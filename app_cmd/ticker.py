import base64
import os
import time
import threading
from argparse import Namespace

import gradio as gr
import loguru

from app_version import get_app_version
from util import get_application_path


def exit_app_ui():
    loguru.logger.info("程序退出")
    threading.Timer(2.0, lambda: os._exit(0)).start()
    gr.Info("程序将在弹出提示后退出")


def _get_lan_ip() -> str:
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
        share_project_id = gr.Textbox(
            label="项目ID",
            placeholder="输入B站会员购活动ID，例如 84096",
            info="可从活动链接中提取，如 ?id=84096",
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

        def on_share_start(pid, custom_host, port):
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                raise gr.Error("请输入有效的项目ID")
            port_int = int(port) if port else 7862
            actual_port = start_share_server(pid_int, port_int)
            host = (custom_host or "").strip() or lan_ip
            url = f"http://{host}:{actual_port}"
            gr.Info(f"分享服务已启动，端口 {actual_port}", duration=5)
            return url

        def on_cf_start(pid, port):
            from tab.share import start_cloudflare_tunnel
            try:
                pid_int = int(pid)
            except (TypeError, ValueError):
                raise gr.Error("请输入有效的项目ID")
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


def ticker_cmd(args: Namespace):
    from tab.go import go_settings_tab, go_start_tab
    from tab.log import log_tab, refresh_log_panel, refresh_task_panel
    from tab.problems import problems_tab
    from tab.settings import login_tab, setting_tab
    from tab.update import update_tab
    from util.log_web import attach_log_routes
    from util import ConfigDB, LOG_DIR
    from util.LogConfig import loguru_config

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
        go_refresh_token, go_panel_update = refresh_task_panel()
        log_refresh_token, log_panel_update = refresh_log_panel()
        return (
            go_refresh_token,
            go_panel_update,
            log_refresh_token,
            log_panel_update,
        )

    with gr.Blocks(
        title="biliTickerBuy",
        css=css_path,
        head="""
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;500;600;700&family=Noto+Serif+SC:wght@600&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
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
        """,
    ) as demo:
        with gr.Column(elem_classes="btb-app-shell"):
            header_ui = gr.HTML(header, visible=not hide_header)
            with gr.Tabs(elem_id="btb-main-tabs", elem_classes="btb-top-tabs"):
                with gr.Tab("账号登录", id="login", elem_id="btb-tab-login"):
                    login_tab()
                with gr.Tab("生成配置", id="config", elem_id="btb-tab-config"):
                    setting_tab()
                with gr.Tab("操作抢票", id="go", elem_id="btb-tab-go"):
                    go_task_refresh_token, go_task_panel = go_start_tab()
                with gr.Tab("高级设置", id="advanced", elem_id="btb-tab-advanced"):
                    go_settings_tab(header_ui)
                with gr.Tab("项目说明", id="guide", elem_id="btb-tab-guide"):
                    problems_tab()
                with gr.Tab("软件更新", id="update", elem_id="btb-tab-update"):
                    update_tab(demo)
                with gr.Tab("日志查看", id="logs", elem_id="btb-tab-logs"):
                    log_task_refresh_token, log_task_panel = log_tab()
                with gr.Tab("分享选票", id="share", elem_id="btb-tab-share"):
                    _share_tab(args.server_name)
        demo.load(
            fn=refresh_all_task_panels,
            outputs=[
                go_task_refresh_token,
                go_task_panel,
                log_task_refresh_token,
                log_task_panel,
            ],
        )

    is_docker = os.path.exists("/.dockerenv") or os.environ.get("BTB_DOCKER") == "1"
    demo.launch(
        share=args.share or is_docker,
        inbrowser=not is_docker,
        server_name=args.server_name,
        server_port=args.port,
        prevent_thread_lock=True,
        show_api=False,
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
