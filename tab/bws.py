"""
tab/bws.py — BWS（Bilibili World/Show）活动预约标签页。

文件整体功能：
  提供 Gradio UI 的“BWS 活动预约”标签页，支持以下能力：
  1. 按日期查询可预约场次。
  2. 若账号未绑定门票，则引导用户提交票号与实名信息完成绑定。
  3. 选择目标场次后发起 BWS 预约请求。
  4. 集中展示请求日志，便于排查预约过程中的接口返回与异常。

所属模块：
  UI 层 (tab)

依赖文件：
  - task.bws (get_reserve_info / bind_ticket / reserve_bws / BwsRequestLog / 状态码常量)
  - util (main_request 全局请求对象)
  - gradio / loguru (UI 与日志)

对外能力：
  - bws_tab() → 构建并返回完整的 BWS 预约 Gradio Tab 组件与事件绑定。
"""

import gradio as gr
from loguru import logger
import util
from task.bws import (
    get_reserve_info, bind_ticket, reserve_bws,
    BwsRequestLog,
    BWS_RESERVE_SUCCESS, BWS_RESERVE_FAIL,
    TICKET_BIND_CODE,
)


def bws_tab():
    """
    构建“BWS 活动预约”标签页。

    核心作用：
      1. 使用 Gradio 组件搭建预约日期输入、请求延迟设置、
         查询/开始预约按钮、状态提示、门票绑定表单、场次选择器与日志展示区。
      2. 内嵌闭包函数处理查询、绑定、预约三类交互事件。
      3. 维护 BwsRequestLog 实例收集 task.bws 中的请求日志并回显到界面。
      4. 通过 Gradio 事件绑定将按钮/输入框与回调函数关联。

    输入参数：
      无。

    返回值：
      无（直接渲染 Gradio 组件并注册事件）。

    调用场景：
      由 ticker.py 在启动时注册为独立的 BWS 预约标签页。
    """
    gr.Markdown("## BWS 活动预约")

    with gr.Row():
        reserve_dates_ui = gr.Textbox(
            label="预约日期（逗号分隔，如 20250711,20250712）",
            placeholder="20250711,20250712,20250713",
            scale=3,
        )
        delay_ui = gr.Number(
            label="请求延迟（秒）",
            value=0.9,
            minimum=0.1,
            maximum=10,
            precision=1,
            scale=1,
        )

    with gr.Row():
        query_reserve_btn = gr.Button("1. 查询预约场次", variant="secondary", scale=1)
        start_reserve_btn = gr.Button("2. 开始预约", variant="primary", scale=1)

    status_ui = gr.Textbox(label="状态", interactive=False, lines=2)

    with gr.Group(visible=False) as bind_group:
        gr.Markdown("### 绑定门票")
        with gr.Row():
            bind_ticket_no_ui = gr.Textbox(label="票号后四位", scale=1)
            bind_name_ui = gr.Textbox(label="姓名", scale=1)
        with gr.Row():
            bind_card_type_ui = gr.Dropdown(
                label="证件类型",
                choices=[("身份证", 0), ("护照", 1), ("港澳通行证", 2), ("台湾通行证", 3)],
                value=0,
                scale=1,
            )
            bind_card_no_ui = gr.Textbox(label="证件号码", scale=2)
        bind_btn = gr.Button("提交绑定", variant="primary")
        bind_status_ui = gr.Textbox(label="绑定结果", interactive=False)

    with gr.Group(visible=False) as reserve_group:
        gr.Markdown("### 选择预约场次")
        reserve_dropdown_ui = gr.Dropdown(
            label="可预约场次",
            choices=[],
            interactive=True,
        )
        ticket_no_ui = gr.Textbox(label="票号（留空则自动获取）", placeholder="留空自动获取")

    # 存储预约数据（label -> item 的映射）
    reserve_data_state = gr.State({})

    # 请求日志展示区域
    with gr.Group():
        with gr.Row():
            gr.Markdown("### 请求日志", scale=8)
            clear_log_btn = gr.Button("清除日志", variant="secondary", scale=1, size="sm")
        log_ui = gr.Textbox(label="", interactive=False, lines=12, max_lines=30)

    # 全局日志收集器，用于汇总 task.bws 中的请求记录
    _bws_log = BwsRequestLog()

    def on_query_reserve(dates):
        """
        “查询预约场次”按钮回调。

        核心作用：
          1. 清空历史日志并校验日期输入是否为空。
          2. 调用 task.bws.get_reserve_info() 拉取指定日期的预约场次。
          3. 若返回码为 75638，说明门票未绑定，显示绑定表单。
          4. 若查询失败，展示错误信息并隐藏相关表单。
          5. 查询成功时，将场次列表构建为下拉选项，默认选中第一个“可预约”状态项。

        输入参数：
          dates : str — 用户输入的预约日期，多个日期以逗号分隔，如 "20250711,20250712"。

        返回值：
          tuple — (状态文本, 绑定表单可见性, 场次选择区可见性, 下拉框更新, 场次映射字典, 日志文本)。

        调用场景：
          query_reserve_btn 按钮点击时触发。
        """
        _bws_log.clear()
        if not dates or not dates.strip():
            gr.Warning("请输入预约日期")
            return (
                "请输入预约日期",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(choices=[]),
                {},
                _bws_log.get_text(),
            )

        try:
            info = get_reserve_info(util.main_request, dates.strip(), log=_bws_log)
            code = info["code"]

            if code == 75638:
                return (
                    "门票未绑定，请先绑定门票",
                    gr.update(visible=True),
                    gr.update(visible=False),
                    gr.update(choices=[]),
                    {},
                    _bws_log.get_text(),
                )

            if not info["success"]:
                gr.Warning(f"查询失败: {info['message']}")
                return (
                    f"查询失败: {info['message']}",
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(choices=[]),
                    {},
                    _bws_log.get_text(),
                )

            data = info["data"]
            reserve_list = data.get("reserve_list", {})
            all_reserves = []
            for date_key, items in reserve_list.items():
                if isinstance(items, list):
                    for item in items:
                        all_reserves.append(item)

            if not all_reserves:
                return (
                    "未查询到可预约场次",
                    gr.update(visible=False),
                    gr.update(visible=False),
                    gr.update(choices=[]),
                    {},
                    _bws_log.get_text(),
                )

            # 构建下拉选项：label 包含活动名、状态、票号，reserve_map 用于反查原始数据
            choices = []
            reserve_map = {}
            for item in all_reserves:
                rid = item.get("reserve_id") or item.get("inter_reserve_id")
                name = item.get("act_title") or item.get("reserve_name", "未知")
                state = item.get("state", -1)
                state_text = {1: "可预约", 2: "未开始", 3: "已结束", 4: "已预约", 5: "售完"}.get(state, f"状态{state}")
                ticket_no = item.get("ticket_no", "")
                label = f"{name} | {state_text} | 票号:{ticket_no}"
                choices.append(label)
                reserve_map[label] = item

            # 默认选中第一个 state=1（可预约）的场次，没有则选第一个
            default_idx = 0
            for i, item in enumerate(all_reserves):
                if item.get("state") == 1:
                    default_idx = i
                    break

            gr.Info(f"查询到 {len(all_reserves)} 个场次")
            return (
                f"查询到 {len(all_reserves)} 个场次",
                gr.update(visible=False),
                gr.update(visible=True),
                gr.update(choices=choices, value=choices[default_idx] if choices else None),
                reserve_map,
                _bws_log.get_text(),
            )
        except Exception as e:
            logger.error(f"[BWS] 查询预约信息异常: {e}")
            gr.Error(f"查询异常: {e}")
            return (
                f"查询异常: {e}",
                gr.update(visible=False),
                gr.update(visible=False),
                gr.update(choices=[]),
                {},
                _bws_log.get_text(),
            )

    def on_bind(ticket_no, name, card_type, card_no):
        """
        “提交绑定”按钮回调。

        核心作用：
          1. 校验票号、姓名、证件号是否完整。
          2. 调用 task.bws.bind_ticket() 向 B 站提交门票绑定请求。
          3. 根据绑定结果显示成功提示或警告，并返回日志文本。

        输入参数：
          ticket_no : str — 票号后四位。
          name      : str — 持票人姓名。
          card_type : int — 证件类型（0 身份证 / 1 护照 / 2 港澳通行证 / 3 台湾通行证）。
          card_no   : str — 证件号码。

        返回值：
          tuple — (绑定结果文本, 日志文本)。

        调用场景：
          bind_btn 按钮点击时触发。
        """
        _bws_log.clear()
        if not all([ticket_no, name, card_no]):
            gr.Warning("请填写完整绑定信息")
            return "请填写完整绑定信息", _bws_log.get_text()
        try:
            result = bind_ticket(util.main_request, {
                "ticket_no": ticket_no,
                "card_type": card_type,
                "card_no": card_no,
                "name": name,
            }, log=_bws_log)
            if result["success"]:
                gr.Info("绑定成功！")
            else:
                gr.Warning(result["message"])
            return result["message"], _bws_log.get_text()
        except Exception as e:
            logger.error(f"[BWS] 绑定异常: {e}")
            gr.Error(f"绑定异常: {e}")
            return f"绑定异常: {e}", _bws_log.get_text()

    def on_start_reserve(dates, delay, reserve_label, ticket_no, reserve_map):
        """
        “开始预约”按钮回调。

        核心作用：
          1. 校验日期输入。
          2. 根据用户在下拉框中选择的 label，从 reserve_map 中反查目标 reserve_id 与票号。
          3. 若用户未手动填写票号，则自动使用选中场次自带的 ticket_no。
          4. 调用 task.bws.reserve_bws() 执行预约，并刷新日志展示。

        输入参数：
          dates          : str — 预约日期（逗号分隔）。
          delay          : float — 请求延迟秒数，默认 0.9。
          reserve_label  : str — 用户在 dropdown 中选中的场次 label。
          ticket_no      : str — 用户手动输入的票号，留空则自动获取。
          reserve_map    : dict — label 到场次原始字典的映射（由 on_query_reserve 生成）。

        返回值：
          tuple — (预约结果文本, 日志文本)。

        调用场景：
          start_reserve_btn 按钮点击时触发。
        """
        _bws_log.clear()
        if not dates or not dates.strip():
            gr.Warning("请输入预约日期")
            return "请输入预约日期", _bws_log.get_text()

        # 解析选中的预约项，获取 reserve_id 与票号
        target_id = 0
        target_ticket = ticket_no or ""
        if reserve_label and reserve_label in reserve_map:
            item = reserve_map[reserve_label]
            target_id = item.get("reserve_id") or item.get("inter_reserve_id", 0)
            if not target_ticket:
                target_ticket = item.get("ticket_no", "")

        try:
            success, msg = reserve_bws(
                request=util.main_request,
                reserve_dates=dates.strip(),
                target_reserve_id=target_id,
                target_ticket_no=target_ticket,
                delay=float(delay) if delay else 0.9,
                progress_callback=lambda m: logger.info(f"[BWS] {m}"),
                log=_bws_log,
            )
            if success:
                gr.Info(f"预约成功: {msg}")
            else:
                gr.Warning(f"预约失败: {msg}")
            return msg, _bws_log.get_text()
        except Exception as e:
            logger.error(f"[BWS] 预约异常: {e}")
            gr.Error(f"预约异常: {e}")
            return f"预约异常: {e}", _bws_log.get_text()

    # 事件绑定：查询、绑定、开始预约、清除日志
    query_reserve_btn.click(
        fn=on_query_reserve,
        inputs=[reserve_dates_ui],
        outputs=[status_ui, bind_group, reserve_group, reserve_dropdown_ui, reserve_data_state, log_ui],
    )

    bind_btn.click(
        fn=on_bind,
        inputs=[bind_ticket_no_ui, bind_name_ui, bind_card_type_ui, bind_card_no_ui],
        outputs=[bind_status_ui, log_ui],
    )

    start_reserve_btn.click(
        fn=on_start_reserve,
        inputs=[reserve_dates_ui, delay_ui, reserve_dropdown_ui, ticket_no_ui, reserve_data_state],
        outputs=[status_ui, log_ui],
    )

    clear_log_btn.click(
        fn=lambda: (_bws_log.clear(), ""),
        outputs=[log_ui],
    )
