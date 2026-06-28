"""
文件整体功能：管理 B 站账号 Cookie 与多账号信息持久化。
所属模块：util.request
依赖文件：
    - util.Storage.KVDatabase（底层键值数据库）
    - requests（调用 B 站 API 获取用户信息）
对外能力：
    1. 提供 Account 数据类描述 B 站账号信息；
    2. 提供 parse_cookie_list 将 Cookie 字符串解析为列表；
    3. 提供 CookieManager 完成当前账号 Cookie 读写、多账号增删查、
       以及通过 Cookie 拉取 B 站用户信息。
"""
from dataclasses import dataclass
from typing import Optional

import requests

from util.Storage.KVDatabase import KVDatabase


@dataclass
class Account:
    """
    B 站账号信息数据类。

    类设计作用：统一描述一个 B 站账号的关键字段，便于多账号管理与序列化。
    存储属性：
        uid (str)：用户 mid。
        name (str)：用户昵称。
        face (str)：头像 URL。
        cookies (list[dict])：Cookie 列表，每项包含 name 与 value。
        level (int)：账号等级，默认 0。
        is_vip (bool)：是否为大会员，默认 False。
        coins (float)：硬币数，默认 0.0。
    承担业务：作为 CookieManager 多账号列表的存储单元。
    """
    uid: str
    name: str
    face: str
    cookies: list[dict]
    level: int = 0
    is_vip: bool = False
    coins: float = 0.0


def parse_cookie_list(cookie_str: str) -> list:
    """
    将逗号分隔的 Cookie 字符串解析为结构化列表。

    参数：
        cookie_str (str)：原始 Cookie 字符串，可能包含逗号分隔的多个 Cookie，
                          每个 Cookie 内部又可能包含分号分隔的多个键值对。
    返回值：list，包含 {"name": ..., "value": ...} 字典的 Cookie 列表。
    内部逻辑：
        1. 按逗号拆分，但需处理值中也可能含逗号的复杂情况，通过判断等号位置合并；
        2. 提取每个合并项中分号前的键值对；
        3. 按等号拆分 name 与 value 并去除空白。
    调用位置：从浏览器复制整段 Cookie 后解析入库时调用。
    """
    cookies = []
    parts = cookie_str.split(",")

    merged = []
    current = ""
    for part in parts:
        if "=" in part.split(";", 1)[0]:
            if current:
                merged.append(current.strip())
            current = part
        else:
            current += "," + part
    if current:
        merged.append(current.strip())

    for item in merged:
        if ";" in item:
            key_value = item.split(";", 1)[0]
        else:
            key_value = item
        if "=" in key_value:
            key, value = key_value.split("=", 1)
            cookies.append({"name": key.strip(), "value": value.strip()})
    return cookies


