"""
interface/search.py — B站会员购票务搜索与结果格式化接口。

文件整体功能：
  1. 基于关键词搜索 B站会员购演出/票务项目，支持分页。
  2. 在发起搜索前校验登录状态，未登录时返回友好的登录提示。
  3. 将搜索结果格式化为中文文本，便于聊天机器人或日志展示。

所属模块：接口层 (interface)
依赖文件：
  - interface.auth   (get_login_state)
  - interface.common (_cookies_to_header / _resolve_cookie_list)

对外能力：
  - search_tickets                    → 按关键词分页搜索票务项目。
  - format_ticket_search_results_text → 将搜索结果格式化为可读文本。
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode

import requests
from typing import cast, List

from .auth import get_login_state
from .common import _cookies_to_header, _resolve_cookie_list


def search_tickets(
    keyword: str,
    *,
    page: int = 1,
    pagesize: int = 16,
    platform: str = "web",
    cookies: list[dict[str, object]] | dict[str, object] | None = None,
    cookies_path: str | Path | None = None,
) -> dict[str, object]:
    """
    按关键词搜索 B站会员购票务项目。

    核心作用：
      1. 校验 keyword 非空。
      2. 调用 get_login_state 检查当前是否已登录；未登录时直接返回需要登录的提示。
      3. 解析 Cookie 并构造请求头，调用 show.bilibili.com 搜索接口。
      4. 对响应进行错误处理，返回包含结果列表与总数的字典。

    输入参数：
      - keyword     : str — 搜索关键词，必填且不能为空字符串。
      - page        : int — 页码，从 1 开始，默认 1。
      - pagesize    : int — 每页结果数，默认 16。
      - platform    : str — 请求平台标识，默认 "web"。
      - cookies     : list[dict[str, object]] | dict[str, object] | None — 显式传入的 Cookie。
      - cookies_path: str | Path | None — Cookie 文件路径。

    返回值：
      dict[str, object] — 成功时返回 {
        "ok": True,
        "keyword": str,
        "page": int,
        "pagesize": int,
        "total": int,
        "results": list[dict],
        "requires_login": False,
        "username": str,
        "cookies_path": str | None
      }；未登录时 requires_login=True 并附带 next_action="prompt_login"。

    调用位置：
      由上层搜索入口（如聊天机器人指令、Web API）调用。
    """
    if not keyword or not keyword.strip():
        raise ValueError("keyword is required")

    login_state = get_login_state(cookies=cookies, cookies_path=cookies_path)
    if not login_state.get("logged_in"):
        return {
            "ok": False,
            "keyword": keyword.strip(),
            "page": page,
            "pagesize": pagesize,
            "total": 0,
            "results": [],
            "requires_login": True,
            "error": "当前未登录，请先登录后再搜索",
            "next_action": "prompt_login",
            "username": login_state.get("username", "未登录"),
            "cookies_path": login_state.get("cookies_path"),
        }

    active_cookies = _resolve_cookie_list(cookies, cookies_path=cookies_path)
    params = urlencode(
        {
            "version": 134,
            "keyword": keyword.strip(),
            "pagesize": pagesize,
            "page": page,
            "platform": platform,
        }
    )
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "referer": "https://show.bilibili.com/platform/search.html?searchValue={0}".format(
            quote(keyword.strip(), safe="")
        ),
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "cookie": _cookies_to_header(active_cookies),
    }
    response = requests.get(
        "https://show.bilibili.com/api/ticket/search/list?{0}".format(params),
        headers=headers,
        timeout=10,
    ).json()
    errno = response.get("errno", response.get("code"))
    if errno != 0:
        raise RuntimeError(
            response.get("msg", response.get("message", "failed to search tickets"))
        )

    data = response.get("data") or {}
    results = data.get("result") or []
    return {
        "ok": True,
        "keyword": keyword.strip(),
        "page": page,
        "pagesize": pagesize,
        "total": data.get("total", len(results)),
        "results": results,
        "requires_login": False,
        "username": login_state.get("username", "未登录"),
        "cookies_path": login_state.get("cookies_path"),
    }


def format_ticket_search_results_text(
    search_result: dict[str, object],
    *,
    limit: int = 10,
) -> str:
    """
    将 search_tickets 返回的搜索结果格式化为中文文本。

    核心作用：
      1. 若结果要求登录，返回登录提示。
      2. 若结果为空，返回未找到相关票务的提示。
      3. 否则遍历前 limit 条结果，拼接标题、城市、场地、时间、价格、状态与链接。

    输入参数：
      - search_result : dict[str, object] — search_tickets 返回的字典。
      - limit         : int — 最多展示的条数，默认 10。

    返回值：
      str — 格式化后的中文文本，可直接用于消息回复或日志输出。

    调用位置：
      由聊天机器人、前端展示层或日志模块在获取搜索结果后调用。
    """
    keyword = search_result.get("keyword", "")
    if search_result.get("requires_login"):
        return "搜索“{0}”前需要先登录当前会员购账号。你先完成登录，我再继续帮你搜。".format(
            keyword
        )

    results = list(cast(List[dict[str, object]], search_result.get("results") or []))[
        :limit
    ]
    if not results:
        return "没有找到和“{0}”相关的票务结果。".format(keyword)

    lines = ["搜索结果：{0}".format(keyword), ""]
    for idx, item in enumerate(results, start=1):
        price_low = item.get("price_low")
        price_high = item.get("price_high")
        if isinstance(price_low, int) and isinstance(price_high, int):
            if price_low == price_high:
                price_text = "￥{0}".format(price_low / 100)
            else:
                price_text = "￥{0} - ￥{1}".format(price_low / 100, price_high / 100)
        else:
            price_text = "价格未知"

        lines.extend(
            [
                "{0}. {1}".format(
                    idx, item.get("title") or item.get("project_name") or "未知活动"
                ),
                "   城市：{0}  场地：{1}".format(
                    item.get("city", "未知城市"),
                    item.get("venue_name", "未知场地"),
                ),
                "   时间：{0}".format(
                    item.get("tlabel") or item.get("start_time", "未知时间")
                ),
                "   价格：{0}  状态：{1}".format(
                    price_text, item.get("sale_flag", "未知状态")
                ),
                "   链接：{0}".format(item.get("url", "")),
                "",
            ]
        )
    return "\n".join(lines).rstrip()
