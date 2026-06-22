# biliTickerBuy 新人入职指南

> 本指南由知识图谱自动生成，帮助新开发者快速理解项目架构和核心流程。

---

## 项目概览

| 属性 | 值 |
|------|-----|
| **项目名称** | biliTickerBuy |
| **描述** | 开源免费的 B 站会员购抢票辅助工具 |
| **主要语言** | Python |
| **框架** | Gradio (Web UI)、Pydantic (数据验证)、httpx (HTTP/2)、Textual/Rich (终端 UI)、tyro (CLI)、TinyDB (配置存储)、Loguru (日志) |
| **入口命令** | `btb` (定义在 pyproject.toml) |

### 项目定位

biliTickerBuy 是一个 B 站会员购抢票工具，支持两种使用方式：
1. **命令行模式** — 直接在终端执行抢票任务
2. **Web UI 模式** — 启动 Gradio 界面进行可视化操作

---

## 架构层次

项目采用清晰的分层架构，共 11 层：

```
┌─────────────────────────────────────────────────────┐
│                   应用入口层                         │
│            main.py / app_version.py                 │
├─────────────────────────────────────────────────────┤
│    命令行接口层 (app_cmd/)    │   Gradio Web UI 层  │
│    buy.py / ticker.py        │   tab/ (6个标签页)    │
├─────────────────────────────────────────────────────┤
│                   业务接口层                         │
│         interface/ (认证/项目/配置/执行/搜索)        │
├─────────────────────────────────────────────────────┤
│                   任务执行层                         │
│           task/ (buy.py 核心抢票流程)                │
├─────────────────────────────────────────────────────┤
│                    工具层                            │
│  日志 │ 通知(7渠道) │ 代理管理 │ HTTP客户端 │ 存储   │
├─────────────────────────────────────────────────────┤
│            浏览器指纹与 Token 生成层                 │
│                cptoken/ (反风控核心)                 │
└─────────────────────────────────────────────────────┘
```

### 各层职责

| 层 | 目录 | 职责 |
|----|------|------|
| **应用入口层** | `main.py`, `app_version.py`, `app_update.py` | 程序入口、版本管理、自动更新 |
| **命令行接口层** | `app_cmd/` | CLI 参数解析、配置数据类 |
| **Gradio Web UI 层** | `tab/` | 6 个标签页的 Web 界面 |
| **业务接口层** | `interface/` | 认证、项目查询、配置管理、任务执行 |
| **任务执行层** | `task/` | 核心抢票流程、BWS 预约 |
| **工具层** | `util/` | 日志、通知、代理、HTTP 客户端、存储 |
| **浏览器指纹层** | `cptoken/` | ctoken/ptoken 生成、反风控 |
| **静态资源层** | `assets/` | 图标、样式、更新脚本 |
| **文档层** | `docs/`, `README.md` | 安装指南、部署文档 |
| **测试层** | `tests/` | pytest 自动化测试 |
| **配置层** | 根目录 | pyproject.toml、Docker、Git 配置 |

---

## 关键概念

### 1. 配置体系 (BasicConfig 框架)

配置采用继承体系：
- `BasicConfig` — 基类，支持从环境变量、字典、配置 DB 多来源加载
- `BuyConfig` — 票务配置（项目 ID、票档、购买人等）
- `NotifierConfig` — 通知配置（各推送渠道参数）

### 2. 抢票流程 (buy_stream 生成器)

`task/buy.py` 中的 `buy_stream` 是核心，采用生成器模式 yield 状态事件：

```
初始化 → 等待开票(NTP校时) → prepare获取token → create订单 → 重试/终止 → 通知 → 支付二维码
```

### 3. 反风控机制

- **BiliRequest** — HTTP 客户端，注入浏览器指纹
- **BrowerState** — 生成随机化的浏览器参数（navigator、WebGL 等）
- **cptoken** — 模拟用户行为计数器，生成 ctoken/ptoken

### 4. 代理管理

三层代理系统：
- `ProxyManager` — 解析代理列表并轮换
- `ProxyStateRegistry` — 追踪故障和冷却状态
- `ProxyBackoff` — 指数退避计算等待时间

