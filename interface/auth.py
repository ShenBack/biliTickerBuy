"""
interface/auth.py — B站会员购登录状态管理与二维码登录接口。

文件整体功能：
  1. 基于本地 Cookie 文件或显式传入的 Cookie 列表判断当前登录状态并获取用户名。
  2. 提供二维码登录完整流程：生成二维码（含图片保存）、轮询扫码结果、解析登录 Cookie。
  3. 支持直接使用 Cookie 列表完成登录校验。

所属模块：接口层 (interface)
依赖文件：
  - interface.common  (_cookie_store_path / _fetch_username_silently / _make_request / _resolve_cookie_list)

对外能力：
  - get_login_state      → 查询当前登录状态。
  - start_qr_login       → 启动二维码登录并生成二维码图片。
  - poll_qr_login        → 轮询二维码扫码状态直至登录成功或超时。
  - login_with_cookies   → 使用已有 Cookie 列表校验登录并返回用户信息。
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

import requests

from .common import (
    _cookie_store_path,
    _fetch_username_silently,
    _make_request,
    _resolve_cookie_list,
)


def get_login_state(
    *,
    cookies: list[dict[str, object]] | dict[str, object] | None = None,
    cookies_path: str | Path | None = None,
) -> dict[str, object]:
    """
    获取当前登录状态。

    核心作用：
      1. 通过 _resolve_cookie_list 解析传入的 Cookie 列表或本地 Cookie 文件。
      2. 调用 _fetch_username_silently 获取 B站用户名，判断是否存在有效登录态。
      3. 返回包含登录状态、用户名、Cookie 路径及下一步建议动作的字典。

    输入参数：
      - cookies      : list[dict[str, object]] | dict[str, object] | None — 显式传入的 Cookie。
        当传入 dict 且包含 "cookie" 键时，将提取其中的列表。
      - cookies_path : str | Path | None — Cookie 文件路径；为空时尝试读取 config.json 中的配置或默认 cookies.json。

    返回值：
      dict[str, object] — {
        "ok": True,
        "logged_in": bool,          # 是否已登录
        "username": str,            # 用户名或 "Not login"
        "has_cookies": bool,        # 是否解析到 Cookie
        "cookies_path": str | None, # Cookie 存储路径
        "next_action": str          # "continue" 或 "prompt_qr_login"
      }。

    调用位置：
      由 search_tickets 等需要先确认登录态的接口调用。
    """
    cookie_list = _resolve_cookie_list(cookies, cookies_path=cookies_path)
    has_cookies = bool(cookie_list)
    username = _fetch_username_silently(cookie_list)
    logged_in = has_cookies and username != "Not login"
    return {
        "ok": True,
        "logged_in": logged_in,
        "username": username,
        "has_cookies": has_cookies,
        "cookies_path": _cookie_store_path(cookies_path),
        "next_action": "continue" if logged_in else "prompt_qr_login",
    }


def start_qr_login(
    *,
    headers: dict[str, str] | None = None,
    max_retry: int = 10,
    retry_interval: float = 1.0,
    qr_image_path: str | Path | None = None,
) -> dict[str, object]:
    """
    启动二维码登录流程并生成二维码图片。

    核心作用：
      1. 调用 B站 passport 二维码生成接口获取 login_url 与 qrcode_key。
      2. 在指定路径生成 PNG 二维码图片，便于用户扫码。
      3. 若接口调用失败，按 max_retry 次数重试。

    输入参数：
      - headers        : dict[str, str] | None — 自定义请求头；为空时使用内置 UA。
      - max_retry      : int — 最大重试次数，默认 10。
      - retry_interval : float — 失败后的重试间隔（秒），默认 1.0。
      - qr_image_path  : str | Path | None — 二维码图片保存路径；为空时保存到系统临时目录。
        传 False 时不生成图片（用于兼容纯文本场景）。

    返回值：
      dict[str, object] — 成功时返回 {
        "ok": True,
        "login_url": str,
        "qrcode_key": str,
        "qr_image_path": str | None,
        "next_action": "show_qr_and_confirm_scan"
      }；失败时返回 {"ok": False, "error": str}。

    调用位置：
      由上层登录流程在需要引导用户扫码时调用。
    """
    import qrcode

    request_headers = headers or {
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
        ),
    }
    last_error = "二维码生成失败"
    for _ in range(max_retry):
        response = requests.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
            headers=request_headers,
            timeout=10,
        )
        payload = response.json()
        if payload.get("code") == 0:
            data = payload["data"]
            image_path_value: str | None = None
            if qr_image_path is not False:
                target = (
                    Path(qr_image_path)
                    if qr_image_path is not None
                    else Path(tempfile.gettempdir()) / "biliTickerBuy-login-qrcode.png"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                qr = qrcode.QRCode(box_size=10, border=4)
                qr.add_data(data["url"])
                qr.make(fit=True)
                qr.make_image(fill_color="black", back_color="white").save(target)
                image_path_value = str(target)
            return {
                "ok": True,
                "login_url": data["url"],
                "qrcode_key": data["qrcode_key"],
                "qr_image_path": image_path_value,
                "next_action": "show_qr_and_confirm_scan",
            }
        last_error = payload.get("message", last_error)
        time.sleep(retry_interval)
    return {"ok": False, "error": last_error}


def poll_qr_login(
    qrcode_key: str,
    *,
    cookies_path: str | Path | None = None,
    timeout_seconds: float = 60.0,
    poll_interval: float = 0.5,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    """
    轮询二维码扫码状态，直至登录成功、失败或超时。

    核心作用：
      1. 使用 qrcode_key 循环调用 passport 二维码轮询接口。
      2. 状态码 0 表示扫码确认，解析响应头中的 set-cookie 并保存/返回。
      3. 状态码 86101（未扫码）与 86090（已扫码未确认）时继续轮询。
      4. 超时或其他状态码返回对应失败结果。

    输入参数：
      - qrcode_key     : str — start_qr_login 返回的二维码唯一标识，必填。
      - cookies_path   : str | Path | None — 登录成功后 Cookie 保存路径。
      - timeout_seconds: float — 轮询总超时（秒），默认 60.0。
      - poll_interval  : float — 每次轮询间隔（秒），默认 0.5。
      - headers        : dict[str, str] | None — 自定义请求头；为空时使用简单 UA。

    返回值：
      dict[str, object] — 成功时返回 {
        "ok": True,
        "status": "confirmed",
        "message": "登录成功",
        "cookies": list[dict],
        "cookies_path": str | None,
        "username": str
      }；失败/超时返回 {"ok": False, "status": "failed"|"timeout", "message": str, "code": int|None}。

    调用位置：
      由上层登录流程在展示二维码后调用，与 start_qr_login 配套使用。
    """
    from util.request.CookieManager import parse_cookie_list

    if not qrcode_key:
        raise ValueError("qrcode_key is required")

    request_headers = headers or {"User-Agent": "Mozilla/5.0"}
    deadline = time.time() + timeout_seconds
    last_message = "等待扫码"

    while time.time() < deadline:
        response = requests.get(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"qrcode_key": qrcode_key},
            headers=request_headers,
            timeout=10,
        )
        payload = response.json()
        if payload.get("code") != 0:
            last_message = payload.get("message", "轮询登录失败")
            time.sleep(poll_interval)
            continue

        data = payload.get("data", {})
        state_code = data.get("code")
        last_message = data.get("message", last_message)

        if state_code == 0:
            cookies = parse_cookie_list(response.headers.get("set-cookie", ""))
            cookie_path_value = _cookie_store_path(cookies_path)
            request = _make_request(cookies=cookies, cookies_path=cookie_path_value)
            username = request.get_request_name()
            return {
                "ok": True,
                "status": "confirmed",
                "message": "登录成功",
                "cookies": cookies,
                "cookies_path": cookie_path_value,
                "username": username,
            }

        if state_code in (86101, 86090):
            time.sleep(poll_interval)
            continue

        return {
            "ok": False,
            "status": "failed",
            "message": last_message,
            "code": state_code,
        }

    return {"ok": False, "status": "timeout", "message": last_message or "登录超时"}


def login_with_cookies(
    cookies: list[dict[str, object]] | dict[str, object],
    *,
    cookies_path: str | Path | None = None,
) -> dict[str, object]:
    """
    使用已有 Cookie 列表校验登录状态。

    核心作用：
      1. 解析传入 Cookie 列表。
      2. 调用 _fetch_username_silently 获取用户名，判断登录是否有效。
      3. 返回登录结果与 Cookie 存储路径。

    输入参数：
      - cookies      : list[dict[str, object]] | dict[str, object] — 待校验的 Cookie 数据，必填。
      - cookies_path : str | Path | None — Cookie 文件保存路径（仅用于返回路径信息）。

    返回值：
      dict[str, object] — {
        "ok": True,
        "logged_in": bool,
        "username": str,
        "cookies": list[dict] | None,
        "cookies_path": str | None
      }。

    调用位置：
      由外部在已获取 Cookie 后调用，用于确认 Cookie 是否有效。
    """
    cookie_list = _resolve_cookie_list(cookies, cookies_path=cookies_path)
    username = _fetch_username_silently(cookie_list)
    return {
        "ok": True,
        "logged_in": username != "Not login",
        "username": username,
        "cookies": cookie_list,
        "cookies_path": _cookie_store_path(cookies_path),
    }
