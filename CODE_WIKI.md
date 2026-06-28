# biliTickerBuy — Code Wiki

> 项目版本：v2.15.15
> 文档范围：项目整体架构、模块职责、关键类/函数、依赖关系与运行方式

---

## 1. 项目概览

`biliTickerBuy` 是一个开源的 Bilibili 会员购抢票辅助工具，同时提供**命令行（CLI）**与**Gradio Web UI** 两种使用方式。项目核心目标是自动化 B 站演出/漫展票务的抢购流程，覆盖登录、配置生成、抢票任务执行、支付提醒与日志查看等全链路。

主要特性：

- 二维码扫码登录与 Cookie 多账号管理
- 通过项目 ID / 活动链接自动生成抢票配置
- 多进程/多代理并发抢票，支持代理池、失败冷却与退避策略
- 本地 token 生成与 prepare/create 接口双模式
- 成功通知（Server酱、PushPlus、Bark、ntfy、MeoW、飞书、音频）
- 实时日志（终端渲染 / Web SSE / 独立日志页）
- NTP 时间同步

---

## 2. 技术栈

| 层级 | 主要技术 |
|------|----------|
| 语言 | Python 3.11+ |
| CLI 参数解析 | `tyro` |
| Web UI | `gradio` >= 6.19.0 |
| HTTP 请求 | `httpx[http2,brotli,socks,zstd]`、`requests[socks]` |
| 日志 | `loguru` |
| 终端 UI | `textual`、`rich` |
| 持久化 | `tinydb`（KVDatabase） |
| 二维码 | `qrcode`、`Pillow` |
| 音频播放 | `playsound3` |
| 时间同步 | `ntplib` |
| 版本/打包 | `setuptools`、`PyInstaller`（main.spec） |

完整依赖见 [pyproject.toml](./pyproject.toml) 与 [requirements.txt](./requirements.txt)。

---

## 3. 项目架构

项目采用分层架构，自上而下分为：

```
┌─────────────────────────────────────────────────────────────┐
│ 入口层                                                        │
│  main.py                                                     │
├─────────────────────────────────────────────────────────────┤
│ 命令层 (app_cmd)                                             │
│  buy.py  ·  ticker.py  ·  cli_args.py  ·  config/*           │
├─────────────────────────────────────────────────────────────┤
│ UI 层 (tab)                                                  │
│  settings.py  ·  config.py  ·  go.py  ·  log.py  ·  share.py │
│  bws.py                                                      │
├─────────────────────────────────────────────────────────────┤
│ 业务接口层 (interface)                                        │
│  auth.py  ·  project.py  ·  search.py  ·  config.py          │
│  execution.py  ·  managed_runner.py  ·  common.py  · types.py│
├─────────────────────────────────────────────────────────────┤
│ 任务层 (task)                                                │
│  buy.py  ·  buy_helpers.py  ·  buy_types.py  ·  bws.py       │
├─────────────────────────────────────────────────────────────┤
│ 基础设施层 (util)                                             │
│  request/  ·  proxy/  ·  notifer/  ·  log/  ·  Storage/      │
│  TimeUtil.py  ·  Constant.py  ·  ErrorCodes.py  · __init__.py│
└─────────────────────────────────────────────────────────────┘
```

---

## 4. 目录结构与关键文件

