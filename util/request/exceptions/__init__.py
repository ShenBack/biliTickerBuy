"""
文件整体功能：导出 B 站请求相关异常类。
所属模块：util.request.exceptions
依赖文件：
    - util.request.exceptions.connection.BiliConnectionError
    - util.request.exceptions.rate_limit.BiliRateLimitError
对外能力：统一导出 BiliConnectionError 与 BiliRateLimitError，供上层模块捕获。
"""
from .connection import BiliConnectionError
from .rate_limit import BiliRateLimitError

__all__ = ["BiliConnectionError", "BiliRateLimitError"]
