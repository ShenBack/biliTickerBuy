"""
interface/common.py — 接口层通用工具与常量集合。

文件整体功能：
  1. 定义票务配置校验所需的必填字段常量与状态映射。
  2. 提供 Cookie 存储路径解析、Cookie 列表解析、Cookie 转请求头等功能。
  3. 提供 JSON 文件读取、配置加载、项目 ID 提取、销售状态格式化等通用辅助函数。
  4. 封装统一的 BiliRequest 构造入口 _make_request。

所属模块：接口层 (interface)
依赖文件：
  - util.request.BiliRequest  （由 _make_request 延迟导入）

对外能力：
  本模块主要为 interface.auth / config / execution / project / search 等子模块提供内部支撑，
  不直接对外暴露；其中 _make_request 是各业务接口统一构造 HTTP 请求对象的入口。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests


# 票务配置顶层必填字段：validate_config 会依次检查这些字段是否存在且非空。
REQUIRED_FIELDS = (
    "detail",
    "count",
    "screen_id",
    "project_id",
    "sku_id",
    "pay_money",
    "buyer_info",
    "buyer",
    "tel",
    "deliver_info",
    "cookies",
)

# 购票人信息必填字段。
BUYER_REQUIRED_FIELDS = ("name", "personal_id")

# 收货地址信息必填字段。
DELIVER_REQUIRED_FIELDS = ("name", "tel", "addr_id", "addr")

# Cookie 项必填字段（name/value 缺一不可）。
COOKIE_REQUIRED_FIELDS = ("name", "value")

# B站 sale_flag_number 到中文销售状态的映射，用于 _format_sale_status。
SALES_FLAG_NUMBER_MAP = {
    1: "不可售",
    2: "预售",
    3: "停售",
    4: "售罄",
    5: "不可用",
    6: "库存紧张",
    8: "暂时售罄",
    9: "不在白名单",
    101: "未开始",
    102: "已结束",
    103: "未完成",
    105: "下架",
    106: "已取消",
}


def _load_json_file(path: str | Path) -> Any:
    """
    读取 JSON 文件并反序列化。

    核心作用：
      以 UTF-8 编码打开指定路径文件，返回 json.load 结果。

    输入参数：
      - path : str | Path — 待读取的 JSON 文件路径。

    返回值：
      Any — JSON 反序列化后的 Python 对象。

    调用位置：
      被 _read_tinydb_value、_resolve_cookie_list、_load_config 等函数调用。
    """
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_tinydb_value(path: str | Path, key: str) -> Any:
    """
    从 JSON 文件中按 key 读取配置值（兼容 TinyDB 结构）。

    核心作用：
      1. 读取目标 JSON 文件。
      2. 若文件为 dict 且包含 _default 分组，遍历分组内条目匹配 key。
      3. 若文件为 list，遍历列表匹配 key。
      4. 找到后返回对应 value；未找到或读取失败返回 None。

    输入参数：
      - path : str | Path — 配置文件路径。
      - key  : str — 要读取的配置键名。

    返回值：
      Any — 匹配到的 value；未匹配或异常时返回 None。

    调用位置：
      被 _cookie_store_path 调用，用于从 config.json 中读取 cookies_path 配置。
    """
    target = Path(path)
    if not target.exists():
        return None
    try:
        raw = _load_json_file(target)
    except Exception:
        return None
    if isinstance(raw, dict):
        default_group = raw.get("_default")
        if isinstance(default_group, dict):
            for item in default_group.values():
                if isinstance(item, dict) and item.get("key") == key:
                    return item.get("value")
        if raw.get("key") == key:
            return raw.get("value")
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("key") == key:
                return item.get("value")
    return None


def _cookie_store_path(cookies_path: str | Path | None) -> str | None:
    """
    解析 Cookie 文件存储路径。

    核心作用：
      1. 若显式传入 cookies_path，直接返回其字符串形式。
      2. 否则读取项目根目录 config.json 中 cookies_path 配置项。
      3. 若仍未配置，默认返回项目根目录下的 cookies.json。

    输入参数：
      - cookies_path : str | Path | None — 显式 Cookie 路径。

    返回值：
      str | None — 解析后的 Cookie 文件绝对/相对路径字符串。

    调用位置：
      被 _resolve_cookie_list、get_login_state、poll_qr_login、login_with_cookies 等函数调用。
    """
    if cookies_path is not None:
        return str(Path(cookies_path))

    package_root = Path(__file__).resolve().parents[1]
    config_path = package_root / "config.json"
    configured = _read_tinydb_value(config_path, "cookies_path")
    if configured:
        return str(Path(configured))
    return str(package_root / "cookies.json")


def _coerce_cookie_store(raw: Any) -> list[dict[str, Any]] | None:
    """
    将多种 Cookie 存储格式统一转换为 Cookie 列表。

    核心作用：
      1. 若 raw 为 None，返回 None。
      2. 若 raw 为 list，深拷贝后返回。
      3. 若 raw 为 dict，优先取 raw["cookie"] 列表；否则尝试 TinyDB _default 分组中 key="cookie" 的值。
      4. 其他格式返回 None。

    输入参数：
      - raw : Any — 原始 Cookie 数据（list / dict / None）。

    返回值：
      list[dict[str, Any]] | None — 规范化后的 Cookie 列表或 None。

    调用位置：
      被 _resolve_cookie_list、generate_ticket_config 调用。
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        return copy.deepcopy(raw)
    if isinstance(raw, dict):
        if isinstance(raw.get("cookie"), list):
            return copy.deepcopy(raw["cookie"])
        default_group = raw.get("_default")
        if isinstance(default_group, dict):
            for item in default_group.values():
                if isinstance(item, dict) and item.get("key") == "cookie":
                    value = item.get("value")
                    if isinstance(value, list):
                        return copy.deepcopy(value)
    return None


