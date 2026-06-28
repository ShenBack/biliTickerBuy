"""
tab/__init__.py — UI 标签页模块包初始化文件。

文件整体功能：
  标识当前目录为 Python 包，对外暴露该包的整体作用说明。
  本包集中存放 biliTickerBuy 项目基于 Gradio 的各功能标签页页面，
  包括账号登录、配置生成、抢票操作、日志管理、BWS 预约、分享等入口。

所属模块：
  UI 层 (tab)

依赖文件：
  - 包内各子模块：bws.py / config.py / go.py / log.py / settings.py / share.py

对外能力：
  - 作为 Python 包被导入，供 ticker.py 统一注册各个 Gradio 标签页。
"""
