"""
文件整体功能：定义 B 站请求限流异常。
所属模块：util.request.exceptions
依赖文件：无。
对外能力：提供 BiliRateLimitError 异常类，用于标识请求被限流。
"""
class BiliRateLimitError(RuntimeError):
    """
    B 站请求限流异常。

    类设计作用：封装 HTTP 429 状态码相关错误，便于上层处理限流逻辑。
    存储属性：
        response (requests.Response | httpx.Response | None)：触发限流的响应对象。
    承担业务：在 BiliRequest 检测到 HTTP 429 时抛出，用于触发重试或切换代理。
    """
    def __init__(self, message: str, *, response=None):
        """
        初始化限流异常。

        参数：
            message (str)：异常提示信息。
            response (Any | None)：触发限流的响应对象，默认 None。
        返回值：无。
        内部逻辑：调用父类初始化并保存 response。
        调用位置：BiliRequest 在 _request 中检测到 HTTP 429 时抛出。
        """
        super().__init__(message)
        self.response = response
