"""
tab/share.py — 选票分享服务（FastAPI 独立服务）。

文件整体功能：
  提供一个可独立运行的 FastAPI 服务，用于将“生成配置”能力以网页形式分享给他人：
  1. 启动本地 HTTP 服务（uvicorn）和可选的 Cloudflare Tunnel 公网入口。
  2. 提供二维码扫码登录接口，让被分享者使用自己的 B站账号登录。
  3. 登录后自动拉取项目票档、实名购票人、收货地址。
  4. 被分享者选择票档、购票人、地址后提交，服务端生成完整抢票 JSON 配置并保存，
     同时将登录账号汇入本地账号池。
  5. 通过飞书 Webhook 推送生成结果。

所属模块：
  UI/网络共享层 (tab)

依赖文件：
  - util.EXE_PATH            (项目/可执行文件根目录)
  - util.request.BiliRequest (B站 HTTP 请求封装)
  - interface.project        (fetch_project_payload 拉取项目详情)
  - util.CookieManager       (parse_cookie_list / add_account)

对外能力：
  - start_share_server(project_id, port) → 启动本地 FastAPI 服务，返回监听端口。
  - start_cloudflare_tunnel(port)       → 启动 cloudflared 公网隧道，返回公网 URL。
  - stop_cloudflare_tunnel()            → 停止 cloudflared 隧道。
  - share_page(project_id)              → FastAPI 路由，设置当前分享项目 ID 并返回选票页面。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

import requests as http_requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from util import EXE_PATH
from util.request.BiliRequest import BiliRequest

# 飞书机器人 Webhook 地址，用于提交成功后推送配置文本
FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/1dd7ee3b-f730-426c-8a90-ee1533c6222e"

# FastAPI 应用实例
_app = FastAPI()
# 后台运行的 uvicorn 服务线程
_server_thread: threading.Thread | None = None
# 当前服务监听端口
_running_port: int = 0

# 当前分享的项目 ID
_project_id: int = 0
# cloudflared 子进程句柄
_tunnel_process = None
# 当前隧道公网 URL
_tunnel_url: str = ""


def _find_cloudflared() -> str | None:
    """
    在多个候选位置查找 cloudflared 可执行文件。

    搜索顺序：
      1. 系统 PATH。
      2. EXE_PATH（项目/可执行文件根目录）。
      3. PyInstaller 临时解压目录（打包后的运行环境）。
      4. 当前工作目录。

    返回值：
      str | None — 可执行文件绝对路径；未找到返回 None。

    调用场景：
      start_cloudflare_tunnel() 中启动隧道前定位 cloudflared。
    """
    exe_name = "cloudflared.exe" if sys.platform == "win32" else "cloudflared"

    # 1. 系统 PATH
    found = shutil.which("cloudflared")
    if found:
        return found

    # 2. EXE / 项目根目录
    candidate = os.path.join(EXE_PATH, exe_name)
    if os.path.isfile(candidate):
        return candidate

    # 3. PyInstaller 临时解压目录（打包场景）
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = os.path.join(meipass, exe_name)
        if os.path.isfile(candidate):
            return candidate

    # 4. 当前工作目录
    candidate = os.path.join(os.getcwd(), exe_name)
    if os.path.isfile(candidate):
        return candidate

    return None


def start_share_server(project_id: int, port: int = 7862) -> int:
    """
    启动本地 FastAPI 分享服务。

    核心作用：
      在独立后台线程中运行 uvicorn，监听 0.0.0.0:port；
      若服务已启动则直接返回当前端口，避免重复启动。

    输入参数：
      project_id : int — 当前要分享的项目 ID。
      port       : int — 服务监听端口，默认 7862。

    返回值：
      int — 实际监听端口。

    调用场景：
      tab.go 中“开启分享”按钮点击后启动服务。
    """
    global _server_thread, _running_port, _project_id
    _project_id = project_id
    _running_port = port

    if _server_thread and _server_thread.is_alive():
        return _running_port

    def _run():
        uvicorn.run(_app, host="0.0.0.0", port=port, log_level="warning")

    _server_thread = threading.Thread(target=_run, daemon=True)
    _server_thread.start()
    time.sleep(0.5)
    return _running_port


def start_cloudflare_tunnel(port: int) -> str:
    """
    启动 cloudflare tunnel，返回公网 URL。

    核心作用：
      调用本地 cloudflared 创建 trycloudflare.com 临时隧道，
      从 stderr 输出中解析公网 URL，使外部用户可访问本地分享服务。

    输入参数：
      port : int — 本地服务端口。

    返回值：
      str — 公网访问 URL。

    异常：
      RuntimeError — 未找到 cloudflared、启动失败或超时时抛出。

    调用场景：
      tab.go 中需要生成公网分享链接时调用。
    """
    global _tunnel_process, _tunnel_url

    if _tunnel_process and _tunnel_process.poll() is None:
        return _tunnel_url

    cloudflared = _find_cloudflared()
    if not cloudflared:
        raise RuntimeError(
            "未找到 cloudflared，请先安装：\n"
            "Windows: winget install Cloudflare.cloudflared\n"
            "或下载: https://github.com/cloudflare/cloudflared/releases\n"
            f"（也支持把 cloudflared.exe 放在 {EXE_PATH} 下）"
        )

    _tunnel_process = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")

    deadline = time.time() + 30
    while time.time() < deadline:
        line = _tunnel_process.stderr.readline()
        if not line:
            if _tunnel_process.poll() is not None:
                raise RuntimeError("cloudflared 启动失败")
            time.sleep(0.3)
            continue
        match = url_pattern.search(line)
        if match:
            _tunnel_url = match.group(0)
            logger.info(f"Cloudflare Tunnel 已启动: {_tunnel_url}")
            return _tunnel_url

    _tunnel_process.kill()
    raise RuntimeError("cloudflared 启动超时，未获取到公网URL")


def stop_cloudflare_tunnel():
    """
    停止 cloudflare tunnel。

    核心作用：
      终止 cloudflared 子进程并清空隧道 URL，便于重新创建或退出程序。

    返回值：无。

    调用场景：
      程序退出或切换分享项目时调用。
    """
    global _tunnel_process, _tunnel_url
    if _tunnel_process and _tunnel_process.poll() is None:
        _tunnel_process.kill()
    _tunnel_process = None
    _tunnel_url = ""


# ─── API ─────────────────────────────────────────────────────────────────────


@_app.get("/api/project_id")
def api_project_id():
    """
    获取当前分享的项目 ID。

    返回值：
      dict — {"project_id": int}。

    调用场景：
      前端页面初始化时确认当前分享的是哪个项目。
    """
    return {"project_id": _project_id}


@_app.get("/api/qr/generate")
def api_qr_generate():
    """
    生成 B站网页端二维码。

    核心作用：
      调用 B站 passport 接口获取二维码 URL 和 qrcode_key，
      供前端渲染二维码图片，实现被分享者扫码登录。

    返回值：
      dict — {"ok": True, "url": str, "qrcode_key": str} 或 {"ok": False, "error": str}。

    调用场景：
      分享页面 Step 1 初始化二维码时调用。
    """
    headers = {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
        ),
    }
    for _ in range(5):
        resp = http_requests.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if data.get("code") == 0:
            return {"ok": True, "url": data["data"]["url"], "qrcode_key": data["data"]["qrcode_key"]}
        time.sleep(0.5)
    return {"ok": False, "error": "二维码生成失败"}


@_app.get("/api/qr/poll")
def api_qr_poll(qrcode_key: str):
    """
    轮询 B站二维码登录状态。

    核心作用：
      前端扫码后周期性调用此接口，直到登录成功或超时。

    输入参数：
      qrcode_key : str — 二维码唯一标识。

    返回值：
      dict — 包含 ok、status（confirmed / waiting / failed）、cookies、message 等字段。

    调用场景：
      分享页面 Step 1 pollQR() 中周期性调用。
    """
    resp = http_requests.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
        params={"qrcode_key": qrcode_key},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=10,
    )
    data = resp.json()
    if data.get("code") != 0:
        return {"ok": False, "status": "waiting", "message": "等待扫码"}

    inner = data.get("data", {})
    code = inner.get("code")
    if code == 0:
        from util.CookieManager import parse_cookie_list
        cookies = parse_cookie_list(resp.headers.get("set-cookie", ""))
        return {"ok": True, "status": "confirmed", "cookies": cookies}
    if code in (86101, 86090):
        return {"ok": False, "status": "waiting", "message": inner.get("message", "等待确认")}
    return {"ok": False, "status": "failed", "message": inner.get("message", "登录失败")}


class CookiesBody(BaseModel):
    """
    携带 cookies 列表的请求体模型。

    属性：
      cookies : list[dict[str, str]] — B站登录后的 cookie 字典列表。

    使用场景：
      /api/project、/api/buyers、/api/addresses 等需要身份认证的接口。
    """
    cookies: list[dict[str, str]]


import time as _time
import uuid as _uuid

# 按 session_id 隔离的会话存储：{session_id: {"cookies": ..., "time": ...}}
_sessions: dict[str, dict] = {}
# 会话有效期，单位秒（10 小时）
SESSION_TTL = 36000


def _get_session_id(request) -> str:
    """
    从请求 Cookie 中读取 session_id。

    核心作用：
      检查请求中是否携带有效的 btb_sid Cookie；有效则返回该 session_id，否则返回空字符串。

    输入参数：
      request : Request — FastAPI Request 对象。

    返回值：
      str — 已存在的有效 session_id，或空字符串。

    调用场景：
      api_session() / api_project() 中识别当前浏览器会话。
    """
    sid = request.cookies.get("btb_sid")
    if sid and sid in _sessions:
        return sid
    return ""


def _new_session_id() -> str:
    """
    生成新的 session_id。

    返回值：
      str — 16 位十六进制随机字符串。

    调用场景：
      api_session() / api_project() 中为新的浏览器会话创建标识。
    """
    return _uuid.uuid4().hex[:16]


def _save_session(session_id: str, cookies: list[dict[str, str]]):
    """
    保存会话 cookies 并清理过期会话。

    输入参数：
      session_id : str — 会话 ID。
      cookies    : list[dict[str, str]] — 该会话对应的 B站 cookies。

    返回值：无。

    调用场景：
      api_project() / api_session() 中登录成功后保存会话。
    """
    _sessions[session_id] = {"cookies": cookies, "time": _time.time()}
    _cleanup_sessions()


def _get_session(session_id: str) -> list[dict[str, str]] | None:
    """
    获取指定会话的 cookies（未过期时）。

    输入参数：
      session_id : str — 会话 ID。

    返回值：
      list[dict[str, str]] | None — 有效 cookies 列表；不存在或已过期返回 None。

    调用场景：
      api_session() 中恢复会话登录状态。
    """
    entry = _sessions.get(session_id)
    if entry and entry["cookies"] and _time.time() - entry["time"] < SESSION_TTL:
        return entry["cookies"]
    return None


def _cleanup_sessions():
    """
    清理超过 SESSION_TTL 的过期会话。

    返回值：无。

    调用场景：
      _save_session() 中保存新会话后自动清理，防止内存无限增长。
    """
    now = _time.time()
    expired = [k for k, v in _sessions.items() if now - v["time"] >= SESSION_TTL]
    for k in expired:
        del _sessions[k]


from fastapi import Request

@_app.get("/api/session")
def api_session(request: Request):
    """
    获取或创建浏览器会话。

    核心作用：
      若请求携带有效 session_id，则返回对应 cookies；否则创建新会话并下发 btb_sid Cookie。

    输入参数：
      request : Request — FastAPI Request 对象。

    返回值：
      JSONResponse — {"ok": True, "cookies": ...} 或 {"ok": False, "session_id": ...}。

    调用场景：
      分享页面加载时首先调用，用于恢复登录状态或进入二维码登录流程。
    """
    sid = _get_session_id(request)
    saved = _get_session(sid) if sid else None
    if saved:
        resp = JSONResponse({"ok": True, "cookies": saved})
        return resp
    # 没有 session，返回新的 session_id
    new_sid = _new_session_id()
    resp = JSONResponse({"ok": False, "session_id": new_sid})
    resp.set_cookie("btb_sid", new_sid, max_age=SESSION_TTL, httponly=True)
    return resp


@_app.post("/api/project")
def api_project(body: CookiesBody, http_request: Request):
    """
    获取当前分享项目的票档信息。

    核心作用：
      1. 保存/恢复浏览器会话 cookies。
      2. 使用 cookies 创建 BiliRequest，拉取项目详情。
      3. 按场次整理票档列表，含实际价格（电子票免运费）。
      4. 同步服务器下发的最新 cookies 并返回给前端。

    输入参数：
      body        : CookiesBody — 请求体，包含 cookies。
      http_request : Request — FastAPI Request 对象。

    返回值：
      JSONResponse — 项目 ID、名称、是否热门、场次票档列表、同步后的 cookies。

    调用场景：
      分享页面登录成功后 loadProject() 中调用。
    """
    sid = _get_session_id(http_request)
    if not sid:
        sid = _new_session_id()
    _save_session(sid, body.cookies)
    request = BiliRequest(cookies=body.cookies, proxy="none")
    from interface.project import fetch_project_payload
    try:
        data = fetch_project_payload(request=request, project_id=_project_id)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    # 获取服务器下发的最新 cookie
    try:
        synced_cookies = request.cookieManager.get_cookies(force=True)
    except Exception:
        synced_cookies = body.cookies

    has_eticket = bool(data.get("has_eticket", True))
    screens = []
    for screen in data.get("screen_list", []):
        if "name" not in screen:
            continue
        express_fee = 0 if has_eticket else max(int(screen.get("express_fee", 0) or 0), 0)
        tickets = []
        for t in screen.get("ticket_list", []):
            price = int(t.get("price", 0)) + express_fee
            sale_flag = t.get("sale_flag") or {}
            sale_flag_number = sale_flag.get("number") if isinstance(sale_flag, dict) else None
            tickets.append({
                "id": t.get("id"),
                "desc": t.get("desc", ""),
                "price": price,
                "sale_start": t.get("sale_start", ""),
                "sale_flag_number": sale_flag_number,
            })
        screens.append({
            "id": screen.get("id"),
            "name": screen.get("name", ""),
            "tickets": tickets,
        })

    resp = JSONResponse({
        "ok": True,
        "project_id": _project_id,
        "project_name": data.get("name", ""),
        "is_hot_project": bool(data.get("hotProject", False)),
        "screens": screens,
        "cookies": synced_cookies,
    })
    # 如果是新 session，设置 cookie
    if http_request.cookies.get("btb_sid") != sid:
        resp.set_cookie("btb_sid", sid, max_age=SESSION_TTL, httponly=True)
    return resp


@_app.post("/api/buyers")
def api_buyers(body: CookiesBody):
    """
    获取当前账号的实名购票人列表。

    输入参数：
      body : CookiesBody — 请求体，包含 cookies。

    返回值：
      dict — {"ok": True, "buyers": list[dict]}。

    调用场景：
      分享页面 Step 3 加载购票人时调用。
    """
    request = BiliRequest(cookies=body.cookies, proxy="none")
    resp = request.get(
        url="https://show.bilibili.com/api/ticket/buyer/list?is_default&projectId=" + str(_project_id)
    ).json()
    buyers = resp.get("data", {}).get("list", [])
    return {"ok": True, "buyers": buyers}


@_app.post("/api/addresses")
def api_addresses(body: CookiesBody):
    """
    获取当前账号的收货地址列表。

    输入参数：
      body : CookiesBody — 请求体，包含 cookies。

    返回值：
      dict — {"ok": True, "addresses": list[dict]}，地址字典包含 id、name、tel、addr、display。

    调用场景：
      分享页面 Step 3 加载收货地址时调用。
    """
    request = BiliRequest(cookies=body.cookies, proxy="none")
    resp = request.get(url="https://show.bilibili.com/api/ticket/addr/list").json()
    addrs = resp.get("data", {}).get("addr_list", [])
    return {
        "ok": True,
        "addresses": [
            {
                "id": a["id"],
                "name": a["name"],
                "tel": a["phone"],
                "addr": a["prov"] + a["city"] + a["area"] + a["addr"],
                "display": f"{a['prov']}{a['city']}{a['area']}{a['addr']}-{a['name']}-{a['phone']}",
            }
            for a in addrs
        ],
    }


class SubmitBody(BaseModel):
    """
    提交选票配置的请求体模型。

    属性：
      cookies          : list[dict[str, str]] — B站登录 cookies。
      screen_id        : int — 场次 ID。
      sku_id           : int — 票档（sku）ID。
      project_id       : int — 项目 ID。
      project_name     : str — 项目名称。
      is_hot_project   : bool — 是否为热门项目。
      sale_start       : str — 起售时间。
      sale_flag_number : int — 销售状态码。
      price            : int — 单张票价（分）。
      screen_name      : str — 场次名称。
      ticket_desc      : str — 票档描述。
      count            : int — 购买数量。
      buyer_name       : str — 联系人姓名。
      buyer_phone      : str — 联系人电话。
      buyer_info       : list[dict[str, Any]] — 实名购票人列表。
      deliver_info     : dict[str, Any] — 收货地址信息。

    使用场景：
      /api/submit 接口接收前端提交并生成抢票配置。
    """
    cookies: list[dict[str, str]]
    screen_id: int
    sku_id: int
    project_id: int
    project_name: str
    is_hot_project: bool
    sale_start: str
    sale_flag_number: int
    price: int
    screen_name: str
    ticket_desc: str
    count: int
    buyer_name: str
    buyer_phone: str
    buyer_info: list[dict[str, Any]]
    deliver_info: dict[str, Any]


@_app.post("/api/submit")
def api_submit(body: SubmitBody):
    """
    接收前端选票结果并生成抢票配置。

    核心作用：
      1. 使用 cookies 创建 BiliRequest，获取当前登录用户名并同步最新 cookies。
      2. 补齐默认收货地址（地址为空时）。
      3. 构建符合抢票任务要求的 JSON 配置，保存到“项目/{project_name}/”目录。
      4. 将扫码登录的账号保存到本地 cookies.json 账号池。
      5. 通过飞书 Webhook 推送配置文本。

    输入参数：
      body : SubmitBody — 前端提交的选票信息。

    返回值：
      dict — {"ok": True, "feishu_ok": bool, "config": dict}。

    调用场景：
      分享页面 Step 3 点击“确认发送”后 doSubmit() 调用。
    """
    request = BiliRequest(cookies=body.cookies, proxy="none")
    try:
        username = request.get_request_name()
    except Exception:
        username = "unknown"

    # 同步服务器下发的最新 cookie
    try:
        synced_cookies = request.cookieManager.get_cookies(force=True)
    except Exception:
        synced_cookies = body.cookies

    # 地址为空时自动填入默认地址
    deliver = body.deliver_info
    if not deliver.get("addr"):
        deliver = {
            "name": "张三",
            "tel": "18888888888",
            "addr_id": 0,
            "addr": "浙江省宁波市余姚市红旗路144号碧桂园",
        }

    # 构建 sale_status
    sale_status_map = {1: "不可售", 2: "预售", 3: "停售", 4: "售罄", 5: "不可用", 6: "库存紧张", 8: "暂时售罄", 9: "不在白名单", 101: "未开始", 102: "已结束", 103: "未完成", 105: "下架", 106: "已取消"}
    sale_status = sale_status_map.get(body.sale_flag_number, "未知状态")
    price_str = f"￥{body.price / 100:.2f}".rstrip("0").rstrip(".")
    ticket_str = f"{body.screen_name} - {body.ticket_desc} - {price_str} - {sale_status} - 【起售时间：{body.sale_start}】"
    detail = f"{username}-{body.project_name}-{ticket_str}"
    for b in body.buyer_info:
        detail += f"-{b.get('name', '')}"

    config = {
        "username": username,
        "detail": detail,
        "count": body.count,
        "screen_id": body.screen_id,
        "project_id": body.project_id,
        "is_hot_project": body.is_hot_project,
        "sku_id": body.sku_id,
        "sale_start": body.sale_start,
        "order_type": 1,
        "pay_money": body.price * body.count,
        "buyer_info": body.buyer_info,
        "buyer": body.buyer_name,
        "tel": body.buyer_phone,
        "deliver_info": {
            "name": deliver.get("name", "张三"),
            "tel": deliver.get("tel", "18888888888"),
            "addr_id": deliver.get("addr_id", 0),
            "addr": deliver.get("addr", "浙江省宁波市余姚市红旗路144号碧桂园"),
        },
        "cookies": synced_cookies,
        "phone": "",
    }

    # 保存 JSON 到本地项目文件夹
    try:
        import os
        project_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "项目", body.project_name)
        os.makedirs(project_dir, exist_ok=True)
        buyer_names = "_".join(b.get("name", "") for b in body.buyer_info)
        filename = f"{username}_{body.project_name}_{body.screen_name}_{body.ticket_desc}_{buyer_names}.json"
        filename = re.sub(r'[/:*?"<>|]', "", filename)
        filepath = os.path.join(project_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        logger.info(f"配置已保存: {filepath}")
    except Exception as exc:
        logger.error(f"保存配置文件失败: {exc}")

    # 将扫码登录的账号保存到 cookies.json 账号列表
    try:
        import util
        account = util.main_request.cookieManager.add_account(body.cookies)
        logger.info(f"分享账号已保存: {account.name} (uid={account.uid})")
    except Exception as exc:
        logger.error(f"保存分享账号失败: {exc}")

    payload = {
        "msg_type": "text",
        "content": {
            "text": json.dumps(config, ensure_ascii=False, indent=2),
        },
    }
    try:
        resp = http_requests.post(FEISHU_WEBHOOK, json=payload, timeout=10)
        feishu_ok = resp.status_code == 200
    except Exception as exc:
        logger.error(f"飞书推送失败: {exc}")
        feishu_ok = False

    return {"ok": True, "feishu_ok": feishu_ok, "config": config}


# ─── HTML ─────────────────────────────────────────────────────────────────────

# 分享页面完整前端 HTML（含 QR 登录、票档选择、购票人/地址选择、提交确认）
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>选票</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Google Sans","Roboto","Noto Sans SC","PingFang SC",system-ui,sans-serif;background:#f5f5f5;color:#333;padding:20px}
.container{max-width:640px;margin:0 auto}
h1{text-align:center;margin-bottom:24px;font-size:22px;color:#fb7299}
.step{display:none;background:#fff;border-radius:12px;padding:24px;margin-bottom:16px;box-shadow:0 2px 8px rgba(0,0,0,.08)}
.step.active{display:block}
.step-title{font-size:16px;font-weight:600;margin-bottom:16px;padding-bottom:8px;border-bottom:2px solid #fb7299}
label{display:block;font-size:14px;font-weight:500;margin-bottom:6px;color:#555}
input,select{width:100%;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-bottom:14px;outline:none;transition:border-color .2s}
input:focus,select:focus{border-color:#fb7299}
button{display:inline-block;padding:12px 28px;border:none;border-radius:8px;font-size:15px;font-weight:600;cursor:pointer;transition:all .2s}
.btn-primary{background:#fb7299;color:#fff;width:100%}
.btn-primary:hover{background:#e85a85}
.btn-primary:disabled{background:#ccc;cursor:not-allowed}
.btn-secondary{background:#f0f0f0;color:#333}
.btn-secondary:hover{background:#e0e0e0}
.qr-wrap{text-align:center}
.qr-wrap img{width:200px;height:200px;border:1px solid #eee;border-radius:8px}
.qr-status{margin-top:12px;font-size:13px;color:#888}
.ticket-card{border:1px solid #eee;border-radius:8px;padding:12px;margin-bottom:10px;cursor:pointer;transition:all .2s}
.ticket-card:hover,.ticket-card.selected{border-color:#fb7299;background:#fff5f7}
.ticket-card.selected{box-shadow:0 0 0 2px #fb7299}
.ticket-name{font-weight:600;font-size:14px}
.ticket-info{font-size:12px;color:#888;margin-top:4px}
.tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;margin-left:6px}
.tag-hot{background:#fff0f0;color:#fb7299}
.tag-sold{background:#f0f0f0;color:#999}
.msg{padding:12px;border-radius:8px;margin-bottom:12px;font-size:13px}
.msg-ok{background:#f0fff4;color:#38a169;border:1px solid #c6f6d5}
.msg-err{background:#fff5f5;color:#e53e3e;border:1px solid #fed7d7}
.row{display:flex;gap:12px}
.row>div{flex:1}
input[type=text],input[type=tel],select{width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #ddd;border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;transition:border-color .2s}
input[type=text]:focus,input[type=tel]:focus,select:focus{border-color:#fb7299}
.buyer-card{background:#fff;border:1px solid #e8e8e8;border-radius:8px;padding:10px 12px;margin-bottom:8px;cursor:pointer;display:flex;align-items:center;gap:10px;transition:all .2s}
.buyer-card:hover{border-color:#fb7299;background:#fffafc}
.buyer-card.checked{border-color:#fb7299;background:#fff5f7;box-shadow:0 0 0 1px #fb7299 inset}
.buyer-card input{margin:0;width:18px;height:18px;accent-color:#fb7299;cursor:pointer}
.buyer-card .b-name{font-weight:600;font-size:14px;color:#333}
.buyer-card .b-id{font-size:12px;color:#888;margin-left:6px}
</style>
</head>
<body>
<div class="container">
<h1>选票</h1>

<!-- Step 1: QR Login -->
<div id="step1" class="step active">
  <div class="step-title">Step 1: 扫码登录</div>
  <div id="session-hint" style="text-align:center;color:#fb7299;font-size:13px;padding:8px 0">正在检查登录状态...</div>
  <div class="qr-wrap">
    <div id="qr-loading">正在生成二维码...</div>
    <img id="qr-img" style="display:none"/>
    <div id="qr-status" class="qr-status"></div>
  </div>
</div>

<!-- Step 2: Select Ticket -->
<div id="step2" class="step">
  <div class="step-title">Step 2: 选择票档</div>
  <div id="ticket-list"></div>
  <div style="margin-top:16px;display:flex;gap:12px">
    <button class="btn-secondary" onclick="goStep(1)">上一步</button>
    <button id="btn-to-step3" class="btn-primary" style="width:auto;flex:1" onclick="goStep(3)" disabled>下一步</button>
  </div>
</div>

<!-- Step 3: Buyer & Address -->
<div id="step3" class="step">
  <div class="step-title">Step 3: 填写信息</div>
  <label>购票人姓名</label>
  <input id="buyer-name" placeholder="请输入姓名"/>
  <label>购票人电话</label>
  <input id="buyer-phone" type="tel" placeholder="请输入电话"/>
  <label>选择实名购票人（从B站账号获取）</label>
  <div id="buyer-select" style="background:#fafafa;border:1px solid #e8e8e8;border-radius:8px;padding:8px;max-height:160px;overflow-y:auto;margin-bottom:14px"></div>
  <label>选择收货地址（从B站账号获取）</label>
  <select id="addr-select"><option value="">无可用地址</option></select>
  <div style="margin-top:8px;display:flex;gap:12px">
    <button class="btn-secondary" onclick="goStep(2)">上一步</button>
    <button class="btn-primary" style="width:auto;flex:1" onclick="confirmSubmit()">生成配置</button>
  </div>
</div>

<!-- Confirm Modal -->
<div id="confirm-modal" style="display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:999;justify-content:center;align-items:center">
  <div style="background:#fff;border-radius:12px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto">
    <h3 style="margin-bottom:16px;font-size:17px;color:#fb7299">确认配置信息</h3>
    <div id="confirm-content" style="font-size:14px;line-height:1.8;color:#333"></div>
    <div style="margin-top:20px;display:flex;gap:12px">
      <button class="btn-secondary" style="flex:1" onclick="closeConfirm()">返回修改</button>
      <button class="btn-primary" style="flex:1" onclick="doSubmit()">确认发送</button>
    </div>
  </div>
</div>

<!-- Step 4: Done -->
<div id="step4" class="step">
  <div class="step-title">提交成功</div>
  <div id="submit-result"></div>
</div>

</div>

<script>
const API = window.location.origin;
let cookies = [];
let projectData = null;
let selectedTicket = null;

function goStep(n){
  document.querySelectorAll('.step').forEach(s=>s.classList.remove('active'));
  document.getElementById('step'+n).classList.add('active');
}

async function initQR(){
  document.getElementById('session-hint').style.display='none';
  try{
    const r = await fetch(API+'/api/qr/generate');
    const d = await r.json();
    if(!d.ok){document.getElementById('qr-loading').textContent=d.error||'生成失败';return;}
    const qrUrl = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data='+encodeURIComponent(d.url);
    const img = document.getElementById('qr-img');
    img.src = qrUrl;
    img.style.display='inline-block';
    document.getElementById('qr-loading').style.display='none';
    pollQR(d.qrcode_key);
  }catch(e){
    document.getElementById('qr-loading').textContent='网络错误: '+e.message;
  }
}

async function pollQR(key){
  const status = document.getElementById('qr-status');
  for(let i=0;i<120;i++){
    await new Promise(r=>setTimeout(r,1000));
    try{
      const r = await fetch(API+'/api/qr/poll?qrcode_key='+encodeURIComponent(key));
      const d = await r.json();
      if(d.ok && d.status==='confirmed'){
        cookies = d.cookies;
        status.textContent='登录成功！正在加载票务信息...';
        status.style.color='#38a169';
        await loadProject();
        return;
      }
      status.textContent = d.message || '等待扫码...';
    }catch(e){
      status.textContent='网络错误，重试中...';
    }
  }
  status.textContent='登录超时，请刷新页面重试';
  status.style.color='#e53e3e';
}

async function loadProject(){
  try{
    const r = await fetch(API+'/api/project',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies})});
    const d = await r.json();
    if(!d.ok){alert('获取票务信息失败: '+d.error);return;}
    if(d.cookies) cookies = d.cookies;
    projectData = d;
    renderTickets(d);
    goStep(2);
    loadBuyers();
    loadAddresses();
  }catch(e){
    alert('请求失败: '+e.message);
  }
}

function renderTickets(data){
  const list = document.getElementById('ticket-list');
  list.innerHTML='';
  data.screens.forEach(screen=>{
    screen.tickets.forEach(t=>{
      const card = document.createElement('div');
      card.className='ticket-card';
      const soldOut = [1,3,5,101,102,103,105,106].includes(t.sale_flag_number);
      const priceStr = '￥'+(t.price/100).toFixed(2).replace(/\.?0+$/,'');
      card.innerHTML=`<div class="ticket-name">${esc(screen.name)} - ${esc(t.desc)} <span class="tag tag-hot">${priceStr}</span>${soldOut?'<span class="tag tag-sold">不可售</span>':''}</div><div class="ticket-info">起售时间: ${esc(t.sale_start||'未知')}</div>`;
      card.dataset.screenId=screen.id;
      card.dataset.screenName=screen.name;
      card.dataset.skuId=t.id;
      card.dataset.desc=t.desc;
      card.dataset.price=t.price;
      card.dataset.saleStart=t.sale_start||'';
      card.dataset.saleFlag=t.sale_flag_number;
      card.onclick=function(){
        list.querySelectorAll('.ticket-card').forEach(c=>c.classList.remove('selected'));
        this.classList.add('selected');
        selectedTicket={
          screen_id:+this.dataset.screenId,
          screen_name:this.dataset.screenName,
          sku_id:+this.dataset.skuId,
          ticket_desc:this.dataset.desc,
          price:+this.dataset.price,
          sale_start:this.dataset.saleStart,
          sale_flag:+this.dataset.saleFlag,
        };
        document.getElementById('btn-to-step3').disabled=false;
      };
      list.appendChild(card);
    });
  });
  if(!list.children.length){
    list.innerHTML='<div class="msg msg-err">暂无可选票档</div>';
  }
}

async function loadBuyers(){
  try{
    const r=await fetch(API+'/api/buyers',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies})});
    const d=await r.json();
    if(!d.ok)return;
    const container=document.getElementById('buyer-select');
    container.innerHTML='';
    window._buyers=d.buyers;
    d.buyers.forEach((b,i)=>{
      const div=document.createElement('div');
      div.className='buyer-card';
      div.innerHTML=`<input type="checkbox" class="buyer-cb" data-index="${i}"/><span class="b-name">${esc(b.name)}</span><span class="b-id">${esc(b.personal_id)}</span>`;
      div.onclick=function(e){
        if(e.target.tagName!=='INPUT'){
          const cb=this.querySelector('.buyer-cb');
          cb.checked=!cb.checked;
        }
        div.classList.toggle('checked',div.querySelector('.buyer-cb').checked);
      };
      container.appendChild(div);
    });
    if(!d.buyers.length) container.innerHTML='<span style="color:#999;font-size:13px;padding:8px;display:block">无可用购票人</span>';
  }catch(e){}
}

async function loadAddresses(){
  try{
    const r=await fetch(API+'/api/addresses',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookies})});
    const d=await r.json();
    if(!d.ok)return;
    const sel=document.getElementById('addr-select');
    sel.innerHTML='';
    window._addresses = d.addresses;
    d.addresses.forEach((a,i)=>{
      const opt=document.createElement('option');
      opt.value=i;
      opt.textContent=a.display;
      sel.appendChild(opt);
    });
    if(!d.addresses.length) sel.innerHTML='<option value="">无可用地址</option>';
  }catch(e){}
}

function confirmSubmit(){
  if(!selectedTicket){alert('请先选择票档');return;}
  const buyerName=document.getElementById('buyer-name').value.trim();
  const buyerPhone=document.getElementById('buyer-phone').value.trim();
  if(!buyerName||!buyerPhone){alert('请填写购票人姓名和电话');return;}
  const selectedBuyers=Array.from(document.querySelectorAll('.buyer-cb:checked')).map(cb=>window._buyers[+cb.dataset.index]);
  if(!selectedBuyers.length){alert('请至少选择一位实名购票人');return;}
  const addrIdx=document.getElementById('addr-select').value;
  const addrData=window._addresses&&window._addresses[+addrIdx]?window._addresses[+addrIdx]:null;

  const buyerNames=selectedBuyers.map(b=>b.name).join('、');
  const addrDisplay=addrData?addrData.display:'未选择地址';
  const priceStr='￥'+(selectedTicket.price/100).toFixed(2).replace(/\.?0+$/,'');
  const totalPrice='￥'+(selectedTicket.price*selectedBuyers.length/100).toFixed(2).replace(/\.?0+$/,'');

  const html=`
    <div><b>项目：</b>${esc(projectData?projectData.project_name:'')}</div>
    <div><b>场次：</b>${esc(selectedTicket.screen_name)}</div>
    <div><b>票种：</b>${esc(selectedTicket.ticket_desc)}</div>
    <div><b>单价：</b>${priceStr}</div>
    <div><b>数量：</b>${selectedBuyers.length} 张</div>
    <div><b>总价：</b>${totalPrice}</div>
    <hr style="margin:8px 0;border:none;border-top:1px solid #eee"/>
    <div><b>购票人：</b>${esc(buyerNames)}</div>
    <div><b>联系人：</b>${esc(buyerName)}</div>
    <div><b>电话：</b>${esc(buyerPhone)}</div>
    <div><b>收货地址：</b>${esc(addrDisplay)}</div>
  `;
  document.getElementById('confirm-content').innerHTML=html;
  document.getElementById('confirm-modal').style.display='flex';
}

function closeConfirm(){
  document.getElementById('confirm-modal').style.display='none';
}

async function doSubmit(){
  closeConfirm();
  const buyerName=document.getElementById('buyer-name').value.trim();
  const buyerPhone=document.getElementById('buyer-phone').value.trim();
  const selectedBuyers=Array.from(document.querySelectorAll('.buyer-cb:checked')).map(cb=>window._buyers[+cb.dataset.index]);
  const addrIdx=document.getElementById('addr-select').value;
  const addrData=window._addresses&&window._addresses[+addrIdx]?window._addresses[+addrIdx]:null;

  const buyerNames=selectedBuyers.map(b=>b.name).join('、');
  const addrDisplay=addrData?addrData.display:'未选择地址（将使用默认地址）';
  const priceStr='￥'+(selectedTicket.price/100).toFixed(2).replace(/\.?0+$/,'');
  const totalPrice='￥'+(selectedTicket.price*selectedBuyers.length/100).toFixed(2).replace(/\.?0+$/,'');

  const summaryHtml=`
    <div><b>项目：</b>${esc(projectData?projectData.project_name:'')}</div>
    <div><b>场次：</b>${esc(selectedTicket.screen_name)}</div>
    <div><b>票种：</b>${esc(selectedTicket.ticket_desc)}</div>
    <div><b>单价：</b>${priceStr}</div>
    <div><b>数量：</b>${selectedBuyers.length} 张</div>
    <div><b>总价：</b>${totalPrice}</div>
    <hr style="margin:8px 0;border:none;border-top:1px solid #eee"/>
    <div><b>购票人：</b>${esc(buyerNames)}</div>
    <div><b>联系人：</b>${esc(buyerName)}</div>
    <div><b>电话：</b>${esc(buyerPhone)}</div>
    <div><b>收货地址：</b>${esc(addrDisplay)}</div>
  `;

  const body={
    cookies,
    screen_id:selectedTicket.screen_id,
    sku_id:selectedTicket.sku_id,
    project_id:projectData?projectData.project_id:0,
    project_name:projectData?projectData.project_name:'',
    is_hot_project:projectData?projectData.is_hot_project:false,
    sale_start:selectedTicket.sale_start,
    sale_flag_number:selectedTicket.sale_flag||0,
    price:selectedTicket.price,
    screen_name:selectedTicket.screen_name,
    ticket_desc:selectedTicket.ticket_desc,
    count:selectedBuyers.length,
    buyer_name:buyerName,
    buyer_phone:buyerPhone,
    buyer_info:selectedBuyers,
    deliver_info:{name:addrData?addrData.name:buyerName,tel:addrData?addrData.tel:buyerPhone,addr_id:addrData?addrData.id:'',addr:addrData?addrData.addr:''},
  };

  try{
    const r=await fetch(API+'/api/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const d=await r.json();
    const res=document.getElementById('submit-result');
    if(d.ok){
      res.innerHTML='<div class="msg msg-ok">配置已生成并发送！'+(d.feishu_ok?'':'（推送失败，但配置已返回）')+'</div>'+summaryHtml;
    }else{
      res.innerHTML='<div class="msg msg-err">提交失败</div>';
    }
    goStep(4);
  }catch(e){
    alert('提交失败: '+e.message);
  }
}

function esc(s){const d=document.createElement('div');d.textContent=s;return d.innerHTML;}

(async function(){
  try{
    const r=await fetch(API+'/api/session');
    const d=await r.json();
    if(d.ok&&d.cookies){
      cookies=d.cookies;
      await loadProject();
      if(projectData){
        document.getElementById('session-hint').textContent='已恢复登录状态';
        setTimeout(()=>document.getElementById('session-hint').style.display='none',2000);
        return;
      }
    }
  }catch(e){}
  initQR();
})();
</script>
</body>
</html>"""


@_app.get("/", response_class=HTMLResponse)
def index():
    """
    分享服务首页路由。

    返回值：
      HTMLResponse — 返回选票页面 HTML。

    调用场景：
      用户访问本地服务根路径或公网隧道根路径时展示选票页面。
    """
    return HTML_PAGE


@_app.get("/share/{project_id}", response_class=HTMLResponse)
def share_page(project_id: int):
    """
    设置当前分享项目 ID 并返回选票页面。

    输入参数：
      project_id : int — 要分享的项目 ID。

    返回值：
      HTMLResponse — 返回选票页面 HTML。

    调用场景：
      外部通过 /share/{project_id} 链接访问时，先设置项目再展示页面。
    """
    global _project_id
    _project_id = project_id
    return HTML_PAGE
