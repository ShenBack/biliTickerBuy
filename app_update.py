"""
文件说明：
- 文件整体功能：从 GitHub Releases API 拉取项目发布信息，根据当前版本号和用户选择的更新频道
  （稳定版/测试版）判断是否存在可用更新，并返回新版本详情。
- 所属模块：应用顶层更新检查模块，供 UI 在启动或用户手动检查更新时调用。
- 依赖文件：依赖 app_version 获取当前版本；依赖第三方库 requests、packaging.version；
  依赖标准库 dataclasses、typing。
- 对外能力：对外暴露 UpdateError 异常类、ReleaseInfo 数据类、normalize_version、
  select_update、fetch_update 等函数，用于发现与描述可用更新。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import requests
from packaging.version import InvalidVersion, Version

# GitHub 仓库地址，用于拼接 releases API
GITHUB_REPOSITORY = "mikumifa/biliTickerBuy"
# GitHub Releases API 完整 URL
GITHUB_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases"
# 稳定版频道标识
UPDATE_CHANNEL_STABLE = "稳定版"
# 测试版频道标识
UPDATE_CHANNEL_PRERELEASE = "测试版"
# 支持的更新频道元组
UPDATE_CHANNELS = (UPDATE_CHANNEL_STABLE, UPDATE_CHANNEL_PRERELEASE)
# HTTP 请求超时时间（连接超时秒数, 读取超时秒数）
REQUEST_TIMEOUT = (5, 20)


class UpdateError(RuntimeError):
    """
    更新检查异常。

    类设计作用：在无法安全地发现或解析可用更新时抛出，统一上层错误处理。
    存储属性：继承 RuntimeError，仅通过消息文本携带错误原因。
    整体承担业务：作为 fetch_update、select_update、normalize_version 等函数的错误出口。
    """


@dataclass(frozen=True)
class ReleaseInfo:
    """
    GitHub Release 信息数据类。

    类设计作用：不可变地封装一次 GitHub Release 的关键元数据，便于上层展示和序列化。
    存储属性：
        version (str)：版本号字符串；
        tag_name (str)：GitHub 标签名；
        name (str)：Release 名称；
        html_url (str)：Release 页面链接；
        body (str)：Release 说明正文；
        prerelease (bool)：是否为预发布版本；
        published_at (str)：发布时间 ISO 字符串；
        assets (tuple[dict[str, Any], ...])：资源文件信息元组，每个元素包含 name、
            browser_download_url、size。
    整体承担业务：描述一次可更新的目标版本，支持字典序列化与反序列化。
    """

    version: str
    tag_name: str
    name: str
    html_url: str
    body: str
    prerelease: bool
    published_at: str
    assets: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        """
        将 ReleaseInfo 转换为普通字典。

        核心作用：便于将版本信息序列化为 JSON 供 UI 或配置文件使用。
        输入参数：无。
        返回值 (dict[str, Any])：包含与字段同名的键值对的字典，assets 字段被转换为列表。
        内部关键执行逻辑：
            1. 使用 asdict 将 dataclass 转为字典；
            2. 将 assets 元组转换为列表以兼容 JSON 序列化。
        调用位置：由上层需要将版本信息保存或传输给前端的代码调用。
        """
        data = asdict(self)
        data["assets"] = list(self.assets)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReleaseInfo":
        """
        从普通字典构造 ReleaseInfo 实例。

        核心作用：支持从 JSON/配置文件恢复版本信息对象。
        输入参数：
            data (dict[str, Any])：包含版本信息的字典，字段名需与 ReleaseInfo 对应。
        返回值 (ReleaseInfo)：构造好的 ReleaseInfo 实例。
        内部关键执行逻辑：逐个读取字典字段并强转为对应类型，assets 转为元组。
        调用位置：由上层从缓存或配置文件读取版本信息时调用。
        """
        return cls(
            version=str(data["version"]),
            tag_name=str(data["tag_name"]),
            name=str(data.get("name") or data["tag_name"]),
            html_url=str(data["html_url"]),
            body=str(data.get("body") or ""),
            prerelease=bool(data.get("prerelease")),
            published_at=str(data.get("published_at") or ""),
            assets=tuple(data.get("assets") or ()),
        )


def normalize_version(value: str) -> Version:
    """
    将版本号字符串规范化为 packaging.version.Version 对象。

    核心作用：统一处理带 "v" 前缀或裸数字的版本字符串，供版本比较使用。
    输入参数：
        value (str)：待解析的版本号字符串，例如 "v2.15.4" 或 "2.15.4"。
    返回值 (Version)：packaging 库的版本对象，支持大小比较。
    内部关键执行逻辑：
        1. 去除首尾空白；
        2. 若以 "v" 或 "V" 开头则去掉前缀；
        3. 使用 packaging.version.Version 解析；
        4. 解析失败时抛出 UpdateError 并携带原异常上下文。
    调用位置：由 select_update 在处理每个 release 的 tag_name 以及当前版本时调用。
    """
    candidate = value.strip()
    if candidate.lower().startswith("v"):
        candidate = candidate[1:]
    try:
        return Version(candidate)
    except InvalidVersion as exc:
        raise UpdateError(f"无法识别版本号：{value}") from exc


def select_update(
    releases: Iterable[dict[str, Any]], current_version: str, channel: str
) -> ReleaseInfo | None:
    """
    从 GitHub Releases 列表中筛选出符合更新频道的最新可用版本。

    核心作用：根据当前版本和频道（稳定版/测试版）判断是否存在更高版本，并封装为 ReleaseInfo。
    输入参数：
        releases (Iterable[dict[str, Any]])：GitHub Releases API 返回的 release 字典列表。
        current_version (str)：当前应用版本号，例如 "2.15.4"。
        channel (str)：更新频道，必须是 UPDATE_CHANNEL_STABLE 或 UPDATE_CHANNEL_PRERELEASE。
    返回值 (ReleaseInfo | None)：存在可用更新时返回最新版本的 ReleaseInfo；否则返回 None。
    内部关键执行逻辑：
        1. 校验 channel 合法性；
        2. 将 current_version 规范化为 Version；
        3. 遍历 releases，跳过草稿 release；
        4. 解析每个 release 的 tag_name，版本号无效则跳过；
        5. 若频道为稳定版则跳过 prerelease 或版本号本身含预发布标记的 release；
        6. 保留版本号高于 current_version 的候选；
        7. 取版本号最大的候选，将其 assets 过滤为包含名称和下载链接的有效资源；
        8. 构造并返回 ReleaseInfo。
    调用位置：由 fetch_update 在拉取到 GitHub Releases 列表后调用。
    """
    if channel not in UPDATE_CHANNELS:
        raise UpdateError(f"未知更新频道：{channel}")

    current = normalize_version(current_version)
    candidates: list[tuple[Version, dict[str, Any]]] = []
    for release in releases:
        if release.get("draft"):
            continue
        try:
            release_version = normalize_version(str(release.get("tag_name", "")))
        except UpdateError:
            continue
        is_prerelease = bool(release.get("prerelease")) or release_version.is_prerelease
        if channel == UPDATE_CHANNEL_STABLE and is_prerelease:
            continue
        if release_version > current:
            candidates.append((release_version, release))

    if not candidates:
        return None

    version, release = max(candidates, key=lambda item: item[0])
    assets = tuple(
        {
            "name": str(asset.get("name") or ""),
            "browser_download_url": str(asset.get("browser_download_url") or ""),
            "size": int(asset.get("size") or 0),
        }
        for asset in release.get("assets") or ()
        if asset.get("name") and asset.get("browser_download_url")
    )
    return ReleaseInfo(
        version=str(version),
        tag_name=str(release.get("tag_name") or version),
        name=str(release.get("name") or release.get("tag_name") or version),
        html_url=str(release.get("html_url") or ""),
        body=str(release.get("body") or ""),
        prerelease=bool(release.get("prerelease")) or version.is_prerelease,
        published_at=str(release.get("published_at") or ""),
        assets=assets,
    )


def fetch_update(
    current_version: str,
    channel: str,
    *,
    session: requests.Session | None = None,
) -> ReleaseInfo | None:
    """
    从 GitHub 拉取 Releases 并判断是否存在可用更新。

    核心作用：作为更新检查的入口函数，封装 HTTP 请求、响应校验与版本选择逻辑。
    输入参数：
        current_version (str)：当前应用版本号。
        channel (str)：更新频道，稳定版或测试版。
        session (requests.Session | None, 可选)：可复用的 requests 会话对象；
            未提供时新建 Session。
    返回值 (ReleaseInfo | None)：存在可用更新时返回 ReleaseInfo；否则返回 None。
    内部关键执行逻辑：
        1. 使用传入或新建的 requests.Session；
        2. 设置 GitHub API 需要的 Accept、User-Agent、X-GitHub-Api-Version 请求头；
        3. 以 REQUEST_TIMEOUT 超时时间请求 GITHUB_RELEASES_API；
        4. 检查响应状态码，失败时由 raise_for_status 抛出异常；
        5. 校验响应体为列表，否则抛出 UpdateError；
        6. 调用 select_update 筛选并返回可用更新。
    调用位置：由 UI 在启动时或用户点击检查更新按钮时调用。
    """
    client = session or requests.Session()
    response = client.get(
        GITHUB_RELEASES_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"biliTickerBuy/{current_version}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise UpdateError("GitHub 返回了无法识别的版本列表。")
    return select_update(payload, current_version, channel)