def _resolve_cookie_list(
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    *,
    cookies_path: str | Path | None = None,
) -> list[dict[str, Any]] | None:
    """
    解析最终使用的 Cookie 列表。

    核心作用：
      1. 若显式传入 cookies，直接通过 _coerce_cookie_store 规范化。
      2. 否则读取 cookies_path 指向的 JSON 文件并解析。
      3. 任何读取异常均返回 None，避免 Cookie 文件损坏导致流程中断。

    输入参数：
      - cookies      : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path : str | Path | None — Cookie 文件路径（为空时使用 _cookie_store_path 默认路径）。

    返回值：
      list[dict[str, Any]] | None — 规范化后的 Cookie 列表。

    调用位置：
      被 get_login_state、login_with_cookies、search_tickets、各 project 接口及 _make_request 调用。
    """
    if cookies is not None:
        return _coerce_cookie_store(cookies)

    store_path = _cookie_store_path(cookies_path)
    if not store_path:
        return None
    try:
        return _coerce_cookie_store(_load_json_file(store_path))
    except Exception:
        return None


def _cookies_to_header(cookies: list[dict[str, Any]] | None) -> str:
    """
    将 Cookie 列表拼接为 HTTP 请求头中的 Cookie 字符串。

    核心作用：
      遍历 Cookie 列表，将每个包含 name 与 value 的条目格式化为 "name=value"，并用 "; " 连接。

    输入参数：
      - cookies : list[dict[str, Any]] | None — Cookie 列表。

    返回值：
      str — 拼接后的 Cookie 字符串；空列表或 None 时返回空字符串。

    调用位置：
      被 _fetch_username_silently、search_tickets 调用。
    """
    if not cookies:
        return ""
    parts: list[str] = []
    for cookie in cookies:
        name = cookie.get("name")
        value = cookie.get("value")
        if name and value is not None:
            parts.append("{0}={1}".format(name, value))
    return "; ".join(parts)


def _fetch_username_silently(
    cookies: list[dict[str, Any]] | None,
    *,
    timeout: float = 10.0,
) -> str:
    """
    静默获取当前 Cookie 对应的 B站用户名。

    核心作用：
      1. 构造请求头并调用 api.bilibili.com/x/web-interface/nav。
      2. 从响应中读取 data.uname，去除首尾空白后返回。
      3. 任何异常或用户名缺失均返回 "Not login"，不向调用方抛错。

    输入参数：
      - cookies : list[dict[str, Any]] | None — 用于构造请求头的 Cookie 列表。
      - timeout : float — 请求超时秒数，默认 10.0。

    返回值：
      str — 登录用户名；未登录或失败时返回 "Not login"。

    调用位置：
      被 get_login_state、login_with_cookies 调用。
    """
    if not cookies:
        return "Not login"
    headers = {
        "accept": "*/*",
        "accept-language": "zh-CN,zh;q=0.9",
        "referer": "https://show.bilibili.com/",
        "user-agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "cookie": _cookies_to_header(cookies),
    }
    try:
        response = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data") or {}
        username = data.get("uname")
        if isinstance(username, str) and username.strip():
            return username.strip()
    except Exception:
        return "Not login"
    return "Not login"