```
biliTickerBuy-main/
├── main.py                     # 统一入口，解析 buy/ui 子命令
├── app_cmd/
│   ├── buy.py                  # CLI 抢票入口
│   ├── ticker.py               # Gradio Web UI 启动入口
│   ├── cli_args.py             # 命令行参数数据类
│   └── config/
│       ├── BuyConfig.py        # 抢票配置数据类
│       ├── ConfigBasic.py      # 配置基类与字段工具
│       └── NotifierConfig.py   # 通知配置数据类
├── app_version.py              # 获取应用版本
├── app_update.py               # GitHub Release 更新检查
├── interface/
│   ├── auth.py                 # B 站登录/二维码/Cookie 验证
│   ├── project.py              # 项目详情、票档、购票人、地址查询
│   ├── search.py               # 票务搜索
│   ├── config.py               # RuntimeOptions / 配置校验/生成
│   ├── execution.py            # 内存任务/子进程任务管理
│   ├── managed_runner.py       # 子进程抢票执行器
│   ├── common.py               # Cookie/配置加载通用工具
│   └── types.py                # 公共类型（ValidationResult / BuyTaskRecord）
├── task/
│   ├── buy.py                  # 核心抢票工作流
│   ├── buy_helpers.py          # 抢票辅助函数
│   ├── buy_types.py            # 抢票状态/事件/Worker
│   └── bws.py                  # BWS 预约任务逻辑
├── tab/
│   ├── settings.py             # 账号登录 + 配置生成标签页
│   ├── config.py               # 高级设置标签页
│   ├── go.py                   # 操作抢票标签页
│   ├── log.py                  # 任务与日志管理面板
│   ├── share.py                # 选票分享服务
│   └── bws.py                  # BWS 预约标签页
├── util/
│   ├── __init__.py             # 全局状态、路径、ConfigDB、main_request
│   ├── Constant.py             # 全局常量
│   ├── TimeUtil.py             # NTP 时间同步
│   ├── ErrorCodes.py           # B 站错误码映射
│   ├── CTokenUtilV2.py         # 下单 ctoken 生成
│   ├── FeishuUtil.py           # 飞书机器人推送
│   ├── Storage/
│   │   ├── KVDatabase.py       # TinyDB 键值存储
│   │   └── CleanupUtil.py      # 日志/运行目录清理
│   ├── log/
│   │   ├── LogConfig.py        # loguru 配置
│   │   ├── LogWeb.py           # Web 日志查看与 SSE 流
│   │   └── TerminalRenderer.py # 终端实时渲染
│   ├── notifer/
│   │   ├── Notifier.py         # 通知基类与管理器
│   │   ├── ServerChanUtil.py   # Server酱
│   │   ├── BarkUtil.py         # Bark
│   │   ├── PushPlusUtil.py     # PushPlus
│   │   ├── MeoWUtil.py         # MeoW
│   │   ├── NtfyUtil.py         # ntfy
│   │   ├── AudioUtil.py        # 音频通知
│   │   └── RandomMessages.py   # 失败随机语录
│   ├── proxy/
│   │   ├── ProxyManager.py     # 代理池管理
│   │   ├── ProxyState.py       # 代理状态注册表
│   │   ├── ProxyBackoff.py     # 代理耗尽退避
│   │   ├── ProxyTester.py      # 代理连通性测试
│   │   └── ProxyApiProvider.py # 代理 API 拉取
│   └── request/
│       ├── BiliRequest.py      # B 站 HTTP 请求封装
│       ├── CookieManager.py    # Cookie 与多账号管理
│       ├── TokenUtil.py        # 下单 token 生成
│       ├── BrowerState.py      # 浏览器指纹生成
│       └── exceptions/         # 自定义异常
├── assets/                     # 静态资源（CSS、图标、失败语录）
├── docs/                       # 安装/部署/代理文档
├── tests/                      # 单元测试
└── pyproject.toml / requirements.txt
```

---

## 5. 核心模块详解

### 5.1 入口层

#### `main.py`

- **职责**：统一程序入口，解析命令行参数并分发到 CLI 或 UI 模式。
- **关键函数**：
  - `_normalize_argv(argv)`：兼容旧版 `-cf` 参数；无参数时默认启动 `ui`。
  - `main()`：使用 `tyro.cli` 解析为 `BuyCliArgs` 或 `TickerCliArgs`，调用 `buy_cmd` / `ticker_cmd`。

### 5.2 命令层 (app_cmd)

#### `app_cmd/buy.py`

- **职责**：`python main.py buy ...` 子命令实现。
- **关键函数**：
  - `load_tickets_info(tickets_info, config_file)`：从 JSON 字符串或配置文件读取抢票配置。
  - `resolve_log_file_name(config, tickets_info)`：生成本次运行日志文件名。
  - `install_console_close_handler()`：Windows 下监听控制台关闭事件并写入停止标记。
  - `start_parent_watchdog(parent_pid)`：子进程模式下监控父进程，父进程退出则自杀。
  - `run_with_terminal_renderer(config, tickets_info)`：Windows 主进程启动 Textual/Rich 终端渲染。
  - `buy_cmd(args)`：CLI 抢票主入口，完成日志、时间同步、看门狗、清理注册与抢票启动。

