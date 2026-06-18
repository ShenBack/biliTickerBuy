from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import requests as http_requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from util.BiliRequest import BiliRequest

FEISHU_WEBHOOK = "https://open.feishu.cn/open-apis/bot/v2/hook/1dd7ee3b-f730-426c-8a90-ee1533c6222e"

_app = FastAPI()
_server_thread: threading.Thread | None = None
_running_port: int = 0

_project_id: int = 0
_tunnel_process = None
_tunnel_url: str = ""


def start_share_server(project_id: int, port: int = 7862) -> int:
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
    """启动 cloudflare tunnel，返回公网 URL"""
    import shutil
    import subprocess

    global _tunnel_process, _tunnel_url

    if _tunnel_process and _tunnel_process.poll() is None:
        return _tunnel_url

    cloudflared = shutil.which("cloudflared")
    if not cloudflared:
        raise RuntimeError(
            "未找到 cloudflared，请先安装：\n"
            "Windows: winget install Cloudflare.cloudflared\n"
            "或下载: https://github.com/cloudflare/cloudflared/releases"
        )

    _tunnel_process = subprocess.Popen(
        [cloudflared, "tunnel", "--url", f"http://localhost:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    import re

    deadline = time.time() + 30
    url_pattern = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")

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
    global _tunnel_process, _tunnel_url
    if _tunnel_process and _tunnel_process.poll() is None:
        _tunnel_process.kill()
    _tunnel_process = None
    _tunnel_url = ""


# ─── API ─────────────────────────────────────────────────────────────────────


@_app.get("/api/project_id")
def api_project_id():
    return {"project_id": _project_id}


@_app.get("/api/qr/generate")
def api_qr_generate():
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
    cookies: list[dict[str, str]]


import time as _time
import uuid as _uuid

# 按 session_id 隔离的会话存储：{session_id: {"cookies": ..., "time": ...}}
_sessions: dict[str, dict] = {}
SESSION_TTL = 36000  # 10 hour


def _get_session_id(request) -> str:
    """从请求 Cookie 中读取 session_id，没有则生成一个新的"""
    sid = request.cookies.get("btb_sid")
    if sid and sid in _sessions:
        return sid
    return ""


def _new_session_id() -> str:
    return _uuid.uuid4().hex[:16]


def _save_session(session_id: str, cookies: list[dict[str, str]]):
    _sessions[session_id] = {"cookies": cookies, "time": _time.time()}
    _cleanup_sessions()


def _get_session(session_id: str) -> list[dict[str, str]] | None:
    entry = _sessions.get(session_id)
    if entry and entry["cookies"] and _time.time() - entry["time"] < SESSION_TTL:
        return entry["cookies"]
    return None


def _cleanup_sessions():
    now = _time.time()
    expired = [k for k, v in _sessions.items() if now - v["time"] >= SESSION_TTL]
    for k in expired:
        del _sessions[k]


from fastapi import Request

@_app.get("/api/session")
def api_session(request: Request):
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
    request = BiliRequest(cookies=body.cookies, proxy="none")
    resp = request.get(
        url="https://show.bilibili.com/api/ticket/buyer/list?is_default&projectId=" + str(_project_id)
    ).json()
    buyers = resp.get("data", {}).get("list", [])
    return {"ok": True, "buyers": buyers}


@_app.post("/api/addresses")
def api_addresses(body: CookiesBody):
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
        filename = f"{body.project_name}_{body.screen_name}_{body.ticket_desc}_{buyer_names}.json"
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

HTML_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>选票</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Noto Sans SC","PingFang SC","Microsoft YaHei",sans-serif;background:#f5f5f5;color:#333;padding:20px}
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
      const priceStr = '￥'+(t.price/100).toFixed(2).replace(/\\.?0+$/,'');
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
  const priceStr='￥'+(selectedTicket.price/100).toFixed(2).replace(/\\.?0+$/,'');
  const totalPrice='￥'+(selectedTicket.price*selectedBuyers.length/100).toFixed(2).replace(/\\.?0+$/,'');

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
  const priceStr='￥'+(selectedTicket.price/100).toFixed(2).replace(/\\.?0+$/,'');
  const totalPrice='￥'+(selectedTicket.price*selectedBuyers.length/100).toFixed(2).replace(/\\.?0+$/,'');

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
    return HTML_PAGE


@_app.get("/share/{project_id}", response_class=HTMLResponse)
def share_page(project_id: int):
    global _project_id
    _project_id = project_id
    return HTML_PAGE
