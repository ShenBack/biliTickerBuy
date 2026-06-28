"""
文件整体功能：生成浏览器指纹状态，为 B 站会员购请求提供逼真的浏览器请求头与设备信息。
所属模块：util.request
依赖文件：无项目内业务依赖，仅使用 Python 标准库与 typing。
对外能力：
    1. 提供一组 TypedDict 描述浏览器窗口、显示、navigator、locale、location、
       WebGL、Canvas、Storage 等指纹字段；
    2. 提供 random_hex、build_chrome_user_agent、extract_app_version_from_ua 等工具函数；
    3. 提供 generate_browser_fingerprint_state 生成完整指纹；
    4. 提供 build_headers_from_browser_state 根据指纹构造 HTTP 请求头；
    5. 提供 finalize_device_id 对原始 deviceId 做与旧版对齐的变换。
"""
from __future__ import annotations

import random
import secrets
from typing import Literal, NotRequired, TypedDict
from typing import Any, Mapping


# =========================
# TypedDict 定义
# =========================


class BrowserWindowState(TypedDict):
    """
    浏览器窗口与屏幕状态。

    类设计作用：对应前端 window 与 screen 对象的关键尺寸与位置字段。
    存储属性：scrollX、scrollY、innerWidth、innerHeight、outerWidth、outerHeight、
              screenX、screenY、screenWidth、screenHeight、screenAvailWidth、screenAvailHeight。
    承担业务：为浏览器指纹提供窗口/屏幕维度信息。
    """
    scrollX: int
    scrollY: int
    innerWidth: int
    innerHeight: int
    outerWidth: int
    outerHeight: int
    screenX: int
    screenY: int
    screenWidth: int
    screenHeight: int
    screenAvailWidth: int
    screenAvailHeight: int


class BrowserDisplayState(TypedDict):
    """
    浏览器显示状态。

    类设计作用：对应 window.devicePixelRatio 与 screen 的颜色深度字段。
    存储属性：devicePixelRatio、colorDepth、pixelDepth。
    承担业务：为浏览器指纹提供显示参数。
    """
    devicePixelRatio: float
    colorDepth: int
    pixelDepth: int


class BrowserNavigatorState(TypedDict):
    """
    浏览器 navigator 状态。

    类设计作用：对应 navigator 对象的常见字段，如 UA、平台、语言、硬件并发数等。
    存储属性：userAgent、appCodeName、appName、appVersion、platform、product、
              productSub、vendor、vendorSub、language、languages、cookieEnabled、
              hardwareConcurrency、deviceMemory、maxTouchPoints、webdriver。
    承担业务：为浏览器指纹提供 navigator 维度的信息，用于构造请求头。
    """
    userAgent: str
    appCodeName: str
    appName: str
    appVersion: str
    platform: str
    product: str
    productSub: str
    vendor: str
    vendorSub: str
    language: str
    languages: list[str]
    cookieEnabled: bool
    hardwareConcurrency: int
    deviceMemory: int
    maxTouchPoints: int
    webdriver: bool


class BrowserLocaleState(TypedDict):
    """
    浏览器本地与时区状态。

    类设计作用：对应 Intl.DateTimeFormat 与 Date.getTimezoneOffset 的结果。
    存储属性：locale、timezone、timezoneOffset。
    承担业务：为浏览器指纹提供地域与时区信息。
    """
    locale: str
    timezone: str
    timezoneOffset: int


class BrowserLocationState(TypedDict):
    """
    浏览器 location 与 history 状态。

    类设计作用：对应 window.location 与 history.length。
    存储属性：href、origin、protocol、host、hostname、port、pathname、search、
              hash、hrefLength、historyLength。
    承担业务：为浏览器指纹提供页面地址与历史长度信息。
    """
    href: str
    origin: str
    protocol: str
    host: str
    hostname: str
    port: str
    pathname: str
    search: str
    hash: str
    hrefLength: int
    historyLength: int


class BrowserWebGLState(TypedDict):
    """
    浏览器 WebGL 状态。

    类设计作用：对应 WebGL 上下文参数与 debug renderer info 扩展。
    存储属性：vendor、renderer、unmaskedVendor、unmaskedRenderer。
    承担业务：为浏览器指纹提供 GPU 与渲染器信息。
    """
    vendor: str
    renderer: str
    unmaskedVendor: str
    unmaskedRenderer: str