#### `app_cmd/ticker.py`

- **职责**：`python main.py ui ...` 子命令实现，启动 Gradio Web UI。
- **关键函数**：
  - `ticker_cmd(args)`：初始化日志、加载图标/CSS、构建 Gradio Blocks、注入前端 JS、启动服务并挂载日志路由。
  - `_get_lan_ip()`：获取本机局域网 IP。
  - `_share_tab(server_name)`：构建“分享选票”标签页，支持局域网分享与 Cloudflare 隧道。

#### `app_cmd/cli_args.py`

- **职责**：定义命令行参数数据类与环境变量读取工具。
- **关键类**：
  - `TickerCliArgs`：UI 启动参数（share、server_name、port、root_path）。
  - `BuyCliArgs`：实际是 `BuyConfig` 的别名。

#### `app_cmd/config/BuyConfig.py`

- **职责**：抢票配置数据类，聚合运行参数、代理、通知配置。
- **关键类**：
  - `BuyConfig(BasicConfig)`：包含 `tickets_info`、`time_start`、`interval`、`https_proxys`、`notifier_config`、`log_level`、`use_local_token`、`create_retry_limit`、`create_request_batch_size`、`rate_limit_delay_ms` 等字段。
  - `from_config_db(...)`：从 `ConfigDB` 构建默认配置。

#### `app_cmd/config/ConfigBasic.py`

- **职责**：配置基类与字段定义工具。
- **关键类/函数**：
  - `BasicConfig`：提供 `from_mapping`、`from_env`、`with_overrides`。
  - `config_field(...)` / `nested_config_field(...)`：声明字段的多数据源（env / runtime / db / cli）。

#### `app_cmd/config/NotifierConfig.py`

- **职责**：通知渠道配置。
- **关键字段**：`serverchan_key`、`serverchan3_api_url`、`pushplus_token`、`bark_token`、`meow_nickname`、`ntfy_url`、`ntfy_username`、`ntfy_password`、`notify_proxy_exhausted` 等。

### 5.3 业务接口层 (interface)

#### `interface/auth.py`

- **职责**：B 站登录态管理与二维码登录流程。
- **关键函数**：
  - `get_login_state(...)`：检查登录态并返回用户名/UID。
  - `start_qr_login(...)`：生成二维码并返回登录 URL。
  - `poll_qr_login(...)`：轮询二维码登录结果。
  - `validate_cookies(...)`：验证 Cookie 有效性。

#### `interface/project.py`

- **职责**：B 站项目信息获取与解析。
- **关键函数**：
  - `fetch_project_payload(request, project_id)`：优先调用新版接口，失败回退旧版接口。
  - `extract_ticket_options(...)` / `extract_buyers(...)` / `extract_addresses(...)`：解析票档、购票人、地址。

#### `interface/search.py`

- **职责**：B 站票务搜索。
- **关键函数**：`search_tickets(keyword, ...)`：登录校验、构造搜索请求、格式化结果。

#### `interface/config.py`

- **职责**：运行时选项与配置校验。
- **关键类/函数**：
  - `RuntimeOptions`：包含 `interval`、`outer_interval`、`create_retry_limit`、`create_request_batch_size`、`rate_limit_delay_ms` 等运行时参数。
  - `validate_config(config_or_path)`：校验配置字段并返回 `ValidationResult`。
  - `generate_config(...)`：根据用户选择生成最终抢票 JSON 配置。

#### `interface/execution.py`

- **职责**：任务执行生命周期管理。
- **关键函数**：
  - `start_buy(...)`：启动内存后台线程抢票任务。
  - `start_managed_buy(...)`：启动独立子进程抢票任务，写入 `btb_runs/<run_id>` 目录。
  - `cancel_buy(task_id)` / `delete_buy(task_id)` / `get_buy_status(task_id)`。

#### `interface/managed_runner.py`

- **职责**：子进程抢票执行器，由 `start_managed_buy` 启动。
- **关键函数**：
  - `main(run_dir_arg)`：读取 `run.json` / `config.json` / `runtime.json`，执行 `Buy` 工作流，维护 `status.json` 与 `result.json`。

#### `interface/common.py`

