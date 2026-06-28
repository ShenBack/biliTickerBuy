"""
tab/settings.py — 账号登录与配置生成页面。

文件整体功能：
  提供 Gradio UI 的两个核心标签页：
  1. login_tab()：账号登录与管理页，支持二维码扫码登录、Cookie 文件导入、
                  账号切换/删除、实名购票人导入、用 Cookie 打开 B 站等功能。
  2. setting_tab()：票务配置生成页，支持输入活动链接获取票档、选择日期、
                    选择购票人和收货地址，最终生成 JSON 配置文件供抢票使用。

  本文件还包含大量内部辅助函数，用于：
  - 项目 ID 解析（链接/纯数字）
  - 按日期获取场次与票档信息
  - 票务信息 HTML 渲染
  - 实名购票人数据持久化（people.json）
  - Cookie 同步与账号池管理

所属模块：UI 层 (tab)
依赖文件：
  - interface.common           (_format_sale_status)
  - interface.project          (fetch_project_payload)
  - util                       (ConfigDB / CONFIGS_DIR / GLOBAL_COOKIE_PATH / TEMP_PATH / EXE_PATH)
  - util.request.BiliRequest   (HTTP 请求封装)
  - util.request.CookieManager (Cookie 与账号池管理)

对外能力：
  - login_tab()   → Gradio 组件元组，供 ticker.py 注册"账号登录"标签页。
  - setting_tab() → Gradio 组件，供 ticker.py 注册"生成配置"标签页。
"""

import html
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from typing import Any
from typing import Dict
from typing import List
from urllib.parse import parse_qs
from urllib.parse import urlparse

import gradio as gr
import qrcode
import requests
import util
from loguru import logger

from interface.common import _format_sale_status
from interface.project import fetch_project_payload
from util import ConfigDB
from util import CONFIGS_DIR
from util import GLOBAL_COOKIE_PATH
from util import TEMP_PATH
from util import set_main_request
from util.request.BiliRequest import BiliRequest
from util.request.CookieManager import parse_cookie_list

# ---------------------------------------------------------------------------
# 全局状态变量（由 on_submit_ticket_id 填充，供 on_submit_all 消费）
# ---------------------------------------------------------------------------

buyer_value: List[Dict[str, Any]] = []
addr_value: List[Dict[str, Any]] = []
ticket_value: List[Dict[str, Any]] = []
project_name: str = ""
ticket_str_list: List[str] = []
sales_dates: list[str] = []
project_id = 0
is_hot_project = False


# ---------------------------------------------------------------------------
# 账号与通用辅助函数
# ---------------------------------------------------------------------------

def _format_account_choice(uid: str, name: str, level: int, is_vip: bool = False) -> str:
    """
    格式化账号下拉框的选项文本。

    输入参数：
      uid    : str  — 用户 ID。
      name   : str  — 用户昵称。
      level  : int  — 用户等级。
      is_vip : bool — 是否为大会员。

    返回值：
      str — 形如 "123456 - 用户名 (Lv5)-大会员" 的选项文本。
    """
    vip_tag = "-大会员" if is_vip else ""
    return f"{uid} - {name} (Lv{level}){vip_tag}"


def _find_uid_from_choice(choice: str) -> str:
    """
    从账号选项文本中提取 UID。

    输入参数：
      choice : str — 账号选项文本。

    返回值：
      str — UID；若解析失败返回原字符串或空字符串。
    """
    if not choice:
        return ""
    return choice.split(" - ")[0] if " - " in choice else choice


def _resolve_default_account_choice(
    choices: list[str], active_uid: str | None = None
) -> str | None:
    """
    从账号选项列表中解析默认选中项。

    核心作用：
      若提供了 active_uid，则匹配对应的选项；否则返回列表第一项。

    输入参数：
      choices    : list[str] — 账号选项列表。
      active_uid : str | None — 当前活跃账号的 UID。

    返回值：
      str | None — 默认应选中的选项文本。
    """
    if not choices:
        return None

    if active_uid is not None:
        active_uid = str(active_uid)
        for choice in choices:
            if _find_uid_from_choice(choice) == active_uid:
                return choice

    return choices[0]