def _deepcopy_dict(data: Any) -> dict[str, Any]:
    """
    深拷贝并校验字典类型。

    核心作用：
      对传入对象进行深拷贝；若非 dict 则抛出 TypeError。

    输入参数：
      - data : Any — 待拷贝对象。

    返回值：
      dict[str, Any] — 深拷贝后的字典。

    异常：
      TypeError — data 不是 dict 时抛出。

    调用位置：
      被 _load_config 调用。
    """
    if isinstance(data, dict):
        return copy.deepcopy(data)
    raise TypeError("config must be a dict or a json file path")


def _load_config(config_or_path: str | Path | dict[str, Any]) -> dict[str, Any]:
    """
    加载票务配置（支持文件路径或字典）。

    核心作用：
      1. 若传入 str/Path，读取 JSON 文件。
      2. 若传入 dict，直接深拷贝。

    输入参数：
      - config_or_path : str | Path | dict[str, Any] — 配置对象或配置文件路径。

    返回值：
      dict[str, Any] — 深拷贝后的配置字典。

    调用位置：
      被 load_ticket_config、validate_config 调用。
    """
    if isinstance(config_or_path, (str, Path)):
        return _deepcopy_dict(_load_json_file(config_or_path))
    return _deepcopy_dict(config_or_path)


def _extract_project_id(project_input: str | int) -> int:
    """
    从项目 ID 或项目 URL 中提取数字项目 ID。

    核心作用：
      1. 若为 int 直接返回。
      2. 若为数字字符串，转换为 int 返回。
      3. 若为 URL，解析 query 参数中的 id。

    输入参数：
      - project_input : str | int — 项目 ID 或项目详情页 URL。

    返回值：
      int — 提取到的项目 ID。

    异常：
      ValueError — 输入为空或无法提取项目 ID 时抛出。

    调用位置：
      被 fetch_project_detail、fetch_ticket_options、fetch_buyers、fetch_purchase_context 调用。
    """
    if isinstance(project_input, int):
        return project_input

    text = str(project_input).strip()
    if not text:
        raise ValueError("project_input is empty")
    if text.isdigit():
        return int(text)

    parsed = urlparse(text)
    query = parse_qs(parsed.query)
    project_ids = query.get("id", [])
    if project_ids and project_ids[0].isdigit():
        return int(project_ids[0])
    raise ValueError("could not extract project id from input")


def _format_sale_status(ticket: dict[str, Any]) -> str:
    """
    格式化票档销售状态为中文可读字符串。

    核心作用：
      1. 若 ticket["sale_flag_number"] 在 SALES_FLAG_NUMBER_MAP 中，返回对应中文。
      2. 否则若 ticket 存在 clickable 字段，根据布尔值返回“可购买/不可购买”。
      3. 其他情况返回“未知状态”。

    输入参数：
      - ticket : dict[str, Any] — 票档字典。

    返回值：
      str — 中文销售状态。

    调用位置：
      被 _build_ticket_option 调用。
    """
    sale_flag_number = ticket.get("sale_flag_number")
    if sale_flag_number in SALES_FLAG_NUMBER_MAP:
        return SALES_FLAG_NUMBER_MAP[sale_flag_number]
    if "clickable" in ticket:
        return "可购买" if ticket.get("clickable") else "不可购买"
    return "未知状态"


def _make_request(
    *,
    cookies: list[dict[str, Any]] | dict[str, Any] | None = None,
    cookies_path: str | Path | None = None,
) -> Any:
    """
    构造统一的 BiliRequest 请求对象。

    核心作用：
      根据传入的 Cookie 或 Cookie 路径创建 BiliRequest 实例，供后续业务接口统一发起请求。

    输入参数：
      - cookies      : list[dict[str, Any]] | dict[str, Any] | None — 显式 Cookie。
      - cookies_path : str | Path | None — Cookie 文件路径（为空时使用默认路径）。

    返回值：
      Any — BiliRequest 实例（延迟导入 util.request.BiliRequest）。

    调用位置：
      被 fetch_project_detail、fetch_ticket_options、fetch_buyers、fetch_addresses、
      fetch_purchase_context、poll_qr_login 调用。
    """
    from util.request.BiliRequest import BiliRequest

    return BiliRequest(
        cookies=cookies,
        cookies_config_path=_cookie_store_path(cookies_path),
    )