- **职责**：通用工具（Cookie 解析、配置加载、项目 ID 提取、销售状态格式化）。
- **关键函数**：
  - `_resolve_cookie_list(...)`：统一从入参或文件解析 Cookie 列表。
  - `_load_config(...)`：从路径/字典加载配置。
  - `extract_project_id(...)`：从 URL 或文本提取项目 ID。

#### `interface/types.py`

- **职责**：公共数据类型。
- **关键类**：
  - `ValidationResult`：校验结果（ok、errors、warnings、normalized_config）。
  - `BuyTaskRecord`：任务记录。

### 5.4 任务层 (task)

#### `task/buy.py`

- **职责**：核心抢票工作流。
- **关键类/函数**：
  - `Buy`：封装配置，提供 `buy()`（直接阻塞运行）与 `start_worker()`（启动事件 Worker）。
  - `buy_stream(config)`：主生成器，循环执行：等待开售 → prepare 订单 → 批次 create 请求 → 成功/失败/重试处理 → 支付结果构建。

#### `task/buy_helpers.py`

- **职责**：抢票流程辅助函数。
- **关键函数**：
  - `build_payment_result(_request, order_id)`：构建订单详情页、支付二维码 URL。
  - `wait_until_start(...)`：倒计时等待开售。
  - `build_token_payload(...)`：构造下单 token 请求体。
  - `refresh_count(...)`：刷新余票数量。

#### `task/buy_types.py`

- **职责**：抢票状态与事件类型。
- **关键类**：
  - `BuyStreamState`：阶段、倒计时、当前代理、失败次数等状态。
  - `BuyStreamEvent`：事件类型与消息。
  - `BuyStreamWorker(LatestValueWorker)`：后台 Worker，产出事件流。

#### `task/bws.py`

- **职责**：BWS（Bilibili World/Show）园区/场次预约逻辑。
- **关键函数**：查询可预约日期、绑定门票、提交预约、循环等待开票与重试。

### 5.5 UI 层 (tab)

| 文件 | 标签页 | 职责 |
|------|--------|------|
| `tab/settings.py` | 账号登录 / 生成配置 | 二维码登录、Cookie 管理、购票人导入、项目解析、票档/日期/购票人选择、生成 JSON 配置 |
| `tab/config.py` | 高级设置 | 代理、音频、推送、支付二维码、并发策略、日志清理、抢票间隔等全局配置 |
| `tab/go.py` | 操作抢票 | 上传配置、预览、代理状态监控、设置抢票时间、启动子进程任务、任务面板集成 |
| `tab/log.py` | 日志查看 / 任务管理 | 任务状态同步、终止任务、日志列表、支付二维码自动打开 |
| `tab/share.py` | 分享选票 | 局域网 HTTP 分享服务、Cloudflare 内网穿透 |
| `tab/bws.py` | BWS 预约 | BWS 预约 UI |

### 5.6 基础设施层 (util)

#### `util/__init__.py`

- **职责**：全局初始化与运行时状态管理。
- **关键全局对象**：
  - `EXE_PATH` / `TEMP_PATH` / `LOG_DIR` / `CONFIG_DB_PATH` / `GLOBAL_COOKIE_PATH`
  - `ConfigDB`：TinyDB 键值数据库实例。
  - `main_request`：全局 `BiliRequest` 实例。
  - `time_service`：`TimeUtil` 实例，已同步 NTP 偏移。
  - `GlobalStatusInstance`：全局状态单例，维护任务日志、运行时状态、代理占用。
- **关键装饰器**：
  - `runtime_state_reader(key, ...)`：从全局状态读取值作为函数后备。
  - `runtime_state_writer(key, ...)`：将函数输入/返回值写入全局状态。

#### `util/request/BiliRequest.py`

- **职责**：B 站 HTTP 请求封装。
- **关键类**：`BiliRequest`
- **能力**：
  - HTTP/2 连接池（基于 `httpx`）。
  - 浏览器指纹模拟（`BrowerState`）。
  - Cookie 同步（`CookieManager`）。
  - 代理自动切换（`ProxyManager`）。
  - 统一的 `get/post` 与 `_request` 重试/错误处理。

#### `util/request/CookieManager.py`

- **职责**：Cookie 存储、解析与多账号管理。
- **关键类**：`CookieManager`
- **能力**：
  - 从 KVDatabase 读取/写入 Cookie。
  - 多账号（`Account`）增删查改。
  - 通过 Cookie 拉取 B 站用户信息（UID、昵称、等级、大会员）。

