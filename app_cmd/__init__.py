"""
app_cmd/__init__.py — biliTickerBuy 命令行入口包初始化文件。

文件整体功能：
  将 app_cmd 目录标识为 Python 包，统一承载并暴露命令行子命令的入口能力。
  当前包主要提供 ticker 子命令的启动入口，负责拉起 Gradio Web UI。

所属模块：
  CLI 命令层 (app_cmd)

依赖文件：
  - app_cmd.ticker    (ticker_cmd 函数，构建并启动 Web UI)
  - app_cmd.cli_args  (TickerCliArgs / BuyCliArgs 参数数据类)

对外能力：
  - 供上层 main.py 通过 `from app_cmd import ...` 或 `app_cmd.ticker_cmd` 方式引用本包命令能力。
  - 作为包级初始化文件，维护命令层模块的聚合入口。
"""
