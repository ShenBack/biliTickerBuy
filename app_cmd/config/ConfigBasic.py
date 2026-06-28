"""
app_cmd/config/ConfigBasic.py — 通用配置基类与字段工具。

文件整体功能：
  定义配置系统的基础能力，包括：
  1. 通用类型转换辅助函数（str_to_bool、normalize_log_level）。
  2. 配置字段声明工具（config_field、nested_config_field）。
  3. 通用配置基类 BasicConfig，支持从环境变量、字典映射、配置数据库读取，
     并支持配置覆盖与命令行参数导出。

所属模块：
  配置层 (app_cmd.config)

依赖文件：
  无外部业务依赖，仅使用 Python 标准库与 typing。

对外能力：
  - str_to_bool(value) → 将任意值转为 bool。
  - normalize_log_level(value) → 规范化日志级别字符串。
  - config_field(...) → 声明支持多来源（env/runtime/db/cli）的配置字段。
  - nested_config_field(factory) → 声明嵌套配置字段。
  - BasicConfig → 提供 from_env / from_mapping / from_config_getter / with_overrides / to_cli_args。
"""

from __future__ import annotations

import copy
import os
from dataclasses import MISSING, field, fields
from typing import Any, Callable, ClassVar


# 默认创建订单重试次数上限
DEFAULT_CREATE_RETRY_LIMIT = 10

# 默认单次批量发送创建订单请求的数量
DEFAULT_CREATE_REQUEST_BATCH_SIZE = 1

# 默认外层循环间隔（毫秒）
DEFAULT_OUTER_LOOP_INTERVAL = 1000


