"""
文件整体功能：从本地 JSON 资源加载随机失败提示语，供抢票失败场景使用。
所属模块：util.notifer
依赖文件：
    - util.get_application_path（获取应用根目录）
对外能力：
    提供 get_random_fail_message 函数，随机返回一条失败提示文案。
"""
import json
import os
import random

from util import get_application_path

_FAIL_MESSAGES: list[str] = []


def _load_messages() -> list[str]:
    """
    从 assets/fail_messages.json 加载失败提示语列表。

    参数：无。
    返回值：list[str]，失败提示语列表；加载失败时返回默认列表 ["抢票失败了..."]。
    内部逻辑：
        1. 拼接应用根目录下的 assets/fail_messages.json 路径；
        2. 以 UTF-8 读取并解析 JSON；
        3. 任何异常都返回默认兜底文案，避免影响主流程。
    调用位置：模块导入时自动调用，初始化 _FAIL_MESSAGES。
    """
    json_path = os.path.join(get_application_path(), "assets", "fail_messages.json")
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return ["抢票失败了..."]


_FAIL_MESSAGES = _load_messages()


def get_random_fail_message() -> str:
    """
    随机获取一条抢票失败提示语。

    参数：无。
    返回值：str，从预加载列表中随机选择的一条提示语。
    内部逻辑：使用 random.choice 从 _FAIL_MESSAGES 中随机挑选。
    调用位置：抢票失败、需要向用户展示或推送文案时调用。
    """
    return random.choice(_FAIL_MESSAGES)
