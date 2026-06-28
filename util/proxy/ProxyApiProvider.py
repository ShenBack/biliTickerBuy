"""
文件整体功能：代理 API 提供商接入，负责从代理 API 获取、解析并规范化代理列表。
所属模块：util.proxy
依赖文件：无项目内业务依赖。
对外能力：
    1. 提供 ProxyApiError 异常类，用于代理 API 相关错误；
    2. 提供 ProxyApiResult 数据类，封装解析后的代理列表与原始响应；
    3. 提供 build_proxy_api_url 构造标准化代理 API 请求 URL；
    4. 提供 parse_proxy_api_response 解析多种常见 JSON 结构；
    5. 提供 fetch_proxy_api 完成 HTTP 请求并返回 ProxyApiResult。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests


class ProxyApiError(RuntimeError):
    """
    代理 API 操作异常。

    类设计作用：在代理 API 地址缺失、返回失败或解析不到代理时抛出统一异常，
                便于上层 ProxyManager 等模块捕获并提示用户。
    存储属性：无额外属性，继承 RuntimeError 的 message。
    承担业务：统一代理 API 错误语义。
    """
    pass


@dataclass(frozen=True)
class ProxyApiResult:
    """
    代理 API 调用结果。

    类设计作用：将代理 API 返回的原始响应与解析后的代理列表一起封装，
                方便调用方同时拿到可用代理与调试信息。
    存储属性：
        proxies (list[str])：解析后的代理地址列表，形如 http://host:port。
        response (dict[str, Any])：代理 API 返回的原始 JSON 字典。
    承担业务：作为 fetch_proxy_api 的返回值向上层传递。
    """
    proxies: list[str]
    response: dict[str, Any]


def normalize_proxy_api_protocol(protocol: str | None) -> str:
    """
    规范化代理 API 协议参数。

    参数：
        protocol (str | None)：原始协议字符串，如 "http"、"socks"、"socks5"。
    返回值：str，规范化后的协议，仅返回 "socks5" 或 "http"。
    内部逻辑：将输入转为小写并去除空白，socks/socks5 统一为 socks5，其余为 http。
    调用位置：build_proxy_api_url、parse_proxy_api_response 中调用。
    """
    text = str(protocol or "http").strip().lower()
    if text in {"socks", "socks5"}:
        return "socks5"
    return "http"


def build_proxy_api_url(api_url: str, *, count: int, protocol: str) -> str:
    """
    构造标准化代理 API 请求 URL。

    参数：
        api_url (str)：用户填写的代理 API 地址。
        count (int)：希望获取的代理数量。
        protocol (str)：期望协议，如 http 或 socks5。
    返回值：str，拼接 count、format=json、protocol 参数后的完整 URL。
    内部逻辑：
        1. 校验 api_url 非空；
        2. 解析 URL 查询参数；
        3. 强制设置 count、format、protocol；
        4. 重新组装 URL。
    调用位置：fetch_proxy_api 在发起请求前调用。
    """
    target = str(api_url or "").strip()
    if not target:
        raise ProxyApiError("请先填写代理 API 地址")

    parts = urlsplit(target)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["count"] = str(max(1, int(count)))
    query["format"] = "json"
    query["protocol"] = normalize_proxy_api_protocol(protocol)
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(query, doseq=True),
            parts.fragment,
        )
    )


def _iter_proxy_items(payload: Any) -> list[Any]:
    """
    从代理 API 返回的 JSON 中迭代提取代理条目列表。

    参数：
        payload (Any)：代理 API 返回的解析后数据，可能是 dict 或 list。
    返回值：list[Any]，候选代理条目列表，每个元素可能是 dict 或 str。
    内部逻辑：
        1. 若 payload 为 dict，优先取 data 字段；
        2. 在 data 或 payload 中查找 proxy_list/list/proxies/items 等常见键；
        3. 若 data 本身含 ip/host/port/proxy 等字段，视为单条代理；
        4. 若 payload 为 list，直接返回。
    调用位置：parse_proxy_api_response 中调用。
    """
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("proxy_list", "list", "proxies", "items"):
                value = data.get(key)
                if isinstance(value, list):
                    return value
            if any(key in data for key in ("ip", "host", "port", "proxy")):
                return [data]
        elif isinstance(data, list):
            return data

        for key in ("proxy_list", "list", "proxies", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
    if isinstance(payload, list):
        return payload
    return []


def _extract_host_port(item: Any) -> tuple[str, str] | None:
    """
    从单条代理条目中提取主机与端口。

    参数：
        item (Any)：候选代理条目，可能是 dict 或 str。
    返回值：tuple[str, str] | None，成功返回 (host, port)，失败返回 None。
    内部逻辑：
        1. dict 类型优先取 proxy/addr/address，否则取 ip 与 port；
        2. str 类型去掉协议头与认证信息，按最后冒号拆分 host 与 port。
    调用位置：parse_proxy_api_response 中调用。
    """
    if isinstance(item, dict):
        proxy_value = item.get("proxy") or item.get("addr") or item.get("address")
        if proxy_value:
            return _extract_host_port(str(proxy_value))

        host = item.get("ip") or item.get("host")
        port = item.get("port")
        if host and port:
            return str(host).strip(), str(port).strip()
        return None

    text = str(item or "").strip()
    if not text:
        return None
    text = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", text)
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    if ":" not in text:
        return None
    host, port = text.rsplit(":", 1)
    return host.strip(), port.strip()


def parse_proxy_api_response(payload: dict[str, Any], *, protocol: str) -> list[str]:
    """
    解析代理 API 返回的 JSON，提取可用代理地址。

    参数：
        payload (dict[str, Any])：代理 API 返回的 JSON 字典。
        protocol (str)：期望协议，用于生成代理 URL 的 scheme。
    返回值：list[str]，去重后的代理地址列表，形如 http://host:port。
    内部逻辑：
        1. 检查 code/success 判断 API 是否返回成功；
        2. 通过 _iter_proxy_items 提取候选条目；
        3. 使用 _extract_host_port 解析 host 与 port；
        4. 校验 port 为数字并去重；
        5. 无可用代理时抛出 ProxyApiError。
    调用位置：fetch_proxy_api 中调用，也可由上层单独用于本地调试响应。
    """
    code = payload.get("code", payload.get("errno", 0))
    success = payload.get("success")
    if success is False or str(code) not in {"0", "200", "None"}:
        message = payload.get("msg") or payload.get("message") or payload
        raise ProxyApiError(f"代理 API 返回失败: {message}")

    scheme = "socks" if normalize_proxy_api_protocol(protocol) == "socks5" else "http"
    proxies: list[str] = []
    seen: set[str] = set()
    for item in _iter_proxy_items(payload):
        host_port = _extract_host_port(item)
        if not host_port:
            continue
        host, port = host_port
        if not host or not port.isdigit():
            continue
        proxy = f"{scheme}://{host}:{port}"
        key = proxy.lower()
        if key in seen:
            continue
        seen.add(key)
        proxies.append(proxy)

    if not proxies:
        raise ProxyApiError("代理 API 返回成功，但没有解析到代理 IP 和端口")
    return proxies


def fetch_proxy_api(
    api_url: str,
    *,
    count: int,
    protocol: str,
    timeout: int = 15,
) -> ProxyApiResult:
    """
    向代理 API 发起请求并解析代理列表。

    参数：
        api_url (str)：代理 API 地址。
        count (int)：希望获取的代理数量。
        protocol (str)：期望协议，如 http 或 socks5。
        timeout (int)：请求超时秒数，默认 15。
    返回值：ProxyApiResult，包含解析后的代理列表与原始响应。
    内部逻辑：
        1. 调用 build_proxy_api_url 构造请求 URL；
        2. 使用 GET 请求获取 JSON；
        3. 调用 parse_proxy_api_response 解析代理；
        4. 封装为 ProxyApiResult 返回。
    调用位置：上层需要动态刷新代理池时调用，如配置页面点击“获取代理”。
    """
    request_url = build_proxy_api_url(api_url, count=count, protocol=protocol)
    response = requests.request(
        "GET", request_url, headers={}, data={}, timeout=timeout
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ProxyApiError("代理 API 未返回 JSON 对象")
    return ProxyApiResult(
        proxies=parse_proxy_api_response(payload, protocol=protocol),
        response=payload,
    )
