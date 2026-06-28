"""
文件整体功能：集中存放整个项目使用的常量配置。
所属模块：util
依赖文件：无外部依赖，仅使用 datetime 标准库定义时区。
对外能力：提供时区、默认阈值、URL、路由、超时、日志保留策略等全局常量，
          供其他模块通过 import 直接使用。
"""

import datetime


# 北京时间时区，用于项目内统一的时间展示与计算
BEIJING_TZ = datetime.timezone(datetime.timedelta(hours=8), name="Asia/Shanghai")

# 已上传配置文件列表在运行时状态中的存储键名
GO_UPLOADED_FILES_STATE_KEY = "go.uploaded_config_files"

# 默认请求间隔（毫秒）
DEFAULT_REQUEST_INTERVAL = 1000

# 默认限流延迟（毫秒）
DEFAULT_RATE_LIMIT_DELAY_MS = 300

# 默认创建请求批量大小
DEFAULT_CREATE_REQUEST_BATCH_SIZE = 3

# 代理连续失败阈值，超过后将进入冷却
DEFAULT_PROXY_MAX_CONSECUTIVE_FAILURES = 2

# 代理默认冷却时长（秒）
DEFAULT_PROXY_COOLDOWN_SECONDS = 180

# 代理退避最大时长（秒）
DEFAULT_PROXY_BACKOFF_MAX_SECONDS = 600

# 默认日志保留天数
DEFAULT_LOG_RETENTION_DAYS = 7

# 默认最大日志文件数
DEFAULT_MAX_LOG_FILES = 200

# 默认最大运行目录数
DEFAULT_MAX_RUN_DIRS = 100

# B 站会员购基础 URL
BASE_URL = "https://show.bilibili.com"

# 开票前预热时间点（秒），倒计时进入该范围时触发预热逻辑
WARMUP_AT_SECONDS = 5.0

# 倒计时状态上报间隔（秒）
COUNTDOWN_REPORT_INTERVAL_SECONDS = 15

# 默认创建订单重试上限
DEFAULT_CREATE_RETRY_LIMIT = 20

# 外层循环默认间隔（秒）
DEFAULT_OUTER_LOOP_INTERVAL = 0

# 更新通道在配置数据库中的键名
UPDATE_CHANNEL_KEY = "update_channel"

# Python 包名
PACKAGE_NAME = "bilitickerbuy"

# 日志查看路由（内部使用，以下划线开头）
_LOG_VIEW_ROUTE = "/__btb/logs/view"

# 日志流式推送路由（内部使用，以下划线开头）
_LOG_STREAM_ROUTE = "/__btb/logs/stream"

# MeoW 推送服务基础 URL
MEOW_API_BASE = "https://api.chuckfang.com"

# 默认 HTTP 请求超时（连接超时、读取超时）
DEFAULT_TIMEOUT = (3.05, 8)

# HTTP/2 请求各阶段超时配置
H2_TIMEOUT = {
    "connect": 3.05,
    "read": 5.0,
    "write": 5.0,
    "pool": 5.0,
}

# HTTP/2 连接池限制
H2_LIMITS = {
    "max_keepalive_connections": 10,
    "max_connections": 20,
    "keepalive_expiry": 60.0,
}
