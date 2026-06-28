"""
文件整体功能：基于 TinyDB 的键值对数据库封装，支持文件格式兼容与修复。
所属模块：util.Storage
依赖文件：无外部业务依赖，使用 tinydb 第三方库。
对外能力：提供 KVDatabase 类，以 key-value 形式持久化配置、cookie、账号等数据，
          并兼容旧版非 TinyDB 格式的 config.json。
"""

import json
import os
import shutil
from threading import RLock
from typing import Any, Optional

from tinydb import TinyDB, Query
from tinydb.storages import MemoryStorage, JSONStorage


def _ensure_valid_tinydb_file(path: str) -> None:
    """
    检查 TinyDB JSON 文件是否有效，无效则备份并重建。

    参数：
        path (str)：待检查的 JSON 文件路径。
    返回值：无。
    内部逻辑：
        1. 文件不存在则直接返回，由 TinyDB 自行创建；
        2. 若 JSON 损坏或不是 dict，则备份原文件并写入 {"_default": {}}；
        3. 若已含合法的 _default 表则跳过；
        4. 若为旧版平铺 dict，则将其转换为 TinyDB 文档格式并备份原文件。
    调用位置：KVDatabase.__init__ 初始化文件数据库前调用。
    """
    if not os.path.isfile(path):
        return  # 文件不存在，TinyDB 会自己创建

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        # 损坏的 JSON → 备份并重建
        backup = path + ".bak"
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        # 写一个空的 TinyDB 数据库
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_default": {}}, f)
        return

    # 不是 dict（例如是 list 或 bare string）→ 备份并重建
    if not isinstance(data, dict):
        backup = path + ".bak"
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_default": {}}, f)
        return

    # 已有 _default 表且内容有效 → 直接通过
    if "_default" in data and isinstance(data["_default"], dict):
        # 验证文档 ID 都是有效的整数，防止损坏的 ID（如 "bad"）导致 TinyDB 后续崩溃
        if all(
            isinstance(doc_id, str) and doc_id.isdigit() for doc_id in data["_default"]
        ):
            return

    # 平铺的旧版配置（flat dict，没有 _default 表）→ 迁移
    if not data:
        # 空 dict → 转为空的 TinyDB 格式
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"_default": {}}, f)
        return

    # 尝试把平铺键值对转换为 TinyDB 文档
    docs = {}
    doc_id = 1
    for key, value in data.items():
        if isinstance(key, str):
            docs[str(doc_id)] = {"key": key, "value": value}
            doc_id += 1

    backup = path + ".bak"
    try:
        shutil.copy2(path, backup)
    except OSError:
        pass

    with open(path, "w", encoding="utf-8") as f:
        json.dump({"_default": docs}, f, ensure_ascii=False, indent=2)


