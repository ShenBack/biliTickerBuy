"""
interface/project.py — B站票务项目信息获取与解析模块。

文件整体功能：
  封装与 B站票务 API 的交互，提供项目详情、票档选项、购票人、收货地址等数据的拉取与标准化。
  支持新旧两套项目详情接口的自动降级（先调用新接口 mall-search-items，失败则回退到旧接口 ticket/project/getV2）。
  同时处理联动商品（link goods）合并、票档选项构建、场次按日期筛选等逻辑。

所属模块：接口层 (interface)
依赖文件：
  - interface.common  (_extract_project_id / _format_sale_status / _make_request)

对外能力（主要函数）：
  - fetch_project_payload(request, project_id) → 标准化项目详情字典。
  - fetch_project_detail(project_input, cookies, cookies_path) → 项目详情（含 URL）。
  - fetch_ticket_options(project_input, ...) → 票档选项列表。
  - fetch_buyers(project_input, ...) → 购票人列表。
  - fetch_addresses(cookies, cookies_path) → 收货地址列表。
  - fetch_purchase_context(project_input, ...) → 整合项目、票档、购票人、地址的完整上下文。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .common import _extract_project_id, _format_sale_status, _make_request

NEW_PROJECT_DETAIL_URL = "https://mall.bilibili.com/mall-search-items/items_detail/info"
OLD_PROJECT_DETAIL_URL = "https://show.bilibili.com/api/ticket/project/getV2"


def _normalize_new_project_payload(
    new_payload: dict[str, Any], project_id: int
) -> dict[str, Any]:
    """
    将新接口（mall-search-items）返回的项目详情标准化为统一字典格式。

    核心作用：
      1. 提取/规范化 project_id、screen_list、venue_info、sales_dates 等字段。
      2. 遍历 screen_list 与 ticket_list，填充 project_id、express_fee、screen_name、
         sale_flag_number 等默认值。
      3. 计算项目起止时间（基于 screen_start_times）。
      4. 判断是否为电子票（has_eticket）：若所有场次 express_fee 均为 0 则为电子票。

    输入参数：
      - new_payload : dict[str, Any] — 新接口原始响应的 data 字段。
      - project_id  : int — 外部传入的项目 ID，用于兜底。

    返回值：
      dict[str, Any] — 统一格式的项目详情字典。

    异常：
      RuntimeError — screenList 缺失时抛出。

    调用位置：
      由 _fetch_project_payload_new 在成功获取新接口响应后调用。
    """
    normalized_project_id = int(
        new_payload.get("projectId") or new_payload.get("itemsId") or project_id
    )
    raw_screens = new_payload.get("screenList")
    if not isinstance(raw_screens, list) or not raw_screens:
        raise RuntimeError("new project response missing screenList")

    screens = copy.deepcopy(raw_screens)
    screen_start_times = [
        int(screen.get("start_time", 0))
        for screen in screens
        if isinstance(screen, dict) and str(screen.get("start_time", 0)).isdigit()
    ]
    venue_info = copy.deepcopy(new_payload.get("skuVenueInfo") or {})
    if not isinstance(venue_info, dict):
        venue_info = {}
    venue_info.setdefault("name", "")
    venue_info.setdefault("address_detail", "")
    sales_dates = new_payload.get("salesDates")
    end_time = int(
        new_payload.get("endTime")
        or (max(screen_start_times) if screen_start_times else 0)
    )

    for screen in screens:
        if not isinstance(screen, dict):
            continue
        screen.setdefault("project_id", normalized_project_id)
        screen.setdefault("express_fee", 0)
        for ticket in screen.get("ticket_list", []):
            if not isinstance(ticket, dict):
                continue
            ticket.setdefault("project_id", normalized_project_id)
            ticket.setdefault("screen_name", screen.get("name", ""))
            sale_flag = ticket.get("sale_flag") or {}
            if isinstance(sale_flag, dict):
                ticket.setdefault("sale_flag_number", sale_flag.get("number"))

    return {
        "id": normalized_project_id,
        "name": new_payload.get("projectName", ""),
        "hotProject": bool(new_payload.get("hotProject", False)),
        "has_eticket": not any(
            int(screen.get("express_fee", 0) or 0) > 0
            for screen in screens
            if isinstance(screen, dict)
        ),
        "screen_list": screens,
        "sales_dates": copy.deepcopy(
            sales_dates if isinstance(sales_dates, list) else []
        ),
        "venue_info": venue_info,
        "start_time": min(screen_start_times) if screen_start_times else 0,
        "end_time": end_time,
    }


def _fetch_project_payload_new(*, request: Any, project_id: int) -> dict[str, Any]:
    """
    调用新接口获取项目详情。

    核心作用：
      临时修改请求头 origin/referer 为 mall.bilibili.com，调用 POST 接口，
      响应成功后恢复原始请求头。

    输入参数：
      - request    : Any — 已初始化的请求对象（含 headers）。
      - project_id : int — 项目 ID。

    返回值：
      dict[str, Any] — 标准化后的项目详情。

    异常：
      RuntimeError — 接口返回错误或 data 为空时抛出。

    调用位置：
      由 fetch_project_payload 优先调用。
    """
    request_headers = getattr(request, "headers", None)
    old_headers = {}
    if isinstance(request_headers, dict):
        old_headers = {
            "origin": request_headers.get("origin"),
            "referer": request_headers.get("referer"),
        }
        request_headers.update(
            {
                "origin": "https://mall.bilibili.com",
                "referer": (
                    "https://mall.bilibili.com/neul-next/ticket-renovation/detail.html"
                    "?id={0}&from=pc_ticketlist&noTitleBar=1".format(project_id)
                ),
            }
        )
    try:
        response = request.post(
            url=NEW_PROJECT_DETAIL_URL,
            data={
                "itemsId": project_id,
                "itemsDetailPageType": 3,
            },
            isJson=True,
        ).json()
    finally:
        if isinstance(request_headers, dict):
            for key, value in old_headers.items():
                if value is None:
                    request_headers.pop(key, None)
                else:
                    request_headers[key] = value

    errno = response.get("code", response.get("errno"))
    if response.get("success") is False or errno not in (None, 0):
        raise RuntimeError(
            response.get("message", response.get("msg", "failed to fetch new project"))
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("new project response data is empty")
    return _normalize_new_project_payload(data, project_id)


def _fetch_project_payload_old(*, request: Any, project_id: int) -> dict[str, Any]:
    """
    调用旧接口获取项目详情（降级兜底）。

    核心作用：
      调用 show.bilibili.com/api/ticket/project/getV2 接口获取项目原始 data。

    输入参数：
      - request    : Any — 已初始化的请求对象。
      - project_id : int — 项目 ID。

    返回值：
      dict[str, Any] — 旧接口原始 data 字典。

    异常：
      RuntimeError — 接口返回错误或 data 为空时抛出。

    调用位置：
      由 fetch_project_payload 在新接口失败时作为降级方案调用。
    """
    response = request.get(
        url=(
            "{0}?version=134&id={1}&project_id={1}".format(
                OLD_PROJECT_DETAIL_URL,
                project_id,
            )
        )
    ).json()
    errno = response.get("errno", response.get("code"))
    if errno != 0:
        raise RuntimeError(
            response.get("msg", response.get("message", "failed to fetch project"))
        )
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("old project response data is empty")
    return data


def fetch_project_payload(
    request: Any,
    project_id: int,
) -> dict[str, Any]:
    """
    获取项目详情，自动在新旧接口间降级。

    核心作用：
      优先调用新接口；若失败（异常），则回退到旧接口；若均失败，抛出汇总异常。

    输入参数：
      - request    : Any — 已初始化的请求对象。
      - project_id : int — 项目 ID。

    返回值：
      dict[str, Any] — 标准化项目详情字典（旧接口返回原始 data）。

    异常：
      RuntimeError — 新旧接口均失败时抛出，携带双方错误信息。

    调用位置：
      由 fetch_project_detail、fetch_ticket_options、fetch_buyers、fetch_purchase_context 调用。
    """
    try:
        return _fetch_project_payload_new(request=request, project_id=project_id)
    except Exception as new_error:
        try:
            return _fetch_project_payload_old(request=request, project_id=project_id)
        except Exception as old_error:
            raise RuntimeError(
                "failed to fetch project detail from new and old APIs: "
                "new={0}; old={1}".format(new_error, old_error)
            ) from old_error


def _build_ticket_option(
    *,
    screen: dict[str, Any],
    ticket: dict[str, Any],
    hot_project: bool,
    has_eticket: bool,
) -> dict[str, Any]:
    """
    构建单个票档选项字典。

    核心作用：
      1. 计算含运费的价格（非电子票加上 express_fee）。
      2. 深拷贝 ticket 并补充 screen、screen_id、is_hot_project、project_id、
         sale_status、display 等展示字段。
      3. 若存在 link_id，一并携带。

    输入参数：
      - screen      : dict[str, Any] — 所属场次信息。
      - ticket      : dict[str, Any] — 原始票档信息。
      - hot_project : bool — 是否为热门项目。
      - has_eticket : bool — 是否为电子票（决定运费）。

    返回值：
      dict[str, Any] — 完整票档选项字典，供前端下拉框渲染。

    调用位置：
      由 _fetch_ticket_options 遍历 screen_list 时调用。
    """
    express_fee = 0 if has_eticket else max(int(screen.get("express_fee", 0)), 0)
    price = int(ticket.get("price", 0)) + express_fee
    option = copy.deepcopy(ticket)
    option["price"] = price
    option["screen"] = screen.get("name", "")
    option["screen_id"] = screen.get("id")
    option["is_hot_project"] = hot_project
    option["project_id"] = screen.get("project_id")
    option["sale_status"] = _format_sale_status(ticket)
    option["display"] = (
        "{screen} - {desc} - ￥{price} - {status} - 【起售时间：{sale_start}】".format(
            screen=screen.get("name", ""),
            desc=ticket.get("desc", ""),
            price=price / 100,
            status=option["sale_status"],
            sale_start=ticket.get("sale_start", ""),
        )
    )
    if screen.get("link_id") not in (None, ""):
        option["link_id"] = screen["link_id"]
    return option


def _merge_link_goods(
    *,
    request: Any,
    screen_list: list[dict[str, Any]],
    project_id: int,
) -> list[dict[str, Any]]:
    """
    将联动商品合并到场次列表中。

    核心作用：
      1. 拉取 linkgoods/list 获取联动商品 ID 列表。
      2. 逐个拉取联动商品详情，将其 specs_list 项补充为虚拟场次。
      3. 每个补充项标记 link_id 与 project_id。

    输入参数：
      - request    : Any — 已初始化的请求对象。
      - screen_list: list[dict[str, Any]] — 原始场次列表。
      - project_id : int — 项目 ID。

    返回值：
      list[dict[str, Any]] — 合并后的场次列表（深拷贝）。

    异常处理：
      任何异常均被吞掉，直接返回原始 screen_list 深拷贝，避免联动商品接口故障影响主流程。

    调用位置：
      由 _fetch_ticket_options 在未指定 selected_date 时调用。
    """
    merged = copy.deepcopy(screen_list)
    try:
        good_list = request.get(
            url=(
                "https://show.bilibili.com/api/ticket/linkgoods/list"
                "?project_id={0}&page_type=0".format(project_id)
            )
        ).json()
        good_ids = [item["id"] for item in good_list.get("data", {}).get("list", [])]
        for good_id in good_ids:
            detail = request.get(
                url=(
                    "https://show.bilibili.com/api/ticket/linkgoods/detail"
                    "?link_id={0}".format(good_id)
                )
            ).json()
            good_data = detail.get("data") or {}
            item_id = good_data.get("item_id")
            for item in good_data.get("specs_list", []):
                enriched = copy.deepcopy(item)
                enriched["project_id"] = item_id
                enriched["link_id"] = good_id
                merged.append(enriched)
    except Exception:
        return merged
    return merged


def _fetch_ticket_options(
    *,
    request: Any,
    project_payload: dict[str, Any],
    selected_date: str | None,
) -> list[dict[str, Any]]:
    """
    拉取票档选项列表。

    核心作用：
      - 若指定了 selected_date，调用 infoByDate 接口按日期筛选场次。
      - 否则合并联动商品后遍历所有 screen_list，逐个构建票档选项。

    输入参数：
      - request         : Any — 已初始化的请求对象。
      - project_payload : dict[str, Any] — 标准化后的项目详情。
      - selected_date   : str | None — 指定日期（如 "2026-04-12"）。

    返回值：
      list[dict[str, Any]] — 票档选项列表。

    调用位置：
      由 fetch_ticket_options、fetch_purchase_context 调用。
    """
    hot_project = bool(project_payload.get("hotProject"))
    has_eticket = bool(project_payload.get("has_eticket"))
    project_id = int(project_payload["id"])

    if selected_date:
        date_payload = request.get(
            url=(
                "https://show.bilibili.com/api/ticket/project/infoByDate"
                "?id={0}&date={1}".format(project_id, selected_date)
            )
        ).json()
        screens = date_payload.get("data", {}).get("screen_list", [])
    else:
        screens = _merge_link_goods(
            request=request,
            screen_list=project_payload.get("screen_list", []),
            project_id=project_id,
        )

    options: list[dict[str, Any]] = []
    for screen in screens:
        if "name" not in screen:
            continue
        screen_copy = copy.deepcopy(screen)
        screen_copy["project_id"] = screen_copy.get("project_id", project_id)
        for ticket in screen.get("ticket_list", []):
            options.append(
                _build_ticket_option(
                    screen=screen_copy,
                    ticket=ticket,
                    hot_project=hot_project,
                    has_eticket=has_eticket,
                )
            )
    return options


def fetch_project_detail(
    project_input: str | int,
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    获取项目详情（对外封装）。

    核心作用：
      1. 根据 cookies 或 cookies_path 构建请求对象。
      2. 解析 project_input 提取项目 ID。
      3. 拉取项目详情并补充 project_url。

    输入参数：
      - project_input : str | int — 项目 ID 或项目详情页 URL。
      - cookies       : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path  : str | Path | None — Cookie 文件路径。

    返回值：
      dict[str, Any] — 项目详情字典（含 id, name, screen_list, project_url 等）。

    调用位置：
      由外部项目详情查询入口调用。
    """
    request = _make_request(cookies=cookies, cookies_path=cookies_path)
    project_id = _extract_project_id(project_input)
    payload = fetch_project_payload(request=request, project_id=project_id)
    payload["project_url"] = (
        "https://show.bilibili.com/platform/detail.html?id={0}".format(payload["id"])
    )
    return payload


