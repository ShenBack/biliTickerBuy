"""
文件整体功能：为 FastAPI 应用提供日志查看与实时流式推送能力。
所属模块：util.log
依赖文件：
    - util（导入 LOG_DIR 常量）
    - util.Constant（导入 _LOG_STREAM_ROUTE、_LOG_VIEW_ROUTE）
对外能力：提供 attach_log_routes、build_log_view_url、build_log_stream_url 函数，
          支持在 Web 页面中查看日志并通过 SSE 实时推送新增内容。
"""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
import time
from urllib.parse import quote

from fastapi import HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from util import LOG_DIR
from util.Constant import _LOG_STREAM_ROUTE, _LOG_VIEW_ROUTE


def build_log_view_url(path: str) -> str:
    """
    根据日志文件路径构建日志查看 URL。

    参数：
        path (str)：日志文件的完整路径。
    返回值：str，可访问的日志查看路由 URL，name 参数为 URL 编码后的文件名。
    内部逻辑：提取 basename 后进行 URL 编码并拼接查询参数。
    调用位置：任务日志列表、日志管理界面等需要生成日志查看链接的场景。
    """
    log_name = os.path.basename(path)
    return f"{_LOG_VIEW_ROUTE}?name={quote(log_name, safe='')}"


def _resolve_log_path(raw_path: str | None = None, log_name: str | None = None) -> Path:
    """
    解析并校验日志文件路径，防止目录穿越。

    参数：
        raw_path (str | None)：原始日志路径。
        log_name (str | None)：日志文件名。
    返回值：Path，校验通过且存在的日志文件 Path 对象。
    内部逻辑：
        1. 若提供 log_name，则限制在 LOG_DIR 根目录下；
         若提供 raw_path，则校验其必须位于 LOG_DIR 之下；
        3. 文件不存在或非文件时抛出 HTTPException。
    调用位置：view_log、stream_log 路由处理函数内部调用。
    """
    log_root = Path(LOG_DIR).resolve()

    if log_name:
        safe_name = os.path.basename(log_name.strip())
        if not safe_name:
            raise HTTPException(status_code=400, detail="missing log name")
        target = (log_root / safe_name).resolve()
    elif raw_path:
        target = Path(raw_path).resolve()
        try:
            target.relative_to(log_root)
        except ValueError as exc:
            raise HTTPException(
                status_code=403, detail="log path is outside log dir"
            ) from exc
    else:
        raise HTTPException(status_code=400, detail="missing log identifier")

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="log file not found")

    return target