### 5. 通知系统

7 种推送渠道，统一通过 `NotifierBase` 基类调度：
- ServerChan (微信)
- PushPlus (微信)
- Bark (iOS)
- ntfy
- MeoW
- 飞书 Webhook
- 本地音频播放

---

## 引导式学习路径

按照以下顺序阅读代码，逐步深入理解项目：

### 第 1 步：项目概览与配置
**阅读**: `README.md`, `pyproject.toml`

了解项目定位、依赖和构建配置。

### 第 2 步：程序入口与命令分发
**阅读**: `main.py`

程序使用 tyro 解析 CLI 参数，支持 `buy`（命令行抢票）和 `ui`（Web 界面）两个子命令。

### 第 3 步：命令行接口层与配置体系
**阅读**: `app_cmd/buy.py`, `app_cmd/ticker.py`, `app_cmd/config/`

CLI 层包含两种模式：buy_cmd 直接抢票，ticker_cmd 启动 Web UI。

### 第 4 步：Gradio Web UI 界面
**阅读**: `tab/` 目录下各模块

6 个标签页：settings（登录与配置）、go（抢票操作）、config（高级设置）、log（日志）、share（分享）、bws（BWS 预约）。

### 第 5 步：业务接口层
**阅读**: `interface/` 目录

封装认证、票务查询、配置管理、任务执行等核心业务逻辑。

### 第 6 步：核心抢票流程
**阅读**: `task/buy.py`, `task/buy_helpers.py`, `task/buy_types.py`

`buy_stream` 生成器是整个项目的灵魂，理解完整的抢票状态机。

### 第 7 步：HTTP 请求客户端
**阅读**: `util/request/BiliRequest.py`, `util/request/BrowerState.py`

BiliRequest 集成 Cookie 同步、代理轮换、浏览器指纹注入。

### 第 8 步：浏览器指纹与 Token 生成
**阅读**: `cptoken/__init__.py`

反风控核心模块，生成 ctoken/ptoken 用于请求校验。

### 第 9 步：代理管理
**阅读**: `util/proxy/` 目录

代理轮换、状态追踪、指数退避策略。

### 第 10 步：通知系统
**阅读**: `util/notifer/` 目录

7 种推送渠道的实现和统一调度。

### 第 11 步：日志与终端渲染
**阅读**: `util/log/` 目录

Loguru 配置、Web 日志查看、终端渲染器。

### 第 12 步：全局状态与存储
**阅读**: `util/__init__.py`, `util/Storage/`

GlobalStatus 状态管理、KVDatabase 配置存储。

---

## 文件地图

### 应用入口层
| 文件 | 功能 |
|------|------|
| `main.py` | 程序主入口，CLI 参数解析和命令分发 |
| `app_version.py` | 版本号读取（从 pyproject.toml） |
| `app_update.py` | 自动更新检查和下载 |
| `__init__.py` | 根包初始化，导出 interface API |

### 命令行接口层 (app_cmd/)
| 文件 | 功能 |
|------|------|
| `app_cmd/buy.py` | 命令行抢票入口 |
| `app_cmd/ticker.py` | Web UI 启动入口 |
| `app_cmd/cli_args.py` | CLI 参数定义 (TickerCliArgs) |
| `app_cmd/config/BuyConfig.py` | 票务配置数据类 |
| `app_cmd/config/ConfigBasic.py` | 配置基类，支持多来源加载 |
| `app_cmd/config/NotifierConfig.py` | 通知配置数据类 |

### Gradio Web UI 层 (tab/)
| 文件 | 功能 |
|------|------|
| `tab/settings.py` | 登录与票务配置标签页 |
| `tab/go.py` | 抢票操作与任务管理标签页 |
| `tab/config.py` | 高级设置标签页 |
| `tab/log.py` | 日志查看与任务控制标签页 |
| `tab/share.py` | 分享选票标签页（含 FastAPI 服务） |
| `tab/bws.py` | BWS 活动预约标签页 |