class BrowserCanvasState(TypedDict):
    """
    浏览器 Canvas 指纹状态。

    类设计作用：对应 Canvas  winding 规则与 x64hash128 指纹。
    存储属性：winding、x64hash128、dataUrlHash（可选）。
    承担业务：为浏览器指纹提供 Canvas 渲染特征。
    """
    winding: Literal["yes", "no"]
    x64hash128: str
    dataUrlHash: NotRequired[str]


class BrowserStorageState(TypedDict):
    """
    浏览器存储状态。

    类设计作用：对应 localStorage、sessionStorage 与 cookies。
    存储属性：localStorage、sessionStorage、cookies。
    承担业务：为浏览器指纹提供存储层快照，构建请求头时可从中读取 cookie。
    """
    localStorage: dict[str, str]
    sessionStorage: dict[str, str]
    cookies: dict[str, str]


class BrowserFingerprintState(TypedDict):
    """
    完整浏览器指纹状态。

    类设计作用：聚合上述所有子状态，作为 generate_browser_fingerprint_state 的返回类型。
    存储属性：window、display、navigator、locale、location、webgl、canvas、storage。
    承担业务：为 BiliRequest 提供一整套自洽的浏览器指纹数据。
    """
    window: BrowserWindowState
    display: BrowserDisplayState
    navigator: BrowserNavigatorState
    locale: BrowserLocaleState
    location: BrowserLocationState
    webgl: BrowserWebGLState
    canvas: BrowserCanvasState
    storage: BrowserStorageState


# =========================
# 工具函数
# =========================


