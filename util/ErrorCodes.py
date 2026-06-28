"""
文件整体功能：维护 B 站会员购接口返回的业务错误码与可读提示映射。
所属模块：util
依赖文件：无外部依赖。
对外能力：提供 ErrorCodes 类及其 MESSAGES 字典，用于将 errno 转换为中文提示、
          判断是否展示服务端原始 msg、格式化抢票尝试结果。
"""


class ErrorCodes:
    """
    B 站会员购错误码管理类。

    类设计作用：集中维护 errno 与中文说明的映射关系，统一错误提示输出格式。
    存储属性：
        MESSAGES (dict[int, str])：错误码到中文提示的映射表。
        SHOW_RESPONSE_MSG (set[int])：需要额外附加服务端返回 msg 的错误码集合。
    承担业务：在抢票重试流程中，把接口返回的数字错误码转换为用户可理解的文字，
              并在合适场景下追加服务端原始消息。
    """

    MESSAGES = {
        0: "成功",
        3: "下单过于频繁，请稍后再试",
        100001: "暂无可售票或登录状态异常",
        100041: "未到开票时间",
        100044: "需要完成人机验证",
        100003: "重复购买",
        100016: "项目不可售",
        100039: "活动收摊啦,下次要快点哦",
        100048: "已经下单，有尚未完成订单",
        100017: "票种不可售",
        100051: "订单准备过期，重新验证",
        100034: "票价错误",
        100009: "库存不足",
        219: "下单失败，请重试",
        221: "下单请求过多，请稍后再试",
        900001: "下单过快，被系统限制",
        900002: "当前请求较多，请稍后再试",
    }

    SHOW_RESPONSE_MSG = {10003, 100003}

    @classmethod
    def get_message(cls, code: int) -> str | None:
        """
        根据错误码获取中文提示。

        参数：
            code (int)：接口返回的 errno。
        返回值：str | None，存在则返回对应中文提示，不存在返回 None。
        内部逻辑：从 MESSAGES 字典中按 code 取值。
        调用位置：抢票结果解析、日志输出、前端提示等场景调用。
        """
        return cls.MESSAGES.get(code)

    @classmethod
    def get_message_or_unknown(cls, code: int) -> str:
        """
        根据错误码获取中文提示，未知码返回固定文案。

        参数：
            code (int)：接口返回的 errno。
        返回值：str，对应中文提示或 "未知错误码"。
        内部逻辑：使用 get 方法并指定默认值。
        调用位置：需要确保一定有可读文本的场景。
        """
        return cls.MESSAGES.get(code, "未知错误码")

    @classmethod
    def should_show_response_msg(cls, code: int) -> bool:
        """
        判断是否需要展示服务端返回的原始 msg。

        参数：
            code (int)：接口返回的 errno。
        返回值：bool，属于 SHOW_RESPONSE_MSG 集合时返回 True。
        内部逻辑：判断 code 是否在 SHOW_RESPONSE_MSG 中。
        调用位置：append_response_message 等方法内部调用。
        """
        return code in cls.SHOW_RESPONSE_MSG

    @classmethod
    def append_response_message(
        cls,
        code: int,
        base: str,
        ret: dict | None,
    ) -> str:
        """
        在基础错误提示后追加服务端原始 msg。

        参数：
            code (int)：接口返回的 errno。
            base (str)：基础提示文本。
            ret (dict | None)：接口返回的完整响应字典。
        返回值：str，若不需要追加或没有 msg 则返回 base，否则返回 base + " | msg: " + message。
        内部逻辑：先判断 should_show_response_msg，再从 ret 中提取 msg 或 message 字段。
        调用位置：format_attempt_result 等格式化结果时调用。
        """
        if not cls.should_show_response_msg(code) or ret is None:
            return base
        message = str(ret.get("msg", ret.get("message", "")) or "").strip()
        if not message:
            return base
        return f"{base} | msg: {message}"

    @classmethod
    def format_attempt_result(cls, err: int, ret: dict) -> str:
        """
        格式化单次抢票尝试的结果说明。

        参数：
            err (int)：接口返回的 errno。
            ret (dict)：接口返回的完整响应字典。
        返回值：str，包含错误码、中文说明及可能的服务端 msg。
        内部逻辑：先查询 MESSAGES，再调用 append_response_message 追加原始消息。
        调用位置：抢票任务在每次 create/order 请求后记录日志或展示结果时调用。
        """
        reason = cls.get_message(err)
        if reason:
            return cls.append_response_message(err, f"[{err}] {reason}", ret)
        return cls.append_response_message(err, f"[{err}] 未知错误码 | {ret}", ret)


# 兼容旧代码直接通过 ERRNO_DICT 访问错误码映射
ERRNO_DICT = ErrorCodes.MESSAGES