#### `util/request/TokenUtil.py` / `util/CTokenUtilV2.py`

- **职责**：生成 B 站会员购下单所需的 `token` 与 `ctoken`。
- **使用场景**：`use_local_token=True` 时跳过 prepare 接口直接使用本地 token。

#### `util/proxy/ProxyManager.py`

- **职责**：代理池管理。
- **关键类**：`ProxyManager`
- **能力**：
  - 解析代理字符串（支持 http/https/socks5，含认证）。
  - 记录代理成功/失败/冷却状态。
  - `rotate()`：切换到下一个可用代理。
  - `apply_to_session(session)`：应用代理到 `requests.Session`。

#### `util/proxy/ProxyState.py`

- **职责**：代理状态维护。
- **关键类**：
  - `ProxyStateEntry`：单个代理的失败次数、成功次数、冷却截止时间。
  - `ProxyStateRegistry`：代理池状态注册表，支持 `switch_to_next_available()`。

#### `util/proxy/ProxyBackoff.py`

- **职责**：代理全部不可用时按指数退避计算休眠时间。
- **关键类**：`ProxyBackoff`
- **方法**：`next_delay_seconds()`。

#### `util/proxy/ProxyTester.py`

- **职责**：并发测试多个代理对 B 站接口的连通性与出口 IP。
- **关键函数**：`test_proxy_connectivity(proxy_string, timeout)`。

#### `util/proxy/ProxyApiProvider.py`

- **职责**：从外部代理 API 拉取代理列表。
- **关键函数**：`fetch_proxy_api(api_url, count, protocol, ...)`，自动拼接 `format=json`、`count`、`protocol`。

#### `util/notifer/Notifier.py`

- **职责**：通知系统抽象与管理。
- **关键类**：
  - `NotifierBase`：抽象基类，定义 `send_message(title, message)`。
  - `NotifierConfig`：通知配置数据类。
  - `NotifierManager`：统一管理多个通知器，提供 `start_all()`、`send_all()`、`test_all_notifiers()`。

#### `util/notifer/*Util.py`

实现各通知渠道：

- `ServerChanUtil.py`：Server酱 Turbo / Server酱³
- `PushPlusUtil.py`：PushPlus
- `BarkUtil.py`：Bark（iOS）
- `MeoWUtil.py`：MeoW（鸿蒙 HMS）
- `NtfyUtil.py`：ntfy
- `AudioUtil.py`：本地音频文件播放
- `FeishuUtil.py`：飞书自定义机器人 Webhook

#### `util/log/LogConfig.py`

- **职责**：配置 `loguru`。
- **关键函数**：`loguru_config(log_dir, log_file_name, ...)`：设置文件/控制台 sink、轮替、保留。

#### `util/log/LogWeb.py`

- **职责**：Web 日志查看与 SSE 实时流。
- **关键函数**：`attach_log_routes(app)`：挂载 `/log/view` 与 `/log/stream` 到 FastAPI。

#### `util/log/TerminalRenderer.py`

- **职责**：终端实时渲染抢票状态。
- **关键类**：
  - `BaseTerminalRenderer`
  - `TextualTerminalRenderer`：基于 Textual 的富文本界面。
  - `PlainTerminalRenderer`：纯文本回退。
- **关键函数**：`create_terminal_renderer(...)`、`render_message_stream(...)`。

#### `util/Storage/KVDatabase.py`

- **职责**：基于 TinyDB 的键值存储。
- **关键类**：`KVDatabase`
- **能力**：`insert/get/delete`、线程安全、兼容旧配置格式、类型转换辅助（`get_as_bool`、`get_as_int`）。

#### `util/Storage/CleanupUtil.py`

- **职责**：运行时产物清理。
- **关键函数**：`cleanup_runtime_artifacts(...)`：按保留天数/最大数量清理日志与运行目录。

#### `util/TimeUtil.py`

- **职责**：NTP 时间同步与毫秒级时间戳。
- **关键类/函数**：
  - `current_time_ms(...)`：生成带偏移的毫秒时间戳。
  - `TimeUtil.compute_timeoffset()`：通过 NTP 计算本地偏移。

#### `util/Constant.py`