def fetch_ticket_options(
    project_input: str | int,
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
    selected_date: str | None = None,
) -> dict[str, Any]:
    """
    获取项目票档选项（对外封装）。

    核心作用：
      获取指定项目的所有票档选项，并附带项目基本信息与销售日期。

    输入参数：
      - project_input : str | int — 项目 ID 或项目详情页 URL。
      - cookies       : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path  : str | Path | None — Cookie 文件路径。
      - selected_date : str | None — 指定筛选日期。

    返回值：
      dict[str, Any] — 包含 project_id, project_name, selected_date, sales_dates, ticket_options。

    调用位置：
      由外部票档选择入口、fetch_purchase_context 调用。
    """
    request = _make_request(cookies=cookies, cookies_path=cookies_path)
    project_id = _extract_project_id(project_input)
    project_payload = fetch_project_payload(request=request, project_id=project_id)
    options = _fetch_ticket_options(
        request=request,
        project_payload=project_payload,
        selected_date=selected_date,
    )
    return {
        "project_id": project_payload["id"],
        "project_name": project_payload.get("name", ""),
        "selected_date": selected_date,
        "sales_dates": [
            item["date"] for item in project_payload.get("sales_dates", [])
        ],
        "ticket_options": options,
    }


def fetch_buyers(
    project_input: str | int,
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    获取项目实名购票人列表（对外封装）。

    核心作用：
      调用 ticket/buyer/list 接口获取当前账号下与项目关联的实名购票人列表。

    输入参数：
      - project_input : str | int — 项目 ID 或项目详情页 URL。
      - cookies       : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path  : str | Path | None — Cookie 文件路径。

    返回值：
      dict[str, Any] — 包含 project_id, project_name, buyers。

    调用位置：
      由外部购票人选择入口、fetch_purchase_context 调用。
    """
    request = _make_request(cookies=cookies, cookies_path=cookies_path)
    project_id = _extract_project_id(project_input)
    project_payload = fetch_project_payload(request=request, project_id=project_id)
    buyer_response = request.get(
        url=(
            "https://show.bilibili.com/api/ticket/buyer/list"
            "?is_default&projectId={0}".format(project_payload["id"])
        )
    ).json()
    buyers = buyer_response.get("data", {}).get("list", [])
    return {
        "project_id": project_payload["id"],
        "project_name": project_payload.get("name", ""),
        "buyers": buyers,
    }


def fetch_addresses(
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
) -> dict[str, Any]:
    """
    获取当前账号的收货地址列表（对外封装）。

    核心作用：
      调用 ticket/addr/list 接口获取当前登录账号的收货地址列表。

    输入参数：
      - cookies      : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path : str | Path | None — Cookie 文件路径。

    返回值：
      dict[str, Any] — 包含 addresses 列表。

    调用位置：
      由外部地址选择入口、fetch_purchase_context 调用。
    """
    request = _make_request(cookies=cookies, cookies_path=cookies_path)
    addr_response = request.get(
        url="https://show.bilibili.com/api/ticket/addr/list"
    ).json()
    return {"addresses": addr_response.get("data", {}).get("addr_list", [])}


def fetch_purchase_context(
    project_input: str | int,
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
    selected_date: str | None = None,
    phone: str = "",
) -> dict[str, Any]:
    """
    获取完整购票上下文（对外封装）。

    核心作用：
      一次调用聚合项目详情、票档选项、购票人、收货地址、手机号、Cookie 等全部信息，
      供配置生成页（setting_tab）一键填充使用。

    输入参数：
      - project_input : str | int — 项目 ID 或项目详情页 URL。
      - cookies       : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path  : str | Path | None — Cookie 文件路径。
      - selected_date : str | None — 指定筛选日期。
      - phone         : str — 联系人手机号，为空时尝试从 Cookie 配置读取。

    返回值：
      dict[str, Any] — 包含 project_id, project_name, project_url, username, phone,
                       is_hot_project, has_eticket, sales_dates, selected_date, venue,
                       ticket_options, buyers, addresses, cookies。

    调用位置：
      由 UI 配置生成页、build_ticket_config_from_selection 调用。
    """
    project_id = _extract_project_id(project_input)
    request = _make_request(cookies=cookies, cookies_path=cookies_path)
    project_payload = fetch_project_payload(request=request, project_id=project_id)
    ticket_options = _fetch_ticket_options(
        request=request,
        project_payload=project_payload,
        selected_date=selected_date,
    )

    buyer_response = request.get(
        url=(
            "https://show.bilibili.com/api/ticket/buyer/list"
            "?is_default&projectId={0}".format(project_payload["id"])
        )
    ).json()
    addr_response = request.get(
        url="https://show.bilibili.com/api/ticket/addr/list"
    ).json()

    buyers = buyer_response.get("data", {}).get("list", [])
    addresses = addr_response.get("data", {}).get("addr_list", [])

    return {
        "project_id": project_payload["id"],
        "project_name": project_payload.get("name", ""),
        "project_url": (
            "https://show.bilibili.com/platform/detail.html?id={0}".format(
                project_payload["id"]
            )
        ),
        "username": request.get_request_name(),
        "phone": phone or request.cookieManager.get_config_value("phone", ""),
        "is_hot_project": bool(project_payload.get("hotProject")),
        "has_eticket": bool(project_payload.get("has_eticket")),
        "sales_dates": [
            item["date"] for item in project_payload.get("sales_dates", [])
        ],
        "selected_date": selected_date,
        "venue": project_payload.get("venue_info", {}),
        "ticket_options": ticket_options,
        "buyers": buyers,
        "addresses": addresses,
        "cookies": request.cookieManager.get_cookies(force=True),
    }