def _read_positive_int(value) -> int | None:
    """
    读取正整数，无效时返回 None。

    输入参数：
      value : 任意 — 待转换值。

    返回值：
      int | None — 大于 0 的整数，或 None。
    """
    if value is None:
        return None
    try:
        num = int(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


# ---------------------------------------------------------------------------
# 日期与场次相关辅助函数
# ---------------------------------------------------------------------------

def _iter_project_dates(start_ts: int, end_ts: int):
    """
    生成项目起止时间范围内的所有日期字符串。

    输入参数：
      start_ts : int — 开始时间戳（秒）。
      end_ts   : int — 结束时间戳（秒）。

    返回值：
      Generator[str, None, None] — 逐日 yield "YYYY-MM-DD" 格式字符串。
    """
    start_day = datetime.fromtimestamp(start_ts).date()
    end_day = datetime.fromtimestamp(end_ts).date()
    cursor = start_day
    while cursor <= end_day:
        yield cursor.strftime("%Y-%m-%d")
        cursor += timedelta(days=1)


def _fetch_screens_by_date(
    request: BiliRequest, project_id: int, date_str: str
) -> list[dict]:
    """
    通过 B站 API 按日期获取场次列表。

    核心作用：
      调用 /api/ticket/project/infoByDate 接口获取指定日期的 screen_list。

    输入参数：
      request    : BiliRequest — 已登录的请求对象。
      project_id : int — 项目 ID。
      date_str   : str — 日期字符串（YYYY-MM-DD）。

    返回值：
      list[dict] — 场次字典列表。

    异常：
      RuntimeError — API 返回非 0 错误码时抛出。
    """
    response = request.get(
        url=f"https://show.bilibili.com/api/ticket/project/infoByDate?id={project_id}&date={date_str}",
    )
    payload = response.json()
    errno = payload.get("errno", payload.get("code"))
    if errno != 0:
        raise RuntimeError(payload.get("msg", payload.get("message", "unknown error")))

    data = payload.get("data") if isinstance(payload, dict) else None
    screens = data.get("screen_list") if isinstance(data, dict) else None
    return screens if isinstance(screens, list) else []


def _normalize_date_string(value: Any) -> str | None:
    """
    将多种格式的日期输入标准化为 YYYY-MM-DD。

    支持的输入格式：
      - 时间戳（秒或毫秒）
      - 含分隔符的日期文本，如 "2025年8月3日"、"2025-08-03"

    输入参数：
      value : Any — 原始日期值。

    返回值：
      str | None — 标准化后的日期字符串；无法解析时返回 None。
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        timestamp = int(value)
        if timestamp > 10**12:
            timestamp //= 1000
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    match = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", text)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    try:
        return datetime(year, month, day).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _screen_matches_date(screen: dict[str, Any], date_str: str) -> bool:
    """
    判断场次信息是否匹配指定日期。

    核心作用：
      检查 screen 的 start_time、start_time_str、name 以及 ticket_list 中的
      screen_name 是否包含与 date_str 相同的日期。

    输入参数：
      screen   : dict — 场次字典。
      date_str : str — 目标日期（YYYY-MM-DD）。

    返回值：
      bool — True 表示匹配。
    """
    candidates = [
        screen.get("start_time"),
        screen.get("start_time_str"),
        screen.get("name"),
    ]
    for ticket in screen.get("ticket_list", []):
        if isinstance(ticket, dict):
            candidates.append(ticket.get("screen_name"))

    return any(
        _normalize_date_string(candidate) == date_str for candidate in candidates
    )


def _fetch_screens_by_date_with_fallback(
    request: BiliRequest, project_id: int, date_str: str
) -> list[dict]:
    """
    按日期获取场次，若接口无数据则回退到项目详情页数据匹配。

    核心作用：
      1. 先调用 _fetch_screens_by_date() 获取官方接口数据。
      2. 若为空，则调用 fetch_project_payload() 拉取项目详情，
         手动筛选匹配 date_str 的场次作为兜底。

    输入参数：
      request    : BiliRequest — 已登录的请求对象。
      project_id : int — 项目 ID。
      date_str   : str — 目标日期。

    返回值：
      list[dict] — 场次列表（可能为空）。
    """
    screens = _fetch_screens_by_date(request, project_id, date_str)
    if screens:
        return screens

    project_payload = fetch_project_payload(request=request, project_id=project_id)
    fallback_screens: list[dict] = []
    for screen in project_payload.get("screen_list", []):
        if not isinstance(screen, dict):
            continue
        if not _screen_matches_date(screen, date_str):
            continue
        screen["project_id"] = screen.get("project_id", project_id)
        fallback_screens.append(screen)
    return fallback_screens


def _merge_screens(base_screens: list[dict], extra_screens: list[dict]) -> list[dict]:
    """
    合并两组场次列表，按 screen_id 去重。

    输入参数：
      base_screens  : list[dict] — 基础场次列表。
      extra_screens : list[dict] — 补充场次列表。

    返回值：
      list[dict] — 合并去重后的场次列表。
    """
    merged: list[dict] = []
    seen_screen_ids: set[int] = set()

    for screen in [*base_screens, *extra_screens]:
        if not isinstance(screen, dict):
            continue
        sid = _read_positive_int(screen.get("id"))
        if sid is None or sid in seen_screen_ids:
            continue
        seen_screen_ids.add(sid)
        merged.append(screen)

    return merged


# ---------------------------------------------------------------------------
# 格式化与渲染辅助函数
# ---------------------------------------------------------------------------

def filename_filter(filename):
    """
    过滤文件名中的非法字符。

    输入参数：
      filename : str — 原始文件名。

    返回值：
      str — 去除 Windows 非法字符后的文件名。
    """
    return re.sub(r'[/:*?"<>|]', "", filename)


def _format_price(price: int | float) -> str:
    """
    将分单位价格格式化为人民币字符串。

    输入参数：
      price : int | float — 价格（单位：分）。

    返回值：
      str — 形如 "￥128.00" 或 "￥128" 的字符串。
    """
    return f"￥{price / 100:.2f}".rstrip("0").rstrip(".")


def _render_ticket_info_html(
    title: str,
    lines: list[tuple[str, str]],
    badge: str | None = None,
    hint: str | None = None,
) -> str:
    """
    渲染票务信息摘要 HTML。

    输入参数：
      title : str — 面板标题。
      lines : list[tuple[str, str]] — (标签, 值) 列表。
      badge : str | None — 徽章文本（当前未在 HTML 中使用，保留扩展）。
      hint  : str | None — 提示文本（当前未在 HTML 中使用，保留扩展）。

    返回值：
      str — 格式化的 HTML 字符串。
    """
    items_html = "".join(
        (
            '<div class="btb-mini-card">'
            f"<strong>{html.escape(label)}</strong>"
            f"<span>{html.escape(value)}</span>"
            "</div>"
        )
        for label, value in lines
    )
    return f"""
    <div class="btb-ticket-panel">
        <div class="btb-mini-grid">{items_html}</div>
    </div>
    """


def _empty_ticket_info_updates():
    """
    返回清空票务信息 UI 的 Gradio update 列表。

    返回值：
      list — 包含 6 个 gr.update() 的列表，用于重置下拉框和面板可见性。
    """
    return [
        gr.update(choices=[], value=None),
        gr.update(choices=[], value=[]),
        gr.update(choices=[], value=None),
        gr.update(visible=False),
        gr.update(value="", visible=False),
        gr.update(visible=False, value=None),
    ]


def _has_invalid_index(indices: list[int], values: list[Any]) -> bool:
    """
    检查索引列表是否包含越界或非整数项。

    输入参数：
      indices : list[int] — 索引列表。
      values  : list[Any] — 被索引的原始列表。

    返回值：
      bool — True 表示存在无效索引。
    """
    return any(
        not isinstance(item, int) or item < 0 or item >= len(values) for item in indices
    )


def _format_ticket_option(screen_name: str, ticket: dict, ticket_price: int) -> str:
    """
    格式化票档选项文本。

    输入参数：
      screen_name  : str — 场次名称。
      ticket       : dict — 票档字典。
      ticket_price : int — 实际价格（含运费）。

    返回值：
      str — 形如 "场次名 - 票档描述 - ￥128 - 售票中 - 【起售时间：2025-08-01 12:00】" 的文本。
    """
    ticket_desc = ticket.get("desc", "")
    sale_start = str(ticket.get("sale_start", "未知"))
    return (
        f"{screen_name} - {ticket_desc} - {_format_price(ticket_price)} - "
        f"{_format_sale_status(ticket)} - 【起售时间：{sale_start}】"
    )


# ---------------------------------------------------------------------------
# 实名购票人相关辅助函数
# ---------------------------------------------------------------------------

def _load_people_records() -> list[dict]:
    """
    从 people.json 加载本地实名购票人记录。

    返回值：
      list[dict] — 包含 name 和 personal_id 的字典列表；文件不存在或解析失败返回空列表。
    """
    people_path = os.path.join(util.EXE_PATH, "people.json")
    if not os.path.exists(people_path):
        return []
    try:
        with open(people_path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    records = []
    for item in data:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        personal_id = item.get("personal_id")
        if name and personal_id:
            records.append({"name": name, "personal_id": personal_id})
    return records


def _render_people_cards_html(records: list[dict] | None = None) -> str:
    """
    渲染实名购票人卡片网格 HTML。

    输入参数：
      records : list[dict] | None — 购票人记录；None 时自动从 people.json 加载。

    返回值：
      str — HTML 字符串；无记录时显示空状态提示。
    """
    if records is None:
        records = _load_people_records()
    if not records:
        return (
            '<div class="btb-people-empty">'
            "<span>暂无实名购票人数据，请先点击“从B站导入实名购票人(完整身份证)”</span>"
            "</div>"
        )
    cards = []
    for idx, item in enumerate(records, start=1):
        name = html.escape(str(item.get("name", "")))
        personal_id_raw = str(item.get("personal_id", ""))
        cards.append(
            '<div class="btb-people-card">'
            f'<div class="btb-people-index">#{idx}</div>'
            f'<div class="btb-people-name">{name}</div>'
            f'<div class="btb-people-id">{html.escape(personal_id_raw)}</div>'
            "</div>"
        )
    return (
        '<div class="btb-people-grid-view">'
        f'<div class="btb-people-count">共 {len(records)} 位实名购票人</div>'
        f'<div class="btb-people-cards">{"".join(cards)}</div>'
        "</div>"
    )


# ---------------------------------------------------------------------------
# 项目输入解析
# ---------------------------------------------------------------------------

def _resolve_project_input(project_input: Any) -> tuple[int, int | str, str]:
    """
    解析用户输入的项目 ID 或活动链接。

    核心作用：
      支持纯数字 ID、B站活动详情页 URL，以及带空格的混合格式。

    输入参数：
      project_input : Any — 用户输入值。

    返回值：
      tuple[int, int|str, str]
        - 解析后的整数 ID（用于 API 调用）
        - 原始 ID 或文本（用于配置显示）
        - 提示消息

    异常：
      gr.Error — 输入无效时抛出 Gradio 错误提示。
    """
    if isinstance(project_input, int):
        return project_input, project_input, ""

    text = str(project_input)
    stripped = text.strip()
    if not stripped:
        raise gr.Error("请输入活动详情页链接")

    if stripped.lower().isdigit():
        return (
            int(stripped),
            text,
            f"当前票务id为 {text}",
        )

    if "http" in stripped or "https" in stripped:
        extracted_id = extract_id_from_url(stripped)
        if extracted_id is None:
            raise gr.Error("无法从链接中识别票务 ID，请确认链接是会员购活动详情页。")
        return (
            int(extracted_id),
            int(extracted_id),
            f"已从链接中提取项目 ID：{extracted_id}",
        )

    if stripped.isdigit():
        parsed_id = int(stripped)
        return parsed_id, parsed_id, ""

    raise gr.Error("请输入活动详情页链接，或直接输入纯数字票务 ID。")


# ---------------------------------------------------------------------------
# 获取票务信息（核心回调）
# ---------------------------------------------------------------------------

def on_submit_ticket_id(num):
    """
    "获取票务信息"按钮回调。

    核心作用：
      1. 解析项目 ID，调用 fetch_project_payload() 获取项目详情。
      2. 按日期拉取每日场次并合并去重。
      3. 获取关联商品（周边）信息并加入票档列表。
      4. 获取购票人列表和收货地址列表。
      5. 输出票务信息、票档选项、购票人选项、地址选项等 UI 更新。

    输入参数：
      num : Any — 用户输入的项目 ID 或链接。

    返回值：
      list — 6 个 Gradio update 对象，用于更新票档、购票人、地址、详情面板等组件。

    异常处理：
      - 未登录时给出明确提示。
      - 其他异常记录日志并清空 UI。
    """
    global buyer_value
    global addr_value
    global ticket_value
    global project_name
    global ticket_str_list
    global sales_dates
    global project_id
    global is_hot_project

    logger.info(f"[获取票务信息] 按钮点击，输入值: {repr(num)}")
    logger.info(f"[获取票务信息] main_request 已登录: {util.main_request.cookieManager.have_cookies()}")
    try:
        current_name = util.main_request.get_request_name()
    except Exception:
        current_name = "<获取失败>"
    logger.info(f"[获取票务信息] 当前账号: {current_name}")

    def _raise_login_error(exc: Exception) -> None:
        message = str(exc).strip()
        if "当前未登录" in message or "请先登录" in message or "请重新登陆" in message:
            raise gr.Error(
                "当前未登录或登录状态已失效，请先在“账号登录”页重新登录。"
            ) from exc

    try:
        buyer_value = []
        addr_value = []
        ticket_value = []
        _, num, extracted_id_message = _resolve_project_input(num)
        logger.info(f"[获取票务信息] 解析后项目ID: {num}, 提示: {extracted_id_message}")

        try:
            data = fetch_project_payload(request=util.main_request, project_id=num)
            logger.info(f"[获取票务信息] 项目获取成功: {data.get('name', '?')}, screen_list 数量: {len(data.get('screen_list', []))}")
        except Exception as exc:
            logger.error(f"[获取票务信息] fetch_project_payload 失败: {exc}")
            raise gr.Error(
                str(exc) or "票务信息返回异常，当前活动页暂时不可用。"
            ) from exc

        ticket_str_list = []
        project_id = data["id"]
        project_name = data["name"]
        is_hot_project = data["hotProject"]
        sales_dates = [t["date"] for t in data["sales_dates"]]
        sales_dates_show = len(data["sales_dates"]) != 0
        for item in data["screen_list"]:
            item["project_id"] = data["id"]

        daily_screens: list[dict] = []
        for date_str in _iter_project_dates(data["start_time"], data["end_time"]):
            try:
                items = _fetch_screens_by_date_with_fallback(
                    util.main_request, project_id, date_str
                )
            except Exception:
                continue
            for item in items:
                if isinstance(item, dict):
                    item["project_id"] = data["id"]
                    daily_screens.append(item)

        data["screen_list"] = _merge_screens(data["screen_list"], daily_screens)

        try:
            good_list = util.main_request.get(
                url=f"https://show.bilibili.com/api/ticket/linkgoods/list?project_id={project_id}&page_type=0"
            ).json()
            ids = [item["id"] for item in good_list["data"]["list"]]
            for item_id in ids:
                good_detail = util.main_request.get(
                    url=f"https://show.bilibili.com/api/ticket/linkgoods/detail?link_id={item_id}"
                ).json()
                for item in good_detail["data"]["specs_list"]:
                    item["project_id"] = good_detail["data"]["item_id"]
                    item["link_id"] = item_id
                data["screen_list"] += good_detail["data"]["specs_list"]
        except Exception as exc:
            logger.warning(f"获取周边商品信息失败: {exc}")

        for screen in data["screen_list"]:
            if "name" not in screen:
                continue
            screen_name = screen["name"]
            screen_id = screen["id"]
            current_project_id = screen["project_id"]
            express_fee = (
                0
                if data["has_eticket"]
                else max(int(screen.get("express_fee", 0) or 0), 0)
            )

            for ticket in screen["ticket_list"]:
                ticket_price = int(ticket.get("price", 0)) + express_fee
                ticket["price"] = ticket_price
                ticket["screen"] = screen_name
                ticket["screen_id"] = screen_id
                ticket["is_hot_project"] = is_hot_project
                if "link_id" in screen:
                    ticket["link_id"] = screen["link_id"]
                ticket_str_list.append(
                    _format_ticket_option(screen_name, ticket, ticket_price)
                )
                ticket_value.append(
                    {"project_id": current_project_id, "ticket": ticket}
                )

        try:
            logger.info(f"[获取票务信息] 请求购票人列表, project_id={project_id}")
            buyer_json = util.main_request.get(
                url=f"https://show.bilibili.com/api/ticket/buyer/list?is_default&projectId={project_id}"
            ).json()
            logger.info(f"[获取票务信息] 购票人列表返回: code={buyer_json.get('code')}, 购票人数={len(buyer_json.get('data', {}).get('list', []))}")
            addr_json = util.main_request.get(
                url="https://show.bilibili.com/api/ticket/addr/list"
            ).json()
            logger.info(f"[获取票务信息] 地址列表返回: code={addr_json.get('code')}, 地址数={len(addr_json.get('data', {}).get('addr_list', []))}")
        except Exception as exc:
            logger.error(f"[获取票务信息] 购票人/地址请求失败: {exc}")
            _raise_login_error(exc)
            raise

        buyer_value = buyer_json["data"]["list"]
        buyer_str_list = [
            f"{item['name']}-{item['personal_id']}" for item in buyer_value
        ]
        addr_value = addr_json["data"]["addr_list"]
        
        # Cookie 同步：打印当前所有 Cookie（调试用途）
        logger.info("=" * 60)
        logger.info("[Cookie 同步] 选票信息加载完成，当前所有 Cookie：")
        for cookie in util.main_request.cookieManager.get_cookies(force=True) or []:
            logger.info(f"  {cookie.get('name')}: {cookie.get('value', '')[:50]}{'...' if len(cookie.get('value', '')) > 50 else ''}")
        logger.info("=" * 60)
        
        # 地址空值兜底
        if not addr_value:
            addr_value = [{
                "id": "default",
                "name": "SX",
                "phone": "18888888888",
                "prov": "浙江省",
                "city": "宁波市",
                "area": "余姚市",
                "addr": "红旗路144号碧桂园"
            }]
        
        addr_str_list = [
            f"{item['addr']}-{item['name']}-{item['phone']}" for item in addr_value
        ]

        yield [
            gr.update(choices=ticket_str_list, value=None),
            gr.update(choices=buyer_str_list, value=[]),
            gr.update(choices=addr_str_list, value=None),
            gr.update(visible=True),
            gr.update(
                value=_render_ticket_info_html(
                    title="票务信息",
                    badge="已获取",
                    lines=[
                        ("票务 ID", str(num)),
                        ("展会名称", project_name),
                    ],
                    hint=extracted_id_message or "请继续选择票档、购票人和地址。",
                ),
                visible=True,
            ),
            gr.update(choices=sales_dates, visible=True, value=sales_dates[0])
            if sales_dates_show
            else gr.update(choices=[], visible=False, value=None),
        ]
    except gr.Error as exc:
        logger.warning(f"[获取票务信息] gr.Error: {exc.message}")
        gr.Warning(exc.message)
        yield _empty_ticket_info_updates()
    except Exception as exc:
        if (
            "当前未登录" in str(exc)
            or "请先登录" in str(exc)
            or "请重新登陆" in str(exc)
        ):
            logger.warning(f"[获取票务信息] 未登录: {exc}")
            gr.Warning("当前未登录或登录状态已失效，请先在“账号登录”页重新登录。")
            yield _empty_ticket_info_updates()
            return
        logger.exception(f"[获取票务信息] 未知异常: {exc}")
        gr.Warning("获取票务信息失败，请确认活动链接是否正确，或稍后重试。")
        yield _empty_ticket_info_updates()


def extract_id_from_url(url):
    """
    从 B站活动详情页 URL 中提取项目 ID。

    输入参数：
      url : str — 活动详情页链接。

    返回值：
      str | None — 提取到的数字 ID；失败返回 None。
    """
    parsed_url = urlparse(url)
    query_params = parse_qs(parsed_url.query)
    ticket_id = query_params.get("id", [None])[0]
    if isinstance(ticket_id, str) and ticket_id.isdigit():
        return ticket_id
    return None


# ---------------------------------------------------------------------------
# 生成配置（核心回调）
# ---------------------------------------------------------------------------

def on_submit_all(
    ticket_id,
    ticket_info: int,
    people_indices,
    people_buyer_name,
    people_buyer_phone,
    address_index,
):
    """
    "生成配置"按钮回调。

    核心作用：
      校验用户输入，组装抢票所需完整配置字典，保存为 JSON 文件，
      并返回配置内容和文件路径供用户下载。

    输入参数：
      ticket_id          : Any — 活动链接或项目 ID。
      ticket_info        : int — 选中的票档索引。
      people_indices     : list[int] — 选中的购票人索引列表。
      people_buyer_name  : str — 联系人姓名。
      people_buyer_phone : str — 联系人电话。
      address_index      : int — 选中的地址索引。

    返回值：
      list — [gr.update(value=config_dir, visible=True), gr.update(value=filename, visible=True)]。

    异常：
      gr.Error — 任何校验失败或生成异常时抛出。
    """
    try:
        if ticket_id is None:
            raise gr.Error("请输入正确的活动链接。")
        if not isinstance(people_indices, list) or len(people_indices) == 0:
            raise gr.Error("请至少选择一位实名购票人。")
        if addr_value is None:
            raise gr.Error("没有可用的收货地址。")
        if ticket_info is None:
            raise gr.Error("请先选择票档。")
        if not people_buyer_name:
            raise gr.Error("请填写联系人姓名。")
        if not people_buyer_phone:
            raise gr.Error("请填写联系人电话。")
        if address_index is None:
            raise gr.Error("请先选择收货地址。")
        if _has_invalid_index(people_indices, buyer_value):
            raise gr.Error("实名购票人选择已失效，请重新获取票务信息后选择。")
        if (
            not isinstance(ticket_info, int)
            or ticket_info < 0
            or ticket_info >= len(ticket_value)
        ):
            raise gr.Error("票档选择已失效，请重新获取票务信息后选择。")
        if (
            not isinstance(address_index, int)
            or address_index < 0
            or address_index >= len(addr_value)
        ):
            raise gr.Error("收货地址选择已失效，请重新获取票务信息后选择。")

        ticket_cur: dict[str, Any] = ticket_value[ticket_info]
        people_cur = [buyer_value[item] for item in people_indices]
        resolved_project_id, config_project_id, _message = _resolve_project_input(
            ticket_id
        )

        ConfigDB.insert("people_buyer_name", people_buyer_name)
        ConfigDB.insert("people_buyer_phone", people_buyer_phone)

        address_cur = addr_value[address_index]
        username = util.main_request.get_request_name()
        detail = f"{username}-{project_name}-{ticket_str_list[ticket_info]}"
        for person in people_cur:
            detail += f"-{person['name']}"

        selected_project_id = ticket_cur["project_id"]
        if selected_project_id == resolved_project_id:
            selected_project_id = config_project_id

        config_dir = {
            "username": username,
            "detail": detail,
            "count": len(people_indices),
            "screen_id": ticket_cur["ticket"]["screen_id"],
            "project_id": selected_project_id,
            "is_hot_project": ticket_cur["ticket"]["is_hot_project"],
            "sku_id": ticket_cur["ticket"]["id"],
            "sale_start": ticket_cur["ticket"].get("sale_start", ""),
            "order_type": 1,
            "pay_money": ticket_cur["ticket"]["price"] * len(people_indices),
            "buyer_info": people_cur,
            "buyer": people_buyer_name,
            "tel": people_buyer_phone,
            "deliver_info": {
                "name": address_cur["name"],
                "tel": address_cur["phone"],
                "addr_id": 34309219 if address_cur["id"] == "default" else address_cur["id"],
                "addr": address_cur["prov"]
                + address_cur["city"]
                + address_cur["area"]
                + address_cur["addr"],
            },
            "cookies": util.main_request.cookieManager.get_cookies(),
            "phone": util.main_request.cookieManager.get_config_value("phone", ""),
        }
        if "link_id" in ticket_cur["ticket"]:
            config_dir["link_id"] = ticket_cur["ticket"]["link_id"]

        os.makedirs(CONFIGS_DIR, exist_ok=True)
        filename = os.path.join(CONFIGS_DIR, filename_filter(detail) + ".json")
        with open(filename, "w", encoding="utf-8") as handle:
            json.dump(config_dir, handle, ensure_ascii=False, indent=4)

        yield [
            gr.update(value=config_dir, visible=True),
            gr.update(value=filename, visible=True),
        ]
    except gr.Error as exc:
        gr.Warning(exc.message)
    except Exception:
        raise gr.Error("生成配置失败，请检查是否有遗漏的必填项。")


def upload_file(filepath):
    """
    导入 Cookie 文件并添加到账号池。

    输入参数：
      filepath : str — Cookie JSON 文件路径。

    返回值：
      list — [gr.update(value=GLOBAL_COOKIE_PATH), gr.update(choices=..., value=...)]。

    异常：
      gr.Error — 导入失败时抛出。
    """
    try:
        temp_request = BiliRequest(cookies_config_path=filepath)
        cookies = temp_request.cookieManager.get_cookies()
        account = util.main_request.cookieManager.add_account(cookies)
        set_main_request(BiliRequest(cookies_config_path=GLOBAL_COOKIE_PATH))
        util.main_request.cookieManager.db.insert("cookie", account.cookies)
        gr.Info(f"已导入账号 {account.name}", duration=5)

        new_choices = [
            _format_account_choice(a.uid, a.name, a.level, a.is_vip)
            for a in util.main_request.cookieManager.get_accounts()
        ]
        yield [
            gr.update(value=GLOBAL_COOKIE_PATH),
            gr.update(
                choices=new_choices,
                value=new_choices[-1] if new_choices else None,
            ),
        ]
    except Exception as exc:
        logger.exception(exc)
        raise gr.Error("登录信息导入失败，请检查文件格式。")


# ---------------------------------------------------------------------------
# 浏览器与 Cookie 工具函数
# ---------------------------------------------------------------------------

def _find_browser_exe() -> str | None:
    """
    查找系统中可用的浏览器可执行文件路径。

    搜索顺序：
      1. msedge（系统 PATH）
      2. chrome（系统 PATH）
      3. Edge/Chrome 的默认安装路径（Windows）

    返回值：
      str | None — 浏览器可执行文件完整路径；未找到返回 None。
    """
    candidates = [
        shutil.which("msedge"),
        shutil.which("chrome"),
        os.path.expandvars(
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
        ),
    ]
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return None


def _open_bilibili_with_cookies():
    """
    用当前 Cookie 打开 B站，并生成注入脚本复制到剪贴板。

    核心作用：
      1. 将当前账号 Cookie 拼接为 JavaScript document.cookie 注入代码。
      2. 复制到系统剪贴板。
      3. 启动浏览器（Edge/Chrome）并自动打开 DevTools，
         用户只需在 Console 中粘贴脚本即可登录。

    返回值：无（通过 gr.Info 提示用户操作步骤）。
    """
    cookies = util.main_request.cookieManager.get_cookies(force=True)
    if not cookies:
        gr.Warning("当前无登录信息，请先登录", duration=5)
        return

    # 生成 JS 脚本：设置 cookie 并刷新
    js_parts = []
    for c in cookies:
        name = c["name"]
        value = c["value"]
        # 单引号转义
        safe_value = value.replace("'", "\\'")
        js_parts.append(
            f"document.cookie='{name}={safe_value}; domain=.bilibili.com; path=/; max-age=31536000';"
        )
    js_parts.append("location.reload();")
    js_script = "\n".join(js_parts)

    # 复制到剪贴板
    try:
        subprocess.run(
            ["cmd", "/c", "clip"],
            input=js_script.encode("utf-16-le"),
            check=True,
            timeout=5,
        )
    except Exception:
        pass

    # 启动浏览器：自动打开 DevTools + 禁用控制台粘贴警告
    browser_exe = _find_browser_exe()
    if not browser_exe:
        import webbrowser
        webbrowser.open("https://www.bilibili.com")
    else:
        profile_dir = os.path.join(TEMP_PATH, "bili_browser_profile")
        subprocess.Popen(
            [
                browser_exe,
                f"--user-data-dir={profile_dir}",
                "--auto-open-devtools-for-tabs",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=DevToolsConsoleWarning",
                "--disable-popup-blocking",
                "https://www.bilibili.com",
            ]
        )

    gr.Info(
        "已打开 B 站并将 Cookie 脚本复制到剪贴板。\n"
        "在打开的 DevTools Console 中 Ctrl+V 粘贴并回车即可登录。",
        duration=10,
    )


# ---------------------------------------------------------------------------
# login_tab — 账号登录标签页
# ---------------------------------------------------------------------------

def login_tab():
    """
    构建"账号登录"标签页。

    核心作用：
      1. 提供手机号配置（可选）。
      2. 提供 B站二维码扫码登录流程（生成二维码 -> 扫码 -> 轮询登录状态）。
      3. 提供账号下拉框管理（切换、删除、刷新）。
      4. 支持导入现有 Cookie 文件、用 Cookie 打开 B站。
      5. 支持从 B站导入实名购票人并展示卡片网格。

    返回值：
      tuple — (load_login_accounts 回调函数, [account_dropdown 输出组件列表])，
              供 ticker.py 在页面加载时刷新账号列表。

    调用场景：
      ticker_cmd() 中注册"账号登录"标签页时调用。
    """
    with gr.Column(elem_classes="btb-page-section"):
        with gr.Accordion(
            label="填写当前账号绑定的手机号（可选）",
            open=False,
            elem_classes="btb-card btb-soft-accordion",
        ):
            phone_gate_ui = gr.Textbox(
                label="手机号",
                info="手机验证出现概率较低，可以留空",
                value=util.main_request.cookieManager.get_config_value("phone", ""),
            )

            def input_phone(_phone):
                """
                手机号输入框变更回调。

                核心作用：
                  将用户输入的手机号持久化到 CookieManager 配置中，供后续抢票验证使用。

                输入参数：
                  _phone : str — 用户输入的手机号。

                返回值：无。

                调用场景：
                  login_tab() 中 phone_gate_ui 组件值变化时触发。
                """
                util.main_request.cookieManager.set_config_value("phone", _phone)

            phone_gate_ui.change(fn=input_phone, inputs=phone_gate_ui, outputs=None)

        def generate_qrcode():
            """
            生成 B站登录二维码。

            核心作用：
              调用 B站 passport 接口获取二维码 URL 和 qrcode_key，
              生成 PNG 图片并保存到临时目录。

            返回值：
              tuple[str|None, str] — (图片路径, qrcode_key)；失败返回 (None, 错误消息)。
            """
            headers = {
                "user-agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0"
                ),
            }
            for _ in range(10):
                res = requests.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                    headers=headers,
                    timeout=10,
                )
                res_json = res.json()
                if res_json["code"] == 0:
                    url = res_json["data"]["url"]
                    qrcode_key = res_json["data"]["qrcode_key"]
                    qr = qrcode.QRCode(
                        version=1,
                        error_correction=qrcode.constants.ERROR_CORRECT_H,  # type: ignore
                        box_size=10,
                        border=4,
                    )
                    qr.add_data(url)
                    qr.make(fit=True)
                    path = os.path.join(TEMP_PATH, f"login_qrcode_{qrcode_key}.png")
                    qr.make_image(
                        fill_color="black", back_color="white"
                    ).get_image().save(path)
                    return path, qrcode_key
                time.sleep(1)
            return None, "二维码生成失败"

        def poll_login(qrcode_key):
            """
            轮询 B站二维码登录状态。

            核心作用：
              最多轮询 120 次（约 60 秒），检测扫码结果：
              - code=0：登录成功，返回 cookies。
              - code=86101/86090：等待扫码/已扫码未确认，继续轮询。
              - 其他：返回错误消息。

            输入参数：
              qrcode_key : str — 二维码唯一标识。

            返回值：
              tuple[str, list|None] — (状态消息, cookies 列表或 None)。
            """
            headers = {"User-Agent": "Mozilla/5.0"}
            for _ in range(120):
                res = requests.get(
                    "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                    params={"qrcode_key": qrcode_key},
                    headers=headers,
                    timeout=5,
                )
                poll_res = res.json()
                if poll_res.get("code") != 0:
                    time.sleep(0.5)
                    continue

                code = poll_res["data"]["code"]
                if code == 0:
                    cookies = parse_cookie_list(res.headers["set-cookie"])
                    return "登录成功", cookies
                if code in (86101, 86090):
                    time.sleep(0.5)
                    continue
                return f"扫码失败：{poll_res['data']['message']}", None

            return "登录超时，请重试。", None

        def start_login():
            """
            启动登录流程：生成二维码。

            返回值：
              tuple[str|None, str] — (图片路径, qrcode_key 或错误消息)。
            """
            img_path, qrcode_key = generate_qrcode()
            if not img_path:
                return None, "二维码生成失败"
            return img_path, qrcode_key

        qrcode_key_state = gr.State("")

        def _get_account_choices():
            """
            获取当前账号池的所有选项文本。

            返回值：
              list[str] — 账号选项列表。
            """
            accounts = util.main_request.cookieManager.get_accounts()
            return [_format_account_choice(a.uid, a.name, a.level, a.is_vip) for a in accounts]

        def _get_default_account_choice() -> str | None:
            """
            获取默认选中的账号选项。

            返回值：
              str | None — 默认选项文本。
            """
            return _get_default_account_choice_from(_get_account_choices())

        def _get_default_account_choice_from(choices: list[str]) -> str | None:
            """
            从给定选项列表中解析默认选中项。

            核心作用：
              若当前有活跃 Cookie，则优先选中对应账号。
            """
            active_uid = None
            if util.main_request.cookieManager.have_cookies():
                active_uid = util.main_request.cookieManager.get_cookies_value(
                    "DedeUserID"
                )
            return _resolve_default_account_choice(choices, active_uid=active_uid)

        def load_login_accounts():
            """
            加载账号列表并返回 Gradio update。

            返回值：
              gr.update — 更新 account_dropdown 的 choices 和 value。
            """
            choices = _get_account_choices()
            return gr.update(
                choices=choices,
                value=_get_default_account_choice_from(choices),
            )

        def _activate_account(account) -> None:
            """
            激活指定账号：重建 main_request 并验证登录状态。

            输入参数：
              account : Account — 账号对象。

            核心作用：
              切换全局 main_request 为指定账号，若昵称获取为"未登录"则提示 Cookie 过期。
            """
            set_main_request(BiliRequest(cookies_config_path=GLOBAL_COOKIE_PATH))
            util.main_request.cookieManager.db.insert("cookie", account.cookies)
            name = util.main_request.get_request_name()
            if name == "未登录":
                gr.Warning(
                    f"账号 {account.name} 的 cookies 可能已过期，请重新扫码登录",
                    duration=5,
                )

        with gr.Row(elem_classes="btb-split-grid !items-stretch"):
            with gr.Column(elem_classes="btb-subcard", scale=4):
                qr_img = gr.Image(
                    label="扫我",
                    visible=False,
                    elem_classes="btb-qr-preview",
                )
                login_btn = gr.Button(
                    "点击生成登录二维码",
                    elem_classes="btb-strong-button",
                )
                check_btn = gr.Button(
                    "扫码后点击确认登录",
                    visible=False,
                    elem_classes="btb-soft-button",
                )

            with gr.Column(elem_classes="btb-subcard", scale=6):
                gr.HTML(
                    """
                    <div class="btb-inline-panel">
                        <h4>账号管理</h4>
                    </div>
                    """
                )
                account_choices = _get_account_choices()
                account_dropdown = gr.Dropdown(
                    label="当前账号",
                    choices=account_choices,
                    value=_get_default_account_choice_from(account_choices),
                    interactive=True,
                    allow_custom_value=False,
                    filterable=False,
                )
                with gr.Row(elem_classes="!gap-2"):
                    delete_btn = gr.Button(
                        "删除当前账号",
                        elem_classes="btb-soft-button",
                        variant="stop",
                    )
                    upload_ui = gr.UploadButton(
                        "导入现有登录文件",
                        elem_classes="btb-soft-button",
                    )
                    open_browser_btn = gr.Button(
                        "用Cookie打开B站",
                        elem_classes="btb-soft-button",
                    )
                    refresh_btn = gr.Button(
                        "刷新账号列表",
                        elem_classes="btb-soft-button",
                    )
                    import_people_btn = gr.Button(
                        "从B站导入实名购票人(完整身份证)",
                        elem_classes="btb-soft-button",
                    )
                gr_file_ui = gr.File(
                    label="当前登录信息文件",
                    value=lambda: GLOBAL_COOKIE_PATH,
                )

        def on_login_click():
            """
            "点击生成登录二维码"按钮回调。

            返回值：
              list — [gr.update(value=img_path, visible=True), qrcode_key]。
            """
            img_path, msg_or_key = start_login()
            if img_path:
                gr.Info("已生成二维码，请用 B 站客户端扫码", duration=5)
                return [
                    gr.update(value=img_path, visible=True),
                    msg_or_key,
                ]
            gr.Warning("生成二维码失败", duration=5)
            return [
                gr.update(value="", visible=False),
                "",
            ]

        def on_check_login(key):
            """
            "扫码后点击确认登录"按钮回调。

            核心作用：
              轮询登录状态，成功后将账号添加到账号池并激活。

            输入参数：
              key : str — qrcode_key。

            返回值：
              list — 更新文件 UI、二维码图片、确认按钮、账号下拉框、qrcode_key 状态的 Gradio update 列表。
            """
            if not key:
                return [
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                    gr.update(),
                ]
            msg, cookies = poll_login(key)
            if cookies:
                try:
                    account = util.main_request.cookieManager.add_account(cookies)
                    _activate_account(account)
                    gr.Info(f"已添加并切换至账号 {account.name}", duration=5)
                    new_choices = _get_account_choices()
                    return [
                        gr.update(value=GLOBAL_COOKIE_PATH),
                        gr.update(visible=False),
                        gr.update(visible=False),
                        gr.update(
                            choices=new_choices,
                            value=_get_default_account_choice_from(new_choices),
                        ),
                        gr.update(value=""),
                    ]
                except Exception as exc:
                    logger.exception(exc)
                    gr.Warning(f"添加账号失败: {exc}", duration=5)

            gr.Warning(msg, duration=5)
            return [
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
                gr.update(),
            ]

        def on_dropdown_change(choice):
            """
            账号下拉框切换回调。

            核心作用：
              切换活跃账号，并自动从 B站重新导入实名购票人。

            输入参数：
              choice : str — 选中的账号选项文本。

            返回值：
              list — [gr.update(value=GLOBAL_COOKIE_PATH), gr.update(), people_cards_html]。
            """
            uid = _find_uid_from_choice(choice)
            if not uid:
                return [gr.update(), gr.update(), _render_people_cards_html()]
            account = util.main_request.cookieManager.find_by_uid(uid)
            if account is None:
                gr.Warning(f"未找到账号 {uid}", duration=5)
                return [gr.update(), gr.update(), _render_people_cards_html()]
            _activate_account(account)
            gr.Info(f"已切换到账号 {account.name}，正在刷新实名购票人...", duration=3)
            try:
                _, records = _import_people_from_bili()
                gr.Info(f"已切换到 {account.name}，导入 {len(records)} 位实名购票人", duration=5)
                people_html = _render_people_cards_html(records)
            except gr.Error:
                people_html = _render_people_cards_html()
            return [
                gr.update(value=GLOBAL_COOKIE_PATH),
                gr.update(),
                people_html,
            ]

        def on_delete_account(choice):
            """
            "删除当前账号"按钮回调。

            核心作用：
              删除指定 UID 的账号；若删除的是当前活跃账号，则自动切换到下一个账号。

            输入参数：
              choice : str — 选中的账号选项文本。

            返回值：
              list — [gr.update(value=GLOBAL_COOKIE_PATH), gr.update(choices=..., value=...), gr.update()]。
            """
            uid = _find_uid_from_choice(choice)
            if not uid:
                gr.Warning("请先选择一个账号", duration=5)
                return [gr.update(), gr.update(), gr.update()]
            account = util.main_request.cookieManager.find_by_uid(uid)
            util.main_request.cookieManager.remove_account(uid)
            new_choices = _get_account_choices()

            current_name = util.main_request.get_request_name()
            was_active = account and (
                account.name == current_name or current_name == "未登录"
            )

            if was_active and new_choices:
                first_account = util.main_request.cookieManager.get_accounts()[0]
                _activate_account(first_account)
                gr.Info(
                    f"已删除账号 {account.name if account else uid}，自动切换到 {first_account.name}",
                    duration=5,
                )
                return [
                    gr.update(value=GLOBAL_COOKIE_PATH),
                    gr.update(choices=new_choices, value=new_choices[0]),
                    gr.update(),
                ]
            if was_active:
                set_main_request(BiliRequest(cookies_config_path=GLOBAL_COOKIE_PATH))
                util.main_request.cookieManager.db.delete("cookie")
                gr.Info(
                    f"已删除最后一个账号 {account.name if account else uid}，当前无活跃账号",
                    duration=5,
                )
                return [
                    gr.update(value=GLOBAL_COOKIE_PATH),
                    gr.update(choices=new_choices, value=None),
                    gr.update(),
                ]

            gr.Info(f"已删除账号 {account.name if account else uid}", duration=5)
            return [
                gr.update(),
                gr.update(
                    choices=new_choices,
                    value=_get_default_account_choice_from(new_choices),
                ),
                gr.update(),
            ]

        def on_refresh_accounts():
            """
            "刷新账号列表"按钮回调。

            返回值：
              gr.update — 更新账号下拉框的选项和默认值。
            """
            set_main_request(BiliRequest(cookies_config_path=GLOBAL_COOKIE_PATH))
            new_choices = _get_account_choices()
            gr.Info(f"已刷新账号列表，共 {len(new_choices)} 个账号", duration=3)
            return gr.update(
                choices=new_choices,
                value=_get_default_account_choice_from(new_choices),
            )

        def _import_people_from_bili() -> tuple[str, list[dict]]:
            """
            从 B站接口导入实名购票人并保存到 people.json。

            核心作用：
              调用 /api/ticket/buyer/list?nomask=1 获取完整身份证信息，
              过滤有效记录后写入 util.EXE_PATH/people.json。

            返回值：
              tuple[str, list[dict]] — (文件路径, 购票人记录列表)。

            异常：
              gr.Error — 接口返回错误或没有购票人时抛出。
            """
            url = "https://show.bilibili.com/api/ticket/buyer/list?nomask=1"
            resp = util.main_request.get(url=url).json()
            if resp.get("errno") not in (0, 1) or not resp.get("data"):
                raise gr.Error(
                    f"导入失败：{resp.get('msg') or resp.get('message') or '未知错误'}"
                )
            people_list = resp["data"].get("list") or []
            if not people_list:
                raise gr.Error("未获取到任何实名购票人信息")
            records = []
            for item in people_list:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                personal_id = item.get("personal_id")
                if name and personal_id:
                    records.append({"name": name, "personal_id": personal_id})
            if not records:
                raise gr.Error("接口未返回有效的姓名/身份证信息")
            people_path = os.path.join(util.EXE_PATH, "people.json")
            with open(people_path, "w", encoding="utf-8") as fp:
                json.dump(records, fp, ensure_ascii=False, indent=2)
            return people_path, records

        def on_import_people():
            """
            "从B站导入实名购票人"按钮回调。

            返回值：
              list — [文件路径, people_cards_html]。
            """
            people_path, records = _import_people_from_bili()
            gr.Info(
                f"已从 B 站导入 {len(records)} 位实名购票人，保存到 {people_path}",
                duration=5,
            )
            return people_path, _render_people_cards_html(records)

        login_btn.click(on_login_click, outputs=[qr_img, qrcode_key_state])

        @gr.on(qrcode_key_state.change, inputs=qrcode_key_state, outputs=check_btn)
        def qrcode_key_state_change(key):
            """
            qrcode_key 状态变化时显示/隐藏确认登录按钮。
            """
            return gr.update(visible=bool(key))

        check_btn.click(
            on_check_login,
            inputs=[qrcode_key_state],
            outputs=[
                gr_file_ui,
                qr_img,
                check_btn,
                account_dropdown,
                qrcode_key_state,
            ],
        )
        delete_btn.click(
            on_delete_account,
            inputs=[account_dropdown],
            outputs=[gr_file_ui, account_dropdown, qr_img],
        )
        upload_ui.upload(upload_file, [upload_ui], [gr_file_ui, account_dropdown])
        open_browser_btn.click(fn=_open_bilibili_with_cookies)
        refresh_btn.click(
            on_refresh_accounts,
            inputs=None,
            outputs=[account_dropdown],
        )

        with gr.Column(elem_classes="btb-card btb-card-sky btb-layout-card"):
            gr.HTML(
                """
                <div class="btb-card-head">
                    <div>
                        <h3>实名购票人</h3>
                        <p>从 people.json 读取。</p>
                    </div>
                </div>
                """
            )
            people_cards_html = gr.HTML(value=_render_people_cards_html())

        import_people_btn.click(
            on_import_people,
            inputs=None,
            outputs=[gr.File(visible=False), people_cards_html],
        )

        account_dropdown.change(
            on_dropdown_change,
            inputs=[account_dropdown],
            outputs=[gr_file_ui, account_dropdown, people_cards_html],
        )
    return load_login_accounts, [account_dropdown]


# ---------------------------------------------------------------------------
# setting_tab — 生成配置标签页
# ---------------------------------------------------------------------------

def setting_tab():
    """
    构建"生成配置"标签页。

    核心作用：
      1. 提供活动链接输入框和"获取票务信息"按钮。
      2. 获取成功后展示票档、日期、联系人、收货地址、实名购票人选择器。
      3. 日期变更时按日期刷新票档列表。
      4. "生成配置"按钮组装完整抢票配置并导出 JSON 文件。

    返回值：无（纯 Gradio 组件构建函数）。

    调用场景：
      ticker_cmd() 中注册"生成配置"标签页时调用。
    """
    with gr.Column(elem_classes="btb-page-section"):
        with gr.Column(elem_classes="btb-card btb-card-sky btb-layout-card"):
            gr.HTML(
                """
                <div class="btb-card-head">
                    <div>
                        <h3>票务配置</h3>
                        <p>输入活动链接获取票档，然后依次完成联系人、地址和实名购票人配置。</p>
                    </div>
                </div>
                """
            )
            with gr.Row(elem_classes="btb-action-band !items-end"):
                ticket_id_ui = gr.Dropdown(
                    label="想抢票的活动链接",
                    info="预置：1001701=2026BML，1001653=2026BW；可手动输入其它活动链接。",
                    interactive=True,
                    choices=[
                        "https://show.bilibili.com/platform/detail.html?id=1001701",
                        "https://show.bilibili.com/platform/detail.html?id=1001653",
                    ],
                    value="https://show.bilibili.com/platform/detail.html?id=1001701",
                    allow_custom_value=True,
                    scale=5,
                )
                ticket_id_btn = gr.Button(
                    "获取票务信息",
                    elem_classes="btb-strong-button",
                    scale=1,
                )

            info_ui = gr.HTML(visible=False, elem_classes="btb-ticket-summary")

            with gr.Column(
                visible=False, elem_id="ticket-detail", elem_classes="btb-detail-shell"
            ) as inner:
                with gr.Row():
                    ticket_info_ui = gr.Dropdown(
                        label="选择票档",
                        interactive=True,
                        type="index",
                        allow_custom_value=False,
                        filterable=False,
                    )
                    date_ui = gr.Dropdown(
                        label="选择日期",
                        choices=[],
                        interactive=True,
                        allow_custom_value=False,
                        filterable=False,
                    )

                with gr.Row(elem_classes="btb-split-grid !items-end"):
                    people_buyer_name = gr.Textbox(
                        value=lambda: ConfigDB.get("people_buyer_name") or "SX",
                        label="联系人姓名",
                        placeholder="请输入姓名",
                        interactive=True,
                    )
                    people_buyer_phone = gr.Textbox(
                        value=lambda: ConfigDB.get("people_buyer_phone") or "18888888888",
                        label="联系人电话",
                        placeholder="请输入电话",
                        interactive=True,
                    )
                    address_ui = gr.Dropdown(
                        label="收货地址",
                        interactive=True,
                        type="index",
                        info="请提前在b站手机端填写地址",
                        allow_custom_value=False,
                        filterable=False,
                    )

                people_ui = gr.CheckboxGroup(
                    label="实名购票人",
                    interactive=True,
                    type="index",
                    info="选中几位购票人，就相当于购买几张票。",
                    elem_classes="btb-people-grid",
                )

                with gr.Row(elem_classes="btb-output-band !items-start"):
                    config_btn = gr.Button(
                        "生成配置",
                        elem_classes="btb-strong-button",
                        scale=0,
                    )
                    config_file_ui = gr.File(visible=False, scale=1)

                config_output_ui = gr.JSON(label="生成结果", visible=False)

                config_btn.click(
                    fn=on_submit_all,
                    inputs=[
                        ticket_id_ui,
                        ticket_info_ui,
                        people_ui,
                        people_buyer_name,
                        people_buyer_phone,
                        address_ui,
                    ],
                    outputs=[config_output_ui, config_file_ui],
                )

            ticket_id_btn.click(
                fn=on_submit_ticket_id,
                inputs=ticket_id_ui,
                outputs=[
                    ticket_info_ui,
                    people_ui,
                    address_ui,
                    inner,
                    info_ui,
                    date_ui,
                ],
                show_progress="hidden",
            )

            def on_submit_data(_date):
                """
                日期下拉框变更回调：按所选日期刷新票档列表。

                输入参数：
                  _date : str — 选中的日期字符串。

                返回值：
                  list — [date_ui update, ticket_info_ui update, info_ui update]。
                """
                global ticket_str_list
                global ticket_value
                global is_hot_project
                global project_id
                global project_name

                try:
                    screens = _fetch_screens_by_date_with_fallback(
                        util.main_request, project_id, _date
                    )

                    if not screens:
                        gr.Warning("该日期暂无票务信息。")
                        return [
                            gr.update(choices=sales_dates, value=_date, visible=True),
                            gr.update(choices=[], value=None),
                            gr.update(value="", visible=False),
                        ]

                    ticket_str_list = []
                    ticket_value = []

                    for screen in screens:
                        screen_name = screen["name"]
                        screen_id = screen["id"]
                        express_fee = max(int(screen.get("express_fee", 0) or 0), 0)
                        for ticket in screen["ticket_list"]:
                            ticket_price = int(ticket["price"]) + express_fee
                            ticket["price"] = ticket_price
                            ticket["screen"] = screen_name
                            ticket["screen_id"] = screen_id
                            ticket["is_hot_project"] = is_hot_project
                            ticket_str_list.append(
                                _format_ticket_option(
                                    screen_name,
                                    ticket,
                                    ticket_price,
                                )
                            )
                            ticket_value.append(
                                {"project_id": project_id, "ticket": ticket}
                            )

                    return [
                        gr.update(choices=sales_dates, value=_date, visible=True),
                        gr.update(choices=ticket_str_list, value=None),
                        gr.update(
                            value=_render_ticket_info_html(
                                title="票务信息",
                                badge="日期已更新",
                                lines=[
                                    ("票务 ID", str(project_id)),
                                    ("展会名称", project_name),
                                ],
                                hint="票档列表已按当前日期刷新，请重新确认起售时间。",
                            ),
                            visible=True,
                        ),
                    ]
                except Exception as exc:
                    logger.exception(exc)
                    return [
                        gr.update(),
                        gr.update(),
                        gr.update(value="", visible=False),
                    ]

            date_ui.change(
                fn=on_submit_data,
                inputs=date_ui,
                outputs=[date_ui, ticket_info_ui, info_ui],
            )