def _read_log_text(path: Path) -> str:
    """
    读取日志文件文本内容。

    参数：
        path (Path)：日志文件路径。
    返回值：str，文件内容字符串，读取错误时用 replacement 字符替换。
    内部逻辑：以 utf-8 编码打开文件，errors="replace" 保证损坏字节不会中断读取。
    调用位置：view_log 初始加载、stream_log 重置/追加时调用。
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def attach_log_routes(app) -> None:
    """
    向 FastAPI 应用挂载日志查看与流式推送路由。

    参数：
        app (FastAPI)：目标 FastAPI 应用实例。
    返回值：无。
    内部逻辑：
        1. 检查 app.state.btb_log_routes_ready 防止重复注册；
        2. 注册 /__btb/logs/view GET 路由，返回日志查看 HTML 页面；
        3. 注册 /__btb/logs/stream GET 路由，通过 SSE 推送日志增量；
        4. 标记 btb_log_routes_ready 为 True。
    调用位置：Web 服务启动时调用，如 main.py 或 interface 模块。
    """
    if getattr(app.state, "btb_log_routes_ready", False):
        return

    @app.get(_LOG_VIEW_ROUTE, response_class=HTMLResponse)
    def view_log(
        request: Request,
        path: str | None = Query(default=None),
        name: str | None = Query(default=None),
    ) -> HTMLResponse:
        """
        日志查看页面路由处理函数。

        参数：
            request (Request)：FastAPI 请求对象。
            path (str | None)：原始日志路径查询参数。
            name (str | None)：日志文件名查询参数。
        返回值：HTMLResponse，包含完整 HTML 页面的响应。
        内部逻辑：
            1. 调用 _resolve_log_path 解析并校验日志文件；
            2. 读取初始日志文本并做 HTML 转义；
            3. 构造包含 SSE 订阅逻辑的 HTML 页面返回。
        调用位置：用户通过浏览器访问日志查看 URL 时由 FastAPI 调用。
        """
        log_path = _resolve_log_path(raw_path=path, log_name=name)
        initial_text = escape(_read_log_text(log_path))
        title = escape(log_path.name)
        stream_url = f"{_LOG_STREAM_ROUTE}?name={quote(log_path.name, safe='')}"
        body = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1220;
      --panel: #111827;
      --border: #334155;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #22c55e;
      --mono: "JetBrains Mono", Consolas, monospace;
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.5 "Google Sans", "Roboto", "Noto Sans SC", "PingFang SC", system-ui, sans-serif;
    }}
    .shell {{
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 100vh;
    }}
    .bar {{
      padding: 12px 16px;
      border-bottom: 1px solid var(--border);
      background: rgba(17, 24, 39, 0.95);
      position: sticky;
      top: 0;
    }}
    .title {{
      font-weight: 700;
    }}
    .path {{
      margin-top: 4px;
      color: var(--muted);
      word-break: break-all;
      font-size: 12px;
    }}
    .status {{
      margin-top: 6px;
      color: var(--accent);
      font-size: 12px;
    }}
    pre {{
      margin: 0;
      padding: 16px;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font: 13px/1.5 var(--mono);
      background: var(--panel);
    }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="bar">
      <div class="title">实时日志</div>
      <div class="path">{escape(str(log_path))}</div>
      <div class="status" id="status">已连接，等待新日志...</div>
    </div>
    <pre id="log">{initial_text}</pre>
  </div>
  <script>
    const logEl = document.getElementById("log");
    const statusEl = document.getElementById("status");
    const stream = new EventSource({json.dumps(stream_url)});
    function stickToBottom() {{
      const gap = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight;
      return gap < 80;
    }}
    stream.addEventListener("append", (event) => {{
      const shouldScroll = stickToBottom();
      logEl.textContent += event.data;
      if (shouldScroll) {{
        logEl.scrollTop = logEl.scrollHeight;
      }}
      statusEl.textContent = "已连接，日志实时更新中";
    }});
    stream.addEventListener("reset", (event) => {{
      logEl.textContent = event.data;
      logEl.scrollTop = logEl.scrollHeight;
      statusEl.textContent = "日志已重置，已重新加载";
    }});
    stream.onerror = () => {{
      statusEl.textContent = "连接中断，正在尝试重连...";
    }};
  </script>
</body>
</html>"""
        return HTMLResponse(body)

    @app.get(_LOG_STREAM_ROUTE)
    def stream_log(
        path: str | None = Query(default=None),
        name: str | None = Query(default=None),
    ) -> StreamingResponse:
        """
        日志实时流式推送路由处理函数（SSE）。

        参数：
            path (str | None)：原始日志路径查询参数。
            name (str | None)：日志文件名查询参数。
        返回值：StreamingResponse，Content-Type 为 text/event-stream。
        内部逻辑：
            1. 解析并校验日志文件路径；
            2. 从当前文件末尾开始读取；
            3. 循环检测文件大小变化，输出新增内容或重置事件；
            4. 每 10 秒输出一次 ping 注释保持连接；
            5. 文件被删除时输出提示并结束。
        调用位置：浏览器通过 EventSource 访问流式日志 URL 时由 FastAPI 调用。
        """
        log_path = _resolve_log_path(raw_path=path, log_name=name)

        def generate():
            position = log_path.stat().st_size
            last_ping = 0.0
            while True:
                try:
                    current_size = log_path.stat().st_size
                    if current_size < position:
                        content = _read_log_text(log_path)
                        position = current_size
                        yield _sse("reset", content)
                    elif current_size > position:
                        with open(
                            log_path, "r", encoding="utf-8", errors="replace"
                        ) as handle:
                            handle.seek(position)
                            chunk = handle.read()
                        position = current_size
                        if chunk:
                            yield _sse("append", chunk)

                    now = time.time()
                    if now - last_ping >= 10:
                        last_ping = now
                        yield ": ping\n\n"
                    time.sleep(1)
                except FileNotFoundError:
                    yield _sse("append", "\n[日志文件已不存在]\n")
                    return

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    app.state.btb_log_routes_ready = True


def build_log_stream_url(path: str) -> str:
    """
    根据日志文件路径构建日志流式推送 URL。

    参数：
        path (str)：日志文件的完整路径。
    返回值：str，SSE 流式日志 URL。
    内部逻辑：提取 basename 并 URL 编码后拼接查询参数。
    调用位置：需要在前端或日志列表中生成 SSE 链接的场景。
    """
    log_name = os.path.basename(path)
    return f"{_LOG_STREAM_ROUTE}?name={quote(log_name, safe='')}"


def _sse(event: str, data: str) -> str:
    """
    构造 SSE（Server-Sent Events）事件帧。

    参数：
        event (str)：事件类型，如 "append" 或 "reset"。
        data (str)：事件数据内容。
    返回值：str，符合 SSE 协议的字符串帧。
    内部逻辑：将换行符统一为 \n，并在每行前添加 "data: " 前缀，最后追加空行。
    调用位置：stream_log 的 generate 内部调用。
    """
    safe_data = data.replace("\r\n", "\n").replace("\r", "\n")
    return f"event: {event}\ndata: {safe_data.replace(chr(10), chr(10) + 'data: ')}\n\n"