- **职责**：全局常量。
- **关键常量**：`BEIJING_TZ`、`BASE_URL`、`H2_LIMITS`、`H2_TIMEOUT`、`DEFAULT_REQUEST_INTERVAL`、`DEFAULT_CREATE_RETRY_LIMIT` 等。

#### `util/ErrorCodes.py`

- **职责**：B 站接口错误码映射。
- **关键类**：`ErrorCodes`
- **方法**：`format_attempt_result(err, ret)`：将错误码格式化为可读提示。

---

## 6. 关键类与函数速查

### 6.1 配置相关

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `BuyConfig` | `app_cmd/config/BuyConfig.py` | 抢票配置数据类 |
| `NotifierConfig` | `app_cmd/config/NotifierConfig.py` | 通知配置数据类 |
| `BasicConfig` | `app_cmd/config/ConfigBasic.py` | 配置基类 |
| `RuntimeOptions` | `interface/config.py` | 运行时选项 |
| `validate_config` | `interface/config.py` | 配置校验 |

### 6.2 请求相关

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `BiliRequest` | `util/request/BiliRequest.py` | B 站请求封装 |
| `CookieManager` | `util/request/CookieManager.py` | Cookie 与账号管理 |
| `generate_browser_fingerprint_state` | `util/request/BrowerState.py` | 浏览器指纹 |
| `generate_token` | `util/request/TokenUtil.py` | 下单 token |
| `gen_ctoken` | `util/CTokenUtilV2.py` | 下单 ctoken |

### 6.3 代理相关

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `ProxyManager` | `util/proxy/ProxyManager.py` | 代理池管理 |
| `ProxyStateRegistry` | `util/proxy/ProxyState.py` | 代理状态注册表 |
| `ProxyBackoff` | `util/proxy/ProxyBackoff.py` | 代理退避 |
| `test_proxy_connectivity` | `util/proxy/ProxyTester.py` | 代理测试 |
| `fetch_proxy_api` | `util/proxy/ProxyApiProvider.py` | 代理 API 拉取 |

### 6.4 抢票流程

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `Buy.buy()` | `task/buy.py` | 阻塞运行抢票 |
| `Buy.start_worker()` | `task/buy.py` | 启动事件 Worker |
| `buy_stream(config)` | `task/buy.py` | 核心抢票生成器 |
| `BuyStreamState` | `task/buy_types.py` | 抢票状态 |
| `BuyStreamWorker` | `task/buy_types.py` | 事件流 Worker |
| `build_payment_result` | `task/buy_helpers.py` | 构建支付结果 |

### 6.5 任务管理

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `GlobalStatus` | `util/__init__.py` | 全局状态单例 |
| `TaskLogEntry` | `util/__init__.py` | 任务日志条目 |
| `start_buy` | `interface/execution.py` | 启动内存任务 |
| `start_managed_buy` | `interface/execution.py` | 启动子进程任务 |
| `main(run_dir_arg)` | `interface/managed_runner.py` | 子进程入口 |

### 6.6 通知

| 类/函数 | 位置 | 说明 |
|---------|------|------|
| `NotifierManager` | `util/notifer/Notifier.py` | 通知管理器 |
| `NotifierBase` | `util/notifer/Notifier.py` | 通知基类 |
| `AudioNotifier` | `util/notifer/AudioUtil.py` | 音频通知 |
| `ServerChanNotifier` | `util/notifer/ServerChanUtil.py` | Server酱 |
| `BarkNotifier` | `util/notifer/BarkUtil.py` | Bark |

---

## 7. 依赖关系

### 7.1 模块调用关系

```
main.py
  ├── app_cmd.buy.buy_cmd ──► task.buy.Buy
  │                            ├── util.request.BiliRequest
  │                            ├── util.proxy.ProxyManager
  │                            ├── util.notifer.NotifierManager
  │                            └── util.TimeUtil / Constant / ErrorCodes
  └── app_cmd.ticker.ticker_cmd ──► tab.*
                                   ├── interface.auth / project / search / config / execution
                                   ├── task.buy.buy_new_terminal
                                   ├── util.ConfigDB / GlobalStatusInstance
                                   └── util.log.LogWeb.attach_log_routes
```

### 7.2 核心依赖说明