class KVDatabase:
    """
    键值对数据库封装类。

    类设计作用：为项目提供简单持久化的 key-value 存储，屏蔽 TinyDB 底层细节，
                同时兼容旧版非 TinyDB 配置文件格式。
    存储属性：
        db (TinyDB)：底层 TinyDB 实例，可能是 JSONStorage 或 MemoryStorage。
        KeyValue (Query)：TinyDB 查询对象，用于按 key 查找文档。
    承担业务：保存配置项、当前账号 cookie、账号列表等运行时数据，
              并在多线程环境下通过类级 RLock 保证写入安全。
    """

    # 同一个进程内，所有 KVDatabase 实例共享一把锁
    # 防止 Gradio / anyio 多线程同时写 TinyDB
    _lock = RLock()

    def __init__(self, db_path: Optional[str]):
        """
        初始化键值数据库。

        参数：
            db_path (Optional[str])：数据库文件路径，None 则使用内存存储。
        返回值：无。
        内部逻辑：
            1. 若 db_path 为 None，使用 MemoryStorage 创建内存数据库；
            2. 否则先调用 _ensure_valid_tinydb_file 校验/修复文件，再用 JSONStorage 打开。
        调用位置：util/__init__.py 创建 ConfigDB、CookieManager 等场景调用。
        """
        if db_path is None:
            self.db = TinyDB(storage=MemoryStorage)
        else:
            # 在初始化 TinyDB 之前先验证/修复文件格式
            _ensure_valid_tinydb_file(db_path)
            self.db = TinyDB(db_path, storage=JSONStorage)

        self.KeyValue = Query()

    def insert(self, key: str, value: Any) -> None:
        """
        插入或更新键值对。

        参数：
            key (str)：键名。
            value (Any)：要保存的值，需可 JSON 序列化。
        返回值：无。
        内部逻辑：在类级锁保护下，使用 TinyDB upsert 按 key 更新或插入文档。
        调用位置：配置保存、cookie 写入、账号列表更新等场景调用。
        """
        with self._lock:
            self.db.upsert(
                {"key": key, "value": value},
                self.KeyValue.key == key,
            )

    def get(self, key: str) -> Any:
        """
        获取 key 对应的 value。

        参数：
            key (str)：键名。
        返回值：Any，若 key 不存在返回 None，存在则返回 value 字段。
        内部逻辑：在类级锁保护下查询 TinyDB，异常时返回 None。
        调用位置：配置读取、cookie 读取、账号列表读取等场景调用。
        """
        try:
            with self._lock:
                result = self.db.get(self.KeyValue.key == key)
        except Exception:
            return None

        return result["value"] if result else None

    def get_as_int(self, key: str, default: int) -> int:
        """
        以 int 类型读取配置值。

        参数：
            key (str)：键名。
            default (int)：转换失败或不存在时返回的默认值。
        返回值：int，成功转换后的整数值，或 default。
        内部逻辑：调用 self.get 获取原始值，尝试 int() 转换，异常返回 default。
        调用位置：需要读取数值型配置的场景调用。
        """
        raw = self.get(key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value

    def get_as_bool(self, key: str, default: bool) -> bool:
        """
        以 bool 类型读取配置值。

        参数：
            key (str)：键名。
            default (bool)：不存在时返回的默认值。
        返回值：bool，原始值的布尔转换结果，或 default。
        内部逻辑：若值为 None 返回 default，否则使用 bool() 转换。
        调用位置：需要读取布尔型配置的场景调用。
        """
        value = self.get(key)
        if value is None:
            return default
        return bool(value)

    def update(self, key: str, value: Any) -> None:
        """
        更新已存在的 key。

        参数：
            key (str)：键名。
            value (Any)：新值。
        返回值：无。
        内部逻辑：先判断 key 是否存在，存在则更新 value，不存在抛出 KeyError。
        调用位置：明确 key 已存在且仅更新的场景调用。
        """
        with self._lock:
            if self.db.contains(self.KeyValue.key == key):
                self.db.update(
                    {"value": value},
                    self.KeyValue.key == key,
                )
            else:
                raise KeyError(f"Key '{key}' not found in database.")

    def delete(self, key: str) -> None:
        """
        删除 key。

        参数：
            key (str)：键名。
        返回值：无。
        内部逻辑：在类级锁保护下，使用 TinyDB remove 按 key 删除文档。
        调用位置：需要删除配置项或清理数据的场景调用。
        """
        with self._lock:
            self.db.remove(self.KeyValue.key == key)

    def contains(self, key: str) -> bool:
        """
        判断 key 是否存在。

        参数：
            key (str)：键名。
        返回值：bool，存在返回 True，否则返回 False。
        内部逻辑：在类级锁保护下，使用 TinyDB contains 查询。
        调用位置：调用方需先判断 key 是否存在再读取时调用。
        """
        with self._lock:
            return self.db.contains(self.KeyValue.key == key)

    def close(self) -> None:
        """
        关闭数据库。

        参数：无。
        返回值：无。
        内部逻辑：在类级锁保护下关闭 TinyDB 文件句柄。
        调用位置：程序退出前若想主动释放文件句柄，可以调用。
        """
        with self._lock:
            self.db.close()
