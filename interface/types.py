"""
interface/types.py — 接口层通用数据类型定义。

文件整体功能：
  定义配置校验结果、内存抢票任务记录等跨模块复用的数据类，
  统一数据结构并提供序列化能力（to_dict）。

所属模块：接口层 (interface)
依赖文件：无（仅依赖 Python 标准库 dataclasses 与 typing）。

对外能力：
  - ValidationResult — 票务配置校验结果。
  - BuyTaskRecord    — 内存态抢票任务记录。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """
    票务配置校验结果。

    类设计作用：
      承载 validate_config 对票务配置进行完整性校验后的结果，
      包括是否通过、错误信息、警告信息以及规范化后的配置副本。

    存储属性：
      - ok               : bool — 校验是否通过（无错误时为 True）。
      - errors           : list[str] — 严重错误信息列表，缺省为空列表。
      - warnings         : list[str] — 警告信息列表，缺省为空列表。
      - normalized_config: dict[str, Any] | None — 校验过程中规范化后的配置字典。
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized_config: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        将校验结果序列化为普通字典。

        核心作用：
          基于 dataclasses.asdict 深拷贝所有字段，便于 JSON 序列化或返回给调用方。

        输入参数：无。

        返回值：
          dict[str, Any] — 包含 ok、errors、warnings、normalized_config 的字典。

        调用位置：
          由 validate_config、start_buy、run_buy_sync、start_managed_buy 等函数在返回结果时调用。
        """
        return asdict(self)


@dataclass
class BuyTaskRecord:
    """
    内存态抢票任务记录。

    类设计作用：
      在 start_buy 创建的内存任务中，保存任务生命周期、日志、支付链接等状态，
      供 task_status 查询与展示。

    存储属性：
      - task_id          : str — 任务唯一标识。
      - status           : str — 任务状态（pending / running / succeeded / completed / failed 等）。
      - detail           : str — 任务描述（通常为 project-screen-sku 组合字符串）。
      - created_at       : float — 任务创建时间戳。
      - started_at       : float | None — 任务开始运行时间戳。
      - finished_at      : float | None — 任务结束时间戳。
      - error            : str | None — 失败时的错误信息。
      - payment_qr_url   : str | None — 支付二维码链接。
      - order_id         : int | str | None — 订单号。
      - order_detail_url : str | None — 订单详情页链接。
      - payment_code_url : str | None — 付款码链接。
      - logs             : list[str] — 运行日志列表，缺省为空列表。
    """

    task_id: str
    status: str
    detail: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    payment_qr_url: str | None = None
    order_id: int | str | None = None
    order_detail_url: str | None = None
    payment_code_url: str | None = None
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """
        将任务记录序列化为普通字典。

        核心作用：
          基于 dataclasses.asdict 深拷贝所有字段，便于 task_status 返回 JSON 兼容数据。

        输入参数：无。

        返回值：
          dict[str, Any] — 任务记录的所有字段字典。

        调用位置：
          由 start_buy、task_status 返回内存任务状态时使用。
        """
        return asdict(self)