- `task/buy.py` 依赖 `BiliRequest`、`ProxyManager`、`NotifierManager`、`TimeUtil`、`ErrorCodes`、`CTokenUtilV2`。
- `BiliRequest` 依赖 `CookieManager`、`ProxyManager`、`BrowerState`。
- `interface/execution.py` 依赖 `validate_config`、`BuyConfig`、`managed_runner.main`。
- `tab/go.py` 依赖 `BuyConfig`、`task.buy.buy_new_terminal`、`GlobalStatusInstance`、`tab.log`。
- `tab/settings.py` 依赖 `interface.project.fetch_project_payload`、`BiliRequest`、`CookieManager`。
- 全局状态 `util/__init__.py` 初始化 `ConfigDB`、`main_request`、`time_service`、`GlobalStatusInstance`。

---

## 8. 项目运行方式

### 8.1 环境要求

- Python >= 3.11
- 安装依赖：`pip install -r requirements.txt` 或 `pip install -e .`

### 8.2 启动 Gradio Web UI（默认）

```bash
python main.py
# 或显式
python main.py ui
# 指定端口
python main.py ui --port 7860 --server-name 0.0.0.0
```

### 8.3 命令行抢票

```bash
python main.py buy --config-file ./ticket_config.json
# 或直接传入 JSON
python main.py buy --tickets-info '{"project_id": 84096, ...}'
```

### 8.4 打包可执行文件

项目提供 `main.spec`，使用 PyInstaller 打包：

```bash
pyinstaller main.spec
```

### 8.5 Docker 运行

项目提供 `Dockerfile` 与 `docker-compose.yml`，Docker 环境下 `ticker_cmd` 会自动启用 `share` 模式。

### 8.6 环境变量

| 变量 | 说明 |
|------|------|
| `BTB_TIME_START` | 默认抢票开始时间 |
| `BTB_INTERVAL` | 默认抢票间隔（毫秒） |
| `BTB_LOG_DIR` | 日志目录 |
| `BTB_APP_LOG_NAME` | 应用日志文件名 |
| `BTB_CONFIG_PATH` | 配置文件（TinyDB）路径 |
| `BTB_COOKIES_PATH` | Cookie 文件路径 |
| `BTB_SERVER_NAME` | UI 监听地址 |
| `BTB_PORT` / `GRADIO_SERVER_PORT` | UI 端口 |
| `BTB_ROOT_PATH` / `GRADIO_ROOT_PATH` | UI 根路径 |
| `BTB_PARENT_PID` | 子进程看门狗父进程 PID |
| `BTB_HOLD_TERMINAL` | 抢票结束后保持终端（1 启用） |
| `BTB_DOCKER` | Docker 环境标记 |
| `BTB_CHILD_PROCESS` | 标记当前为子进程，禁用终端渲染 |

---

## 9. 配置说明

### 9.1 抢票配置字段示例

| 字段 | 类型 | 说明 |
|------|------|------|
| `project_id` | int | 项目 ID |
| `screen_id` | int | 场次 ID |
| `ticket_id` | int | 票档 ID |
| `count` | int | 购买数量 |
| `pay_money` | int | 单价（分） |
| `buyer_info` | list | 实名购票人信息 |
| `addr_id` | int | 收货地址 ID（实体票） |
| `detail` | str | 任务描述 |
| `time_start` | str | 抢票开始时间 `YYYY-MM-DD HH:MM:SS` |

### 9.2 全局配置存储

- 使用 TinyDB 文件（默认 `config.json`）持久化。
- 由 `util/Storage/KVDatabase.py` 封装，通过 `util.ConfigDB` 全局访问。

---

## 10. 扩展与维护提示

- **新增通知渠道**：继承 `util/notifer/Notifier.py` 中的 `NotifierBase`，在 `NotifierManager.create_from_config` 中注册。
- **新增标签页**：在 `tab/` 下实现组件函数，在 `app_cmd/ticker.py` 的 `ticker_cmd` 中使用 `gr.Tab` 注册。
- **修改抢票流程**：核心逻辑集中在 `task/buy.py`，辅助函数在 `task/buy_helpers.py`，状态事件在 `task/buy_types.py`。
- **代理策略扩展**：在 `util/proxy/ProxyManager.py` 与 `ProxyStateRegistry` 中调整切换逻辑。