class CookieManager:
    """
    Cookie 与多账号管理器。

    类设计作用：基于 KVDatabase 持久化当前账号 Cookie、手机号与账号列表，
                并提供从 Cookie 拉取 B 站用户信息的辅助方法。
    存储属性：
        db (KVDatabase)：底层键值数据库实例。
    类常量：
        _COOKIE_KEY (str)：当前账号 Cookie 的存储键。
        _PHONE_KEY (str)：手机号的存储键。
        _ACCOUNTS_KEY (str)：账号列表的存储键。
    承担业务：
        1. 当前账号 Cookie 的读写与查询；
        2. 多账号的增删改查；
        3. 通过 Cookie 获取 B 站用户资料并保存。
    """

    # 数据库中的键
    _COOKIE_KEY = "cookie"  # 当前账号的 cookie
    _PHONE_KEY = "phone"  # 手机号
    _ACCOUNTS_KEY = "accounts"  # 所有账号列表 List[Account]

    def __init__(self, config_file_path=None, cookies=None):
        """
        初始化 CookieManager。

        参数：
            config_file_path (str | None)：数据库文件路径，为 None 时使用默认路径。
            cookies (list[dict] | None)：初始 Cookie 列表，若提供则直接入库。
        返回值：无。
        内部逻辑：创建 KVDatabase 实例，若传入 cookies 则写入当前账号 Cookie。
        调用位置：BiliRequest 初始化或单独管理账号时调用。
        """
        self.db = KVDatabase(config_file_path)
        if cookies is not None:
            self.db.insert(self._COOKIE_KEY, cookies)

    # ---------- 当前账号 cookie 操作 ----------

    def get_cookies(self, force=False):
        """
        获取当前账号 Cookie。

        参数：
            force (bool)：为 True 时强制返回数据库值，不检查是否存在；
                         为 False 时若不存在则抛出 RuntimeError。
        返回值：list[dict]，当前账号 Cookie 列表。
        内部逻辑：根据 force 标志决定是否校验 Cookie 存在性。
        调用位置：BiliRequest 发起请求前获取 Cookie 时调用。
        """
        if force:
            return self.db.get(self._COOKIE_KEY)
        if not self.db.contains(self._COOKIE_KEY):
            raise RuntimeError("当前未登录，请登录")
        else:
            return self.db.get(self._COOKIE_KEY)

    def have_cookies(self):
        """
        判断当前是否已保存 Cookie。

        参数：无。
        返回值：bool，已保存返回 True，否则 False。
        内部逻辑：调用 db.contains 检查 _COOKIE_KEY。
        调用位置：BiliRequest.get_request_name 等需要判断登录状态的场景。
        """
        return self.db.contains(self._COOKIE_KEY)

    def get_cookies_str(self):
        """
        将当前账号 Cookie 拼接为 "name=value; " 字符串。

        参数：无。
        返回值：str，拼接后的 Cookie 字符串。
        内部逻辑：遍历 Cookie 列表拼接 name=value，末尾带分号空格。
        调用位置：需要以字符串形式设置请求头 Cookie 时调用。
        """
        cookies = self.get_cookies()
        cookies_str = ""
        assert cookies
        for cookie in cookies:
            cookies_str += cookie["name"] + "=" + cookie["value"] + "; "
        return cookies_str

    def get_cookies_value(self, name):
        """
        获取当前账号中指定名称的 Cookie 值。

        参数：
            name (str)：Cookie 名称。
        返回值：str | None，找到返回 value，未找到返回 None。
        内部逻辑：遍历当前 Cookie 列表按 name 匹配。
        调用位置：需要读取特定 Cookie（如 SESSDATA、csrf）时调用。
        """
        cookies = self.get_cookies()
        assert cookies
        for cookie in cookies:
            if cookie["name"] == name:
                return cookie["value"]
        return None

    def get_config_value(self, name, default=None):
        """
        读取指定配置项。

        参数：
            name (str)：配置键名。
            default (Any)：键不存在时返回的默认值。
        返回值：Any，数据库中对应的值或 default。
        内部逻辑：检查键是否存在，存在则返回 db.get，否则返回 default。
        调用位置：读取手机号等其他配置时调用。
        """
        if self.db.contains(name):
            return self.db.get(name)
        else:
            return default

    def set_config_value(self, name, value):
        """
        写入指定配置项。

        参数：
            name (str)：配置键名。
            value (Any)：要写入的值。
        返回值：无。
        内部逻辑：调用 db.insert 覆盖写入。
        调用位置：保存手机号等其他配置时调用。
        """
        self.db.insert(name, value)

    # ---------- 多账号管理 ----------

    def get_accounts(self) -> list[Account]:
        """
        获取所有已保存账号。

        参数：无。
        返回值：list[Account]，账号对象列表；数据异常时返回空列表。
        内部逻辑：从数据库读取原始列表，将字典反序列化为 Account 对象。
        调用位置：账号管理页面加载账号列表、add_account/remove_account 中调用。
        """
        raw = self.db.get(self._ACCOUNTS_KEY)
        if not raw or not isinstance(raw, list):
            return []
        return [Account(**a) for a in raw if isinstance(a, dict)]

    def add_account(self, cookies: list[dict]) -> Account:
        """
        添加或更新一个账号。

        参数：
            cookies (list[dict])：该账号的 Cookie 列表。
        返回值：Account，添加或更新后的账号对象。
        内部逻辑：
            1. 调用 B 站 API 获取用户信息；
            2. 创建 Account 对象；
            3. 按 uid 去重后保存完整账号列表。
        调用位置：扫码登录或导入 Cookie 后调用。
        """
        user_info = self._fetch_user_info(cookies)
        account = Account(
            uid=user_info["uid"],
            name=user_info["name"],
            face=user_info["face"],
            cookies=cookies,
            level=user_info["level"],
            is_vip=user_info["is_vip"],
            coins=user_info["coins"],
        )

        accounts = [a for a in self.get_accounts() if a.uid != account.uid]
        accounts.append(account)
        self._save_accounts(accounts)

        return account

    def remove_account(self, uid: str) -> None:
        """
        删除指定 uid 的账号。

        参数：
            uid (str)：要删除的账号 uid。
        返回值：无。
        内部逻辑：过滤掉目标 uid 后重新保存账号列表。
        调用位置：账号管理页面点击删除时调用。
        """
        accounts = [a for a in self.get_accounts() if a.uid != uid]
        self._save_accounts(accounts)

    def find_by_uid(self, uid: str) -> Optional[Account]:
        """
        根据 uid 查找账号。

        参数：
            uid (str)：目标账号 uid。
        返回值：Account | None，找到返回账号对象，否则 None。
        内部逻辑：遍历 get_accounts 结果按 uid 匹配。
        调用位置：切换当前账号时查找对应账号信息。
        """
        for a in self.get_accounts():
            if a.uid == uid:
                return a
        return None

    def _save_accounts(self, accounts: list[Account]) -> None:
        """
        将账号列表持久化到数据库。

        参数：
            accounts (list[Account])：要保存的账号列表。
        返回值：无。
        内部逻辑：将每个 Account 转为 __dict__ 后调用 db.insert。
        调用位置：add_account、remove_account 内部调用。
        """
        self.db.insert(self._ACCOUNTS_KEY, [a.__dict__ for a in accounts])

    @staticmethod
    def _fetch_user_info(cookies: list[dict]) -> dict:
        """
        使用 Cookie 调用 B 站 API 获取用户信息。

        参数：
            cookies (list[dict])：账号 Cookie 列表。
        返回值：dict，包含 uid、name、face、level、is_vip、coins 的字典。
        内部逻辑：
            1. 将 Cookie 列表拼接为请求头 Cookie 字符串；
            2. 请求 https://api.bilibili.com/x/web-interface/nav；
            3. 解析响应 data 字段并返回标准字段。
        调用位置：add_account 中添加新账号时调用。
        """
        cookies_str = ""
        for cookie in cookies:
            cookies_str += f"{cookie['name']}={cookie['value']}; "

        headers = {
            "accept": "*/*",
            "accept-language": "zh-CN,zh;q=0.9",
            "referer": "https://show.bilibili.com/",
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "cookie": cookies_str.strip(),
        }

        resp = requests.get(
            "https://api.bilibili.com/x/web-interface/nav",
            headers=headers,
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {}) or {}

        return {
            "uid": str(data.get("mid", "")),
            "name": str(data.get("uname", "") or ""),
            "face": str(data.get("face", "") or ""),
            "level": data.get("level_info", {}).get("current_level", 0) or 0,
            "is_vip": data.get("vipStatus", 0) == 1,
            "coins": float(data.get("money", 0.0) or 0.0),
        }