def random_hex(length: int) -> str:
    """
    生成指定长度的 hex 字符串。

    参数：
        length (int)：目标长度，例如 x64hash128 通常为 32。
    返回值：str，指定长度的十六进制字符字符串。
    内部逻辑：使用 secrets.token_hex 生成足够长度后截取。
    调用位置：generate_browser_canvas_state 等需要随机 hex 指纹的场景。
    """
    return secrets.token_hex((length + 1) // 2)[:length]


def build_chrome_user_agent(
    *,
    os_name: Literal["windows", "macos", "linux"] = "windows",
    chrome_major: int | None = None,
) -> str:
    """
    构造一个常见桌面 Chrome User-Agent。

    参数：
        os_name (str)：操作系统，可选 windows/macos/linux，默认 windows。
        chrome_major (int | None)：Chrome 主版本号，为 None 时随机选择。
    返回值：str，完整的 Chrome UA 字符串。
    内部逻辑：
        1. 随机选择或采用传入的 Chrome 主版本号；
        2. 根据 os_name 选择对应的系统平台字符串；
        3. 拼接成标准 Mozilla/5.0 ... Chrome/... Safari/537.36 格式。
    调用位置：generate_browser_navigator_state 中调用。
    """
    if chrome_major is None:
        chrome_major = random.choice([124, 125, 126, 127, 128, 129, 130, 131])

    chrome_version = (
        f"{chrome_major}.0.{random.randint(6000, 6900)}.{random.randint(80, 180)}"
    )

    if os_name == "windows":
        system = "Windows NT 10.0; Win64; x64"
    elif os_name == "macos":
        system = random.choice(
            [
                "Macintosh; Intel Mac OS X 10_15_7",
                "Macintosh; Intel Mac OS X 13_6_1",
                "Macintosh; Intel Mac OS X 14_5_0",
            ]
        )
    else:
        system = "X11; Linux x86_64"

    return (
        f"Mozilla/5.0 ({system}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_version} Safari/537.36"
    )


def extract_app_version_from_ua(user_agent: str) -> str:
    """
    从 User-Agent 中提取 navigator.appVersion 常见值。

    参数：
        user_agent (str)：完整 UA 字符串。
    返回值：str，去掉开头 "Mozilla/" 后的字符串。
    内部逻辑：检测并移除前缀 "Mozilla/"。
    调用位置：generate_browser_navigator_state 中构造 appVersion 字段。
    """
    prefix = "Mozilla/"
    if user_agent.startswith(prefix):
        return user_agent[len(prefix) :]
    return user_agent


# =========================
# Window / Screen
# =========================


def generate_browser_window_state(
    *,
    screen_width: int | None = None,
    screen_height: int | None = None,
    maximized: bool | None = None,
    scroll: bool = False,
    os_name: Literal["windows", "macos", "linux"] = "windows",
) -> BrowserWindowState:
    """
    生成一组自洽的浏览器窗口与屏幕尺寸参数。

    参数：
        screen_width (int | None)：屏幕宽度，为 None 时随机选择。
        screen_height (int | None)：屏幕高度，为 None 时随机选择。
        maximized (bool | None)：是否最大化，为 None 时按概率随机。
        scroll (bool)：是否生成滚动偏移，默认 False。
        os_name (str)：操作系统，影响任务栏保留高度。
    返回值：BrowserWindowState，包含窗口与屏幕各字段的字典。
    内部逻辑：
        1. 若未指定则随机选择常见分辨率；
        2. 根据 os_name 扣除任务栏/菜单栏高度得到可用区域；
        3. 随机决定最大化或窗口化位置；
        4. 计算 inner/outer 尺寸与滚动偏移。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    common_screens = [
        (1920, 1080),
        (2560, 1440),
        (1366, 768),
        (1440, 900),
        (1536, 864),
        (1600, 900),
        (1280, 720),
        (3840, 2160),
    ]

    if screen_width is None or screen_height is None:
        screen_width, screen_height = random.choice(common_screens)

    # Windows / Linux 常见底部任务栏；macOS 常见顶部菜单栏 + Dock
    if os_name == "macos":
        reserved_height = random.choice([64, 74, 84, 96])
    else:
        reserved_height = random.choice([40, 48, 56, 64])

    screen_avail_width = screen_width
    screen_avail_height = max(480, screen_height - reserved_height)

    if maximized is None:
        maximized = random.random() < 0.65

    # 浏览器外框与内容区差值
    chrome_width_delta = random.choice([0, 8, 12, 16])
    chrome_height_delta = random.choice([80, 88, 96, 104, 112, 120])

    if maximized:
        outer_width = screen_avail_width
        outer_height = screen_avail_height
        screen_x = 0
        screen_y = 0
    else:
        outer_width = random.randint(
            int(screen_avail_width * 0.60),
            int(screen_avail_width * 0.90),
        )
        outer_height = random.randint(
            int(screen_avail_height * 0.60),
            int(screen_avail_height * 0.90),
        )

        max_x = max(0, screen_avail_width - outer_width)
        max_y = max(0, screen_avail_height - outer_height)

        screen_x = random.randint(0, max_x)
        screen_y = random.randint(0, max_y)

    inner_width = max(320, outer_width - chrome_width_delta)
    inner_height = max(240, outer_height - chrome_height_delta)

    if scroll:
        scroll_x = random.choice([0, 0, 0, random.randint(1, 200)])
        scroll_y = random.choice([0, random.randint(50, 2000)])
    else:
        scroll_x = 0
        scroll_y = 0

    return {
        "scrollX": scroll_x,
        "scrollY": scroll_y,
        "innerWidth": inner_width,
        "innerHeight": inner_height,
        "outerWidth": outer_width,
        "outerHeight": outer_height,
        "screenX": screen_x,
        "screenY": screen_y,
        "screenWidth": screen_width,
        "screenHeight": screen_height,
        "screenAvailWidth": screen_avail_width,
        "screenAvailHeight": screen_avail_height,
    }


# =========================
# Display
# =========================


def generate_browser_display_state(
    *,
    screen_width: int,
    screen_height: int,
    device_pixel_ratio: float | None = None,
) -> BrowserDisplayState:
    """
    生成浏览器显示相关参数。

    参数：
        screen_width (int)：屏幕宽度。
        screen_height (int)：屏幕高度。
        device_pixel_ratio (float | None)：设备像素比，为 None 时按分辨率随机。
    返回值：BrowserDisplayState，包含 devicePixelRatio、colorDepth、pixelDepth。
    内部逻辑：根据屏幕分辨率选择合理的 devicePixelRatio，colorDepth 常见为 24。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    if device_pixel_ratio is None:
        if screen_width >= 3840:
            device_pixel_ratio = random.choice([1.0, 1.25, 1.5, 2.0])
        elif screen_width >= 2560:
            device_pixel_ratio = random.choice([1.0, 1.25, 1.5])
        else:
            device_pixel_ratio = random.choice([1.0, 1.0, 1.0, 1.25, 1.5])

    return {
        "devicePixelRatio": device_pixel_ratio,
        "colorDepth": random.choice([24, 24, 24, 30]),
        "pixelDepth": 24,
    }


# =========================
# Navigator
# =========================


def generate_browser_navigator_state(
    *,
    os_name: Literal["windows", "macos", "linux"] = "windows",
    locale: str = "zh-CN",
    user_agent: str | None = None,
) -> BrowserNavigatorState:
    """
    生成浏览器 navigator 状态。

    参数：
        os_name (str)：操作系统，影响 platform 与默认 UA。
        locale (str)： locale，影响 languages。
        user_agent (str | None)：自定义 UA，为 None 时随机生成。
    返回值：BrowserNavigatorState，包含 navigator 各字段。
    内部逻辑：
        1. 生成或采用传入的 UA；
        2. 根据 os_name 设置 platform；
        3. 根据 locale 生成 languages 列表；
        4. 随机 hardwareConcurrency 与 deviceMemory；
        5. 固定其他 navigator 字段。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    if user_agent is None:
        user_agent = build_chrome_user_agent(os_name=os_name)

    if os_name == "windows":
        platform = "Win32"
    elif os_name == "macos":
        platform = "MacIntel"
    else:
        platform = "Linux x86_64"

    if locale == "zh-CN":
        languages = random.choice(
            [
                ["zh-CN", "zh"],
                ["zh-CN", "zh", "en"],
                ["zh-CN", "zh", "en-US", "en"],
            ]
        )
    elif locale == "ja-JP":
        languages = random.choice(
            [
                ["ja-JP", "ja"],
                ["ja-JP", "ja", "en-US", "en"],
            ]
        )
    else:
        languages = [locale, locale.split("-")[0], "en-US", "en"]

    hardware_concurrency = random.choice([4, 6, 8, 8, 12, 16])
    device_memory = random.choice([4, 8, 8, 16])

    return {
        "userAgent": user_agent,
        "appCodeName": "Mozilla",
        "appName": "Netscape",
        "appVersion": extract_app_version_from_ua(user_agent),
        "platform": platform,
        "product": "Gecko",
        "productSub": "20030107",
        "vendor": "Google Inc.",
        "vendorSub": "",
        "language": languages[0],
        "languages": languages,
        "cookieEnabled": True,
        "hardwareConcurrency": hardware_concurrency,
        "deviceMemory": device_memory,
        "maxTouchPoints": 0,
        "webdriver": False,
    }


# =========================
# Locale / Timezone
# =========================


def generate_browser_locale_state(
    *,
    locale: str = "zh-CN",
    timezone: str | None = None,
) -> BrowserLocaleState:
    """
    生成浏览器 locale 与时区状态。

    参数：
        locale (str)：语言区域代码，默认 "zh-CN"。
        timezone (str | None)：时区名称，为 None 时根据 locale 选择默认。
    返回值：BrowserLocaleState，包含 locale、timezone、timezoneOffset。
    内部逻辑：
        1. 根据 locale 获取默认时区与偏移；
        2. 若未指定 timezone 则使用默认值；
        3. 根据 timezone 名称查表得到 offset。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    timezone_map = {
        "zh-CN": ("Asia/Shanghai", -480),
        "zh-TW": ("Asia/Taipei", -480),
        "ja-JP": ("Asia/Tokyo", -540),
        "en-US": ("America/Los_Angeles", 480),
        "en-GB": ("Europe/London", 0),
    }

    default_timezone, default_offset = timezone_map.get(locale, ("Asia/Shanghai", -480))

    if timezone is None:
        timezone = default_timezone

    offset_map = {
        "Asia/Shanghai": -480,
        "Asia/Taipei": -480,
        "Asia/Tokyo": -540,
        "America/Los_Angeles": 480,
        "America/New_York": 300,
        "Europe/London": 0,
        "Europe/Berlin": -60,
    }

    timezone_offset = offset_map.get(timezone, default_offset)

    return {
        "locale": locale,
        "timezone": timezone,
        "timezoneOffset": timezone_offset,
    }


# =========================
# Location / History
# =========================


def generate_browser_location_state(
    *,
    href: str | None = None,
    history_length: int | None = None,
) -> BrowserLocationState:
    """
    生成浏览器 location 与 history 状态。

    参数：
        href (str | None)：页面地址，为 None 时随机选择 B 站会员购示例地址。
        history_length (int | None)：历史长度，为 None 时随机。
    返回值：BrowserLocationState，包含 location 各字段与 historyLength。
    内部逻辑：
        1. 若未提供 href 则随机选取示例地址；
        2. 使用 urllib.parse.urlparse 解析 href；
        3. 构造 origin、protocol、host、hostname、port、pathname、search、hash；
        4. 计算 hrefLength 与 historyLength。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    if href is None:
        href = random.choice(
            [
                "https://show.bilibili.com/platform/detail.html?id=1001581",
                "https://show.bilibili.com/platform/detail.html?id=1001581&from=pc",
                "https://show.bilibili.com/platform/home.html",
            ]
        )

    from urllib.parse import urlparse

    parsed = urlparse(href)

    protocol = f"{parsed.scheme}:"
    hostname = parsed.hostname or ""
    port = str(parsed.port or "")
    host = hostname if not port else f"{hostname}:{port}"
    origin = f"{parsed.scheme}://{host}"

    pathname = parsed.path or "/"
    search = f"?{parsed.query}" if parsed.query else ""
    hash_value = f"#{parsed.fragment}" if parsed.fragment else ""

    if history_length is None:
        history_length = random.randint(2, 10)

    return {
        "href": href,
        "origin": origin,
        "protocol": protocol,
        "host": host,
        "hostname": hostname,
        "port": port,
        "pathname": pathname,
        "search": search,
        "hash": hash_value,
        "hrefLength": len(href),
        "historyLength": history_length,
    }


# =========================
# WebGL
# =========================


def generate_browser_webgl_state(
    *,
    os_name: Literal["windows", "macos", "linux"] = "windows",
    gpu_profile: Literal["intel", "nvidia", "amd", "apple", "swiftshader"]
    | None = None,
) -> BrowserWebGLState:
    """
    生成浏览器 WebGL 状态。

    参数：
        os_name (str)：操作系统，影响默认 GPU 类型。
        gpu_profile (str | None)：GPU 厂商配置，为 None 时按 os_name 随机。
    返回值：BrowserWebGLState，包含 vendor、renderer、unmaskedVendor、unmaskedRenderer。
    内部逻辑：
        1. 根据 os_name 随机或采用传入的 gpu_profile；
        2. 按厂商选择对应的 unmaskedVendor 与 unmaskedRenderer；
        3. vendor 与 renderer 固定为常见 WebKit 值。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    if gpu_profile is None:
        if os_name == "macos":
            gpu_profile = random.choice(["apple", "intel"])
        elif os_name == "linux":
            gpu_profile = random.choice(["intel", "nvidia", "amd", "swiftshader"])
        else:
            gpu_profile = random.choice(["intel", "nvidia", "amd"])

    if gpu_profile == "nvidia":
        unmasked_vendor = "Google Inc. (NVIDIA)"
        unmasked_renderer = random.choice(
            [
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                "ANGLE (NVIDIA, NVIDIA GeForce GTX 1660 SUPER Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ]
        )
    elif gpu_profile == "amd":
        unmasked_vendor = "Google Inc. (AMD)"
        unmasked_renderer = random.choice(
            [
                "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                "ANGLE (AMD, AMD Radeon Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ]
        )
    elif gpu_profile == "apple":
        unmasked_vendor = "Google Inc. (Apple)"
        unmasked_renderer = random.choice(
            [
                "ANGLE (Apple, Apple M1, OpenGL 4.1)",
                "ANGLE (Apple, Apple M2, OpenGL 4.1)",
                "ANGLE (Apple, Apple M3, OpenGL 4.1)",
            ]
        )
    elif gpu_profile == "swiftshader":
        unmasked_vendor = "Google Inc. (Google)"
        unmasked_renderer = "ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)"
    else:
        unmasked_vendor = "Google Inc. (Intel)"
        unmasked_renderer = random.choice(
            [
                "ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ]
        )

    return {
        "vendor": "WebKit",
        "renderer": "WebKit WebGL",
        "unmaskedVendor": unmasked_vendor,
        "unmaskedRenderer": unmasked_renderer,
    }


# =========================
# Canvas
# =========================


def generate_browser_canvas_state(
    *,
    x64hash128: str | None = None,
    data_url_hash: str | None = None,
) -> BrowserCanvasState:
    """
    生成浏览器 Canvas 指纹状态。

    参数：
        x64hash128 (str | None)：固定 Canvas 指纹，为 None 时随机生成 32 位 hex。
        data_url_hash (str | None)：可选的 data URL 哈希。
    返回值：BrowserCanvasState，包含 winding、x64hash128、dataUrlHash（可选）。
    内部逻辑：若未提供 x64hash128 则调用 random_hex(32) 生成。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    if x64hash128 is None:
        x64hash128 = random_hex(32)

    result: BrowserCanvasState = {
        "winding": "yes",
        "x64hash128": x64hash128,
    }

    if data_url_hash is not None:
        result["dataUrlHash"] = data_url_hash

    return result


# =========================
# Storage
# =========================


def generate_browser_storage_state(
    *,
    local_storage: dict[str, str] | None = None,
    session_storage: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> BrowserStorageState:
    """
    生成浏览器 storage 状态。

    参数：
        local_storage (dict | None)：localStorage 键值，默认空。
        session_storage (dict | None)：sessionStorage 键值，默认空。
        cookies (dict | None)：cookies 键值，默认空。
    返回值：BrowserStorageState，包含 localStorage、sessionStorage、cookies。
    内部逻辑：将传入字典复制为新 dict，避免后续修改影响默认值。
    调用位置：generate_browser_fingerprint_state 中调用。
    """

    return {
        "localStorage": dict(local_storage or {}),
        "sessionStorage": dict(session_storage or {}),
        "cookies": dict(cookies or {}),
    }


# =========================
# 总入口
# =========================


def generate_browser_fingerprint_state(
    *,
    os_name: Literal["windows", "macos", "linux"] = "windows",
    locale: str = "zh-CN",
    timezone: str | None = None,
    screen_width: int | None = None,
    screen_height: int | None = None,
    maximized: bool | None = None,
    scroll: bool = False,
    href: str | None = None,
    history_length: int | None = None,
    user_agent: str | None = None,
    device_pixel_ratio: float | None = None,
    gpu_profile: Literal["intel", "nvidia", "amd", "apple", "swiftshader"]
    | None = None,
    canvas_hash: str | None = None,
    local_storage: dict[str, str] | None = None,
    session_storage: dict[str, str] | None = None,
    cookies: dict[str, str] | None = None,
) -> BrowserFingerprintState:
    """
    生成完整浏览器指纹状态。

    参数：
        os_name (str)：操作系统，默认 windows。
        locale (str)：语言区域，默认 zh-CN。
        timezone (str | None)：时区名称。
        screen_width (int | None)：屏幕宽度。
        screen_height (int | None)：屏幕高度。
        maximized (bool | None)：是否最大化窗口。
        scroll (bool)：是否生成滚动偏移。
        href (str | None)：页面地址。
        history_length (int | None)：历史长度。
        user_agent (str | None)：自定义 UA。
        device_pixel_ratio (float | None)：设备像素比。
        gpu_profile (str | None)：GPU 厂商配置。
        canvas_hash (str | None)：固定 Canvas 指纹。
        local_storage (dict | None)：localStorage 键值。
        session_storage (dict | None)：sessionStorage 键值。
        cookies (dict | None)：cookies 键值。
    返回值：BrowserFingerprintState，完整指纹状态字典。
    内部逻辑：依次调用各子状态生成函数，并确保 window/display 等字段相互自洽。
    调用位置：BiliRequest 初始化或需要构造请求头时调用。
    """

    window = generate_browser_window_state(
        screen_width=screen_width,
        screen_height=screen_height,
        maximized=maximized,
        scroll=scroll,
        os_name=os_name,
    )

    display = generate_browser_display_state(
        screen_width=window["screenWidth"],
        screen_height=window["screenHeight"],
        device_pixel_ratio=device_pixel_ratio,
    )

    navigator = generate_browser_navigator_state(
        os_name=os_name,
        locale=locale,
        user_agent=user_agent,
    )

    locale_state = generate_browser_locale_state(
        locale=locale,
        timezone=timezone,
    )

    location = generate_browser_location_state(
        href=href,
        history_length=history_length,
    )

    webgl = generate_browser_webgl_state(
        os_name=os_name,
        gpu_profile=gpu_profile,
    )

    canvas = generate_browser_canvas_state(
        x64hash128=canvas_hash,
    )

    storage = generate_browser_storage_state(
        local_storage=local_storage,
        session_storage=session_storage,
        cookies=cookies,
    )
    return {
        "window": window,
        "display": display,
        "navigator": navigator,
        "locale": locale_state,
        "location": location,
        "webgl": webgl,
        "canvas": canvas,
        "storage": storage,
    }


def finalize_device_id(raw_device_id: str) -> str:
    """
    对原始 deviceId 做与旧版算法对齐的变换。

    参数：
        raw_device_id (str)：32 位十六进制字符串。
    返回值：str，变换后的 32 位十六进制字符串。
    内部逻辑：
        1. 校验输入为 32 位 hex；
        2. 通过 calculate_position_or_value 计算替换位置与替换值；
        3. 替换对应位置的字符。
    调用位置：BiliRequest 初始化 deviceId 时调用。
    """
    if not isinstance(raw_device_id, str):
        raise TypeError("raw_device_id must be a string")

    if len(raw_device_id) != 32:
        raise ValueError("raw_device_id must be a 32-character hex string")

    try:
        hex_digits = [int(char, 16) for char in raw_device_id]
    except ValueError as exc:
        raise ValueError("raw_device_id must only contain hex characters") from exc

    def calculate_position_or_value(
        digits: list[int],
        source_index: int,
    ) -> int:
        """
        计算 deviceId 变换中的替换位置或替换值。

        参数：
            digits (list[int])：32 位 hex 对应的整数列表。
            source_index (int)：源索引，通常为最后两位。
        返回值：int，计算得到的位置或值。
        内部逻辑：按旧版 JS 算法 i(e, t) 的等价实现，基于半长与步长计算目标位置。
        调用位置：finalize_device_id 内部计算 replace_index 与 replacement_value。
        """

        total_length = len(digits)
        half_length = total_length // 2

        target_index = source_index - digits[source_index]

        if target_index < half_length:
            target_index = total_length - (half_length - target_index)

        step_count = digits[target_index]
        cursor_index = target_index

        for _ in range(step_count):
            cursor_index -= 1

            if cursor_index < half_length:
                cursor_index = total_length - 1

        return (step_count + digits[cursor_index]) % half_length

    replace_index = calculate_position_or_value(
        digits=hex_digits,
        source_index=len(hex_digits) - 1,
    )

    replacement_value = calculate_position_or_value(
        digits=hex_digits,
        source_index=len(hex_digits) - 2,
    )

    replacement_hex = format(replacement_value, "x")

    return (
        raw_device_id[:replace_index]
        + replacement_hex
        + raw_device_id[replace_index + 1 :]
    )


def _cookie_dict_to_header(cookies: Mapping[str, str] | None) -> str:
    """
    将 cookies 字典转换为 Cookie 请求头字符串。

    参数：
        cookies (Mapping[str, str] | None)：Cookie 键值映射。
    返回值：str，形如 "a=1; b=2" 的 Cookie 字符串，无 Cookie 返回空串。
    内部逻辑：使用 "; " 连接键值对。
    调用位置：build_headers_from_browser_state 中构造 Cookie 头。
    """
    if not cookies:
        return ""
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _build_sec_ch_ua(user_agent: str) -> str:
    """
    根据 User-Agent 粗略生成 sec-ch-ua 头。

    参数：
        user_agent (str)：完整 UA 字符串。
    返回值：str，sec-ch-ua 头值。
    内部逻辑：识别 Edge 或 Chrome，返回对应的品牌版本字符串；无法识别则返回通用 Chromium 值。
    调用位置：build_headers_from_browser_state 中调用。
    """
    if "Edg/" in user_agent:
        return '"Microsoft Edge";v="126", "Chromium";v="126", "Not/A)Brand";v="8"'

    if "Chrome/" in user_agent:
        try:
            major = user_agent.split("Chrome/")[1].split(".")[0]
        except Exception:
            major = "126"
        return (
            f'"Google Chrome";v="{major}", "Chromium";v="{major}", "Not/A)Brand";v="8"'
        )

    return '"Chromium";v="126", "Not/A)Brand";v="8"'


def _build_sec_ch_ua_platform(platform: str) -> str:
    """
    根据 navigator.platform 生成 sec-ch-ua-platform 头。

    参数：
        platform (str)：navigator.platform 值，如 Win32、MacIntel。
    返回值：str，对应平台字符串。
    内部逻辑：匹配 Win32/MacIntel/Linux 返回对应引号字符串，默认 Windows。
    调用位置：build_headers_from_browser_state 中调用。
    """
    if platform == "Win32":
        return '"Windows"'
    if platform == "MacIntel":
        return '"macOS"'
    if platform.startswith("Linux"):
        return '"Linux"'
    return '"Windows"'


def build_headers_from_browser_state(
    state: dict[str, Any] | None = None,
    *,
    base_headers: dict[str, str] | None = None,
    referer: str | None = None,
    content_type: str = "application/x-www-form-urlencoded",
) -> dict[str, str]:
    """
    根据浏览器指纹状态构造 HTTP 请求头。

    参数：
        state (dict | None)：BrowserFingerprintState 字典，为 None 时使用默认值。
        base_headers (dict | None)：用户传入的基础头，优先级更高。
        referer (str | None)：Referer 值，为 None 时从 location.origin 获取。
        content_type (str)：Content-Type，默认 application/x-www-form-urlencoded。
    返回值：dict[str, str]，构造好的请求头字典。
    内部逻辑：
        1. 从 state 提取 navigator、location、storage；
        2. 根据 languages 生成 accept-language；
        3. 根据 UA 生成 sec-ch-ua；
        4. 根据 platform 生成 sec-ch-ua-platform；
        5. 设置常见 fetch 与 Client Hints 字段；
        6. 合并 base_headers（base_headers 优先级最高）。
    调用位置：BiliRequest 初始化时构造 self.headers。
    """

    headers = dict(base_headers or {})

    navigator = (state or {}).get("navigator", {})
    location = (state or {}).get("location", {})
    storage = (state or {}).get("storage", {})

    user_agent = navigator.get(
        "userAgent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36",
    )

    languages = navigator.get("languages")
    if isinstance(languages, list) and languages:
        accept_language = ",".join(
            f"{lang};q={max(1.0 - idx * 0.1, 0.1):.1f}" if idx else lang
            for idx, lang in enumerate(languages)
        )
    else:
        accept_language = "zh-CN,zh;q=0.9,en;q=0.8,en-US;q=0.7"

    platform = navigator.get("platform", "Win32")

    if referer is None:
        referer = location.get("origin") or "https://show.bilibili.com/"

    if not referer.endswith("/"):
        referer = referer + "/"

    cookie_header = _cookie_dict_to_header(storage.get("cookies"))

    default_headers = {
        "accept": "*/*",
        "accept-language": accept_language,
        "content-type": content_type,
        "referer": referer,
        "origin": "https://show.bilibili.com",
        "priority": "u=1, i",
        "user-agent": user_agent,
        # 浏览器常见 fetch 相关字段
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        # Client Hints
        "sec-ch-ua": _build_sec_ch_ua(user_agent),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": _build_sec_ch_ua_platform(platform),
    }

    if cookie_header:
        default_headers["cookie"] = cookie_header

    # 用户传入 base_headers 时优先级更高
    default_headers.update(headers)

    return default_headers