### 业务接口层 (interface/)
| 文件 | 功能 |
|------|------|
| `interface/auth.py` | 认证（二维码登录/Cookie 登录） |
| `interface/project.py` | 票务项目查询（新旧 API 自动降级） |
| `interface/config.py` | 配置管理与校验 |
| `interface/execution.py` | 任务执行与托管 |
| `interface/search.py` | 票务搜索 |
| `interface/common.py` | 公共工具函数 |
| `interface/types.py` | 类型定义 (ValidationResult, BuyTaskRecord) |

### 任务执行层 (task/)
| 文件 | 功能 |
|------|------|
| `task/buy.py` | 核心抢票流程 (buy_stream 生成器) |
| `task/buy_helpers.py` | 辅助函数（倒计时、token 构建、代理处理） |
| `task/buy_types.py` | 流式状态机类型定义 |
| `task/bws.py` | BWS 预约流程 |

### 工具层 (util/)
| 文件 | 功能 |
|------|------|
| `util/__init__.py` | 全局状态管理、应用路径、单例初始化 |
| `util/request/BiliRequest.py` | B 站 HTTP 请求客户端 |
| `util/request/BrowerState.py` | 浏览器指纹生成 |
| `util/request/CookieManager.py` | Cookie 管理 |
| `util/notifer/Notifier.py` | 通知基类和管理器 |
| `util/proxy/ProxyManager.py` | 代理管理 |
| `util/proxy/ProxyState.py` | 代理状态追踪 |
| `util/proxy/ProxyBackoff.py` | 指数退避策略 |
| `util/log/LogConfig.py` | Loguru 日志配置 |
| `util/log/LogWeb.py` | Web 日志查看（SSE） |
| `util/log/TerminalRenderer.py` | 终端渲染器（Plain/Textual） |
| `util/Storage/KVDatabase.py` | KV 配置数据库 |
| `util/TimeUtil.py` | 时间工具（NTP 校时） |
| `util/ErrorCodes.py` | 错误码定义 |

---

## 复杂度热点

以下区域代码复杂度较高，新人应谨慎阅读：

| 文件 | 复杂度 | 说明 |
|------|--------|------|
| `tab/settings.py` | 高 | 最大的 UI 模块（1317 行），包含票务选择、登录、配置等多项功能 |
| `task/buy.py` | 高 | 核心抢票流程，状态机逻辑复杂 |
| `tab/log.py` | 中高 | 任务管理和日志渲染，涉及进程控制 |
| `interface/execution.py` | 中高 | 任务执行与托管，涉及子进程管理 |
| `util/request/BrowerState.py` | 中高 | 浏览器指纹生成，需要理解反风控机制 |
| `cptoken/__init__.py` | 中高 | Token 生成逻辑，涉及行为模拟 |

---

## 快速开始

### 安装

```bash
# 方式 1: pip 安装
pip install bilitickerbuy

# 方式 2: 源码安装
git clone https://github.com/mikumifa/biliTickerBuy.git
cd biliTickerBuy
pip install -e .

# 方式 3: Docker
docker-compose up -d
```

### 运行

```bash
# 启动 Web UI（默认）
btb

# 命令行抢票
btb buy --config config.json

# 指定端口启动 Web UI
btb ui --port 7860
```

### 配置文件

- `config.json` — 主配置文件（TinyDB 格式）
- `cookies.json` — B 站账号 Cookies
- `people.json` — 购票人信息

---

## 测试

```bash
# 运行所有测试
pytest

# 运行特定测试
pytest tests/test_buy_prepare_token.py

# 查看测试覆盖率
pytest --cov
```

---

## 常见问题

### Q: 如何添加新的通知渠道？
1. 在 `util/notifer/` 创建新文件，继承 `NotifierBase`
2. 实现 `send_message` 方法
3. 在 `util/notifer/Notifier.py` 的 `NotifierManager.create_from_config` 中注册

### Q: 如何修改抢票流程？
核心流程在 `task/buy.py` 的 `buy_stream` 生成器中，按状态机模式组织。修改时注意保持 yield 事件的兼容性。

### Q: 如何添加新的 CLI 命令？
1. 在 `app_cmd/` 创建新模块
2. 在 `main.py` 的 `_normalize_argv` 中添加命令映射

---

*本指南基于知识图谱自动生成，最后更新于 2026-06-22。*
