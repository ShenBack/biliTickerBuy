"""
文件整体功能：定义 B 站请求连接异常。
所属模块：util.request.exceptions
依赖文件：无。
对外能力：提供 BiliConnectionError 异常类，用于标识网络连接相关错误。
"""
class BiliConnectionError(RuntimeError):
    """
    B 站请求连接异常。

    类设计作用：封装网络超时、连接中断等与网络相关的错误，便于上层统一捕获。
    存储属性：
        cause (Exception | None)：原始异常对象，用于调试。
    承担业务：在 BiliRequest 中捕获网络异常时抛出，区分业务错误与网络错误。
    """
    def __init__(self, message: str, *, cause: Exception | None = None):
        """
        初始化连接异常。

        参数：
            message (str)：异常提示信息。
            cause (Exception | None)：原始异常对象，默认 None。
        返回值：无。
        内部逻辑：调用父类初始化并保存 cause。
        调用位置：BiliRequest 在 _send_with_h2_recovery 中捕获网络异常时抛出。
        """
        super().__init__(message)
        self.cause = cause