def str_to_bool(value: Any) -> bool:
    """
    将任意输入值转换为布尔值。

    核心作用：
      统一处理配置中可能出现的布尔字符串、实际 bool 值等。

    输入参数：
      value : Any
        待转换值。若本身为 bool 则直接返回；否则转为字符串后判断。

    返回值：
      bool
        当 value 为 True 或字符串为 "1", "true", "yes", "y", "on"（不区分大小写）时返回 True；
        否则返回 False。

    内部关键执行逻辑：
      1. 检查是否为 bool 类型，是则直接返回。
      2. 转为字符串并去除首尾空格、转小写。
      3. 与真值集合比较。

    调用场景：
      作为 config_field 的 cast 函数被 BuyConfig / NotifierConfig 等字段使用。
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_log_level(value: Any) -> str:
    """
    将日志级别输入规范化为小写字符串。

    核心作用：
      避免用户输入大小写不一致导致日志级别判定失败。

    输入参数：
      value : Any
        日志级别原始值，例如 "DEBUG", "simple", None。

    返回值：
      str
        规范化后的小写字符串；空值时默认返回 "standard"。

    内部关键执行逻辑：
      1. 对 value 或默认值 "standard" 调用 str()。
      2. 转小写后返回。

    调用场景：
      作为 BuyConfig.log_level 字段的 cast 函数。
    """
    return str(value or "standard").lower()


def config_field(
    default: Any = MISSING,
    *,
    default_factory: Callable[[], Any] | Any = MISSING,
    env: str | None = None,
    runtime: str | None = None,
    db: str | None = None,
    cli: str | None = None,
    cast: Callable[[Any], Any] | None = None,
    env_default: Any = MISSING,
    runtime_default: Any = MISSING,
    db_default: Any = MISSING,
    env_transform: Callable[[Any], Any] | None = None,
    runtime_transform: Callable[[Any], Any] | None = None,
    db_transform: Callable[[Any], Any] | None = None,
    cli_false: str | None = None,
    cli_true: str | None = None,
    omit_cli_if_empty: bool = True,
):
    """
    声明一个支持多来源读取的配置字段。

    核心作用：
      为 dataclass 字段附加元数据，标记该字段可从环境变量、运行时字典、
      配置数据库或命令行参数中读取，并指定默认值、类型转换与转换函数。

    输入参数：
      default : Any
        字段默认值；与 default_factory 互斥。
      default_factory : Callable[[], Any] | Any
        字段默认工厂函数；与 default 互斥。
      env : str | None
        环境变量名。
      runtime : str | None
        运行时字典中的 key。
      db : str | None
        配置数据库中的 key。
      cli : str | None
        命令行参数 flag，例如 "--interval"。
      cast : Callable[[Any], Any] | None
        值类型转换函数，例如 int / str_to_bool / normalize_log_level。
      env_default : Any
        环境变量不存在时的专属默认值。
      runtime_default : Any
        运行时字典缺失时的专属默认值。
      db_default : Any
        配置数据库缺失时的专属默认值。
      env_transform : Callable[[Any], Any] | None
        环境变量读取后的转换函数。
      runtime_transform : Callable[[Any], Any] | None
        运行时读取后的转换函数。
      db_transform : Callable[[Any], Any] | None
        配置数据库读取后的转换函数。
      cli_false : str | None
        布尔值为 False 时输出的命令行参数。
      cli_true : str | None
        布尔值为 True 时输出的命令行参数。
      omit_cli_if_empty : bool
        值为空时是否跳过输出该 CLI 参数，默认 True。

    返回值：
      dataclasses.Field
        带 metadata 的 dataclass 字段定义。

    内部关键执行逻辑：
      1. 校验 default 与 default_factory 不能同时存在。
      2. 将各类元数据打包为 metadata 字典。
      3. 调用 dataclasses.field 返回字段定义。

    调用场景：
      被 BuyConfig、NotifierConfig 等配置类大量调用以声明字段。
    """
    metadata = {
        "env": env,
        "runtime": runtime,
        "db": db,
        "cli": cli,
        "cast": cast,
        "env_default": env_default,
        "runtime_default": runtime_default,
        "db_default": db_default,
        "env_transform": env_transform,
        "runtime_transform": runtime_transform,
        "db_transform": db_transform,
        "cli_false": cli_false,
        "cli_true": cli_true,
        "omit_cli_if_empty": omit_cli_if_empty,
    }

    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("不能同时传 default 和 default_factory")

    if default_factory is not MISSING:
        return field(default_factory=default_factory, metadata=metadata)

    if default is not MISSING:
        return field(default=default, metadata=metadata)

    return field(metadata=metadata)


def nested_config_field(default_factory: Callable[[], Any]):
    """
    声明一个嵌套配置字段。

    核心作用：
      用于在配置类中嵌入另一个 BasicConfig 子类（如 NotifierConfig），
      使 from_env / from_mapping / from_config_getter 能够递归解析子配置。

    输入参数：
      default_factory : Callable[[], Any]
        嵌套配置类的无参构造函数，通常直接写 NotifierConfig。

    返回值：
      dataclasses.Field
        带 nested_config 元数据的字段定义。

    内部关键执行逻辑：
      使用 dataclasses.field 包装 default_factory 与 nested_config 标记。

    调用场景：
      被 BuyConfig 用于声明 notifier_config 嵌套字段。
    """
    return field(
        default_factory=default_factory,
        metadata={
            "nested_config": True,
        },
    )


class BasicConfig:
    """
    通用配置基类。

    类设计作用：
      为所有业务配置类（BuyConfig、NotifierConfig 等）提供统一的数据来源解析、
      配置覆盖与命令行导出能力，避免各子类重复实现环境变量读取、数据库读取等逻辑。

    存储属性：
      _skip_cli_fields : ClassVar[set[str]]
        子类可覆写，指定哪些字段不参与 to_cli_args() 导出。

    整体承担业务：
      1. 从环境变量构建配置实例（from_env）。
      2. 从任意字典映射构建实例（from_mapping）。
      3. 从配置数据库 getter 构建实例（from_config_getter）。
      4. 基于现有实例生成覆盖后的新实例（with_overrides）。
      5. 将实例导出为命令行参数列表（to_cli_args）。
    """

    _skip_cli_fields: ClassVar[set[str]] = set()

    @classmethod
    def _field_default(cls, f) -> Any:
        """
        获取 dataclass 字段的默认值。

        核心作用：
          优先返回字段的 default；若不存在则调用 default_factory；
          两者都不存在则返回 None。

        输入参数：
          f : dataclasses.Field
            待读取默认值的字段对象。

        返回值：
          Any
            字段默认值、工厂函数产物或 None。

        内部关键执行逻辑：
          使用 copy.deepcopy 避免默认值被后续修改污染。

        调用场景：
          被 _source_default() 调用，作为 fallback 默认值来源。
        """
        if f.default is not MISSING:
            return copy.deepcopy(f.default)

        if f.default_factory is not MISSING:  # type: ignore[attr-defined]
            return f.default_factory()  # type: ignore[misc]

        return None

    @classmethod
    def _source_default(cls, f, source_name: str) -> Any:
        """
        获取指定来源（env/runtime/db）的字段默认值。

        核心作用：
          若字段声明了 <source>_default 元数据，则优先返回；
          否则回退到字段本身的 default / default_factory。

        输入参数：
          f : dataclasses.Field
            字段对象。
          source_name : str
            来源名称，取值 "env" / "runtime" / "db"。

        返回值：
          Any
            该来源对应的默认值。

        内部关键执行逻辑：
          1. 拼接元数据 key："{source_name}_default"。
          2. 从 f.metadata 读取；若存在则深拷贝返回。
          3. 否则调用 _field_default()。

        调用场景：
          被 _normalize_value()、from_mapping()、from_env()、from_config_getter() 调用。
        """
        key = f"{source_name}_default"
        value = f.metadata.get(key, MISSING)

        if value is not MISSING:
            return copy.deepcopy(value)

        return cls._field_default(f)

    @staticmethod
    def _safe_apply(value: Any, func: Callable[[Any], Any] | None, default: Any) -> Any:
        """
        安全地对值应用转换函数。

        核心作用：
          在类型转换或来源转换时捕获 TypeError / ValueError，失败时返回默认值，
          避免单个字段解析错误导致整个配置构建失败。

        输入参数：
          value : Any
            待转换的原始值。
          func : Callable[[Any], Any] | None
            转换函数；为 None 时直接返回 value。
          default : Any
            转换失败时的回退值。

        返回值：
          Any
            转换成功返回 func(value)，失败返回 default。

        内部关键执行逻辑：
          使用 try/except 捕获 TypeError、ValueError。

        调用场景：
          被 _normalize_value() 调用。
        """
        if func is None:
            return value

        try:
            return func(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _normalize_value(cls, f, value: Any, *, source_name: str) -> Any:
        """
        规范化字段从指定来源读取的原始值。

        核心作用：
          对来源值执行默认值填充、cast 类型转换、来源 transform 转换，
          得到最终可写入字段的值。

        输入参数：
          f : dataclasses.Field
            字段对象。
          value : Any
            从来源读取的原始值。
          source_name : str
            来源名称，取值 "env" / "runtime" / "db"。

        返回值：
          Any
            规范化后的字段值。

        内部关键执行逻辑：
          1. 读取来源默认值；若 value 为 None 则填充默认值。
          2. 应用 cast 函数进行类型转换。
          3. 应用 <source>_transform 函数进行业务转换。

        调用场景：
          被 from_mapping()、from_env()、from_config_getter() 调用。
        """
        default = cls._source_default(f, source_name)

        if value is None:
            value = default

        cast = f.metadata.get("cast")
        value = cls._safe_apply(value, cast, default)

        transform = f.metadata.get(f"{source_name}_transform")
        value = cls._safe_apply(value, transform, default)

        return value

    @classmethod
    def _is_nested_config_field(cls, f) -> bool:
        """
        判断字段是否为嵌套配置字段。

        核心作用：
          通过 metadata 中的 "nested_config" 标记识别需要递归解析的子配置。

        输入参数：
          f : dataclasses.Field
            字段对象。

        返回值：
          bool
            是嵌套配置字段返回 True，否则返回 False。

        调用场景：
          被 from_mapping()、from_env()、from_config_getter() 调用。
        """
        return bool(f.metadata.get("nested_config"))

    @classmethod
    def from_mapping(
        cls,
        source: dict[str, Any],
        *,
        source_name: str,
    ):
        """
        从字典映射构建配置实例。

        核心作用：
          根据字段元数据中声明的 source_name key，从 source 字典读取值并规范化，
          递归处理嵌套配置。

        输入参数：
          source : dict[str, Any]
            原始配置字典。
          source_name : str
            使用哪种来源 key，取值 "env" / "runtime" / "db"。

        返回值：
          cls 的实例
            构建完成的配置对象。

        内部关键执行逻辑：
          1. 遍历 cls 的所有 init 字段。
          2. 对嵌套配置字段递归调用 nested_cls.from_mapping。
          3. 读取 source 中对应 key 的原始值。
          4. 调用 _normalize_value 规范化后写入 kwargs。
          5. 使用 cls(**kwargs) 实例化。

        调用场景：
          被子类 from_runtime_options 方法调用，例如 BuyConfig.from_runtime_options。
        """
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if not f.init:
                continue

            if cls._is_nested_config_field(f):
                nested_cls = f.default_factory  # type: ignore[attr-defined]
                kwargs[f.name] = nested_cls.from_mapping(
                    source,
                    source_name=source_name,
                )
                continue

            source_key = f.metadata.get(source_name)
            if not source_key:
                continue

            default = cls._source_default(f, source_name)
            raw = source.get(source_key, default)

            kwargs[f.name] = cls._normalize_value(
                f,
                raw,
                source_name=source_name,
            )

        return cls(**kwargs)

    @classmethod
    def from_env(cls):
        """
        从环境变量构建配置实例。

        核心作用：
          读取 os.environ 中字段元数据声明的 env 变量，规范化后构建配置。

        输入参数：无（通过 os.environ 隐式读取）。

        返回值：
          cls 的实例
            基于当前环境变量构建的配置对象。

        内部关键执行逻辑：
          1. 遍历 cls 的所有 init 字段。
          2. 对嵌套配置字段递归调用 nested_cls.from_env。
          3. 读取 env_key 对应的环境变量值。
          4. 调用 _normalize_value(source_name="env") 规范化。

        调用场景：
          可供 CLI 入口在启动时从环境变量加载配置。
        """
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if not f.init:
                continue

            if cls._is_nested_config_field(f):
                nested_cls = f.default_factory  # type: ignore[attr-defined]
                kwargs[f.name] = nested_cls.from_env()
                continue

            env_key = f.metadata.get("env")
            if not env_key:
                continue

            default = cls._source_default(f, "env")
            raw = os.environ.get(env_key, default)

            kwargs[f.name] = cls._normalize_value(
                f,
                raw,
                source_name="env",
            )

        return cls(**kwargs)

    @classmethod
    def from_config_getter(
        cls,
        getter: Callable[[str], Any],
    ):
        """
        从配置数据库 getter 构建配置实例。

        核心作用：
          通过传入的 getter 函数（如 ConfigDB.get）读取数据库值并规范化，
          支持嵌套配置递归解析。

        输入参数：
          getter : Callable[[str], Any]
            接收 db key 并返回对应值的函数。

        返回值：
          cls 的实例
            基于配置数据库构建的配置对象。

        内部关键执行逻辑：
          1. 遍历 cls 的所有 init 字段。
          2. 对嵌套配置字段递归调用 nested_cls.from_config_getter。
          3. 使用 getter(db_key) 读取数据库值。
          4. 缺失时填充 db_default。
          5. 调用 _normalize_value(source_name="db") 规范化。

        调用场景：
          被 BuyConfig.from_config_db 与 NotifierConfig.from_config_db 调用。
        """
        kwargs: dict[str, Any] = {}

        for f in fields(cls):
            if not f.init:
                continue

            if cls._is_nested_config_field(f):
                nested_cls = f.default_factory  # type: ignore[attr-defined]
                kwargs[f.name] = nested_cls.from_config_getter(getter)
                continue

            db_key = f.metadata.get("db")
            if not db_key:
                continue

            default = cls._source_default(f, "db")
            raw = getter(db_key)

            if raw is None:
                raw = default

            kwargs[f.name] = cls._normalize_value(
                f,
                raw,
                source_name="db",
            )

        return cls(**kwargs)

    def with_overrides(self, **changes):
        """
        基于当前配置生成一个带有局部覆盖的新配置实例。

        核心作用：
          在不修改原对象的前提下，使用深拷贝创建新实例并应用覆盖值。

        输入参数：
          **changes : Any
            需要覆盖的字段名与值。

        返回值：
          cls 的实例
            覆盖后的新配置对象。

        内部关键执行逻辑：
          1. 深拷贝当前所有 init 字段值。
          2. 用 changes 更新对应字段。
          3. 使用 type(self)(**payload) 创建新实例。

        调用场景：
          被 BuyConfig.from_runtime_options、from_config_db 等用于注入运行时代码中的显式参数。
        """
        payload = {
            f.name: copy.deepcopy(getattr(self, f.name)) for f in fields(self) if f.init
        }
        payload.update(changes)
        return type(self)(**payload)

    def to_cli_args(self) -> list[str]:
        """
        将配置实例导出为命令行参数列表。

        核心作用：
          遍历所有字段，将非空值转换为对应的 CLI flag 与值，
          支持布尔字段的 cli_true / cli_false 特殊输出。

        输入参数：无（读取 self 的字段值）。

        返回值：
          list[str]
            可直接传递给 subprocess 或 argparse 的参数列表。

        内部关键执行逻辑：
          1. 遍历所有 init 字段，跳过 _skip_cli_fields。
          2. 对嵌套配置递归调用 to_cli_args() 并拼接。
          3. 布尔值优先使用 cli_true / cli_false；否则使用 cli flag。
          4. 空值根据 omit_cli_if_empty 决定是否跳过。

        调用场景：
          在需要将内存配置转为命令行启动参数时调用，例如 buy 子命令启动子进程。
        """
        args: list[str] = []

        def append_value(flag: str, value: Any, *, omit_if_empty: bool = True) -> None:
            """
            辅助函数：将单个 flag/value 追加到 args 列表。

            核心作用：
              根据 omit_if_empty 控制是否跳过空值，并将值转为字符串追加。

            输入参数：
              flag : str
                命令行 flag。
              value : Any
                命令行值。
              omit_if_empty : bool
                值为 None 或空字符串时是否跳过，默认 True。

            返回值：无。

            调用场景：
              to_cli_args() 内部使用。
            """
            if omit_if_empty and value in (None, ""):
                return
            args.extend([flag, str(value)])

        for f in fields(self):
            if not f.init:
                continue

            if f.name in self._skip_cli_fields:
                continue

            value = getattr(self, f.name)

            if self._is_nested_config_field(f):
                if isinstance(value, BasicConfig):
                    args.extend(value.to_cli_args())
                continue

            cli_true = f.metadata.get("cli_true")
            cli_false = f.metadata.get("cli_false")
            cli = f.metadata.get("cli")
            omit_cli_if_empty = bool(f.metadata.get("omit_cli_if_empty", True))

            if isinstance(value, bool):
                if value and cli_true:
                    args.append(cli_true)
                elif not value and cli_false:
                    args.append(cli_false)
                elif cli:
                    append_value(cli, value, omit_if_empty=omit_cli_if_empty)
                continue

            if cli:
                append_value(cli, value, omit_if_empty=omit_cli_if_empty)

        return args
