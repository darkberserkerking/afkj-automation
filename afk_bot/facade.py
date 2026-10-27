"""
afk_bot facade 模块

AFKBot 薄 facade 类，内部创建 AFKBotCore 实例并委托所有操作。
保持与旧版本 API 的完全向后兼容。

从 afk_bot.py 顶层提取到包内，支持 `from afk_bot import AFKBot`。
"""

import os
import time
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from afk_bot.auto_farm.loop import AFKBotCore
from afk_bot.infra.ocr_memory import OCRStrategy, OCRMemory
from afk_bot.config import (
    _SNAP_TRIM, _BTN_POS, _BTN_POS_PORTRAIT,
)


class AFKBot:
    """AFK Journey 自动推图核心逻辑 — 薄 facade 层。

    内部创建 AFKBotCore 实例并委托所有操作。
    保持与旧版本 API 的完全向后兼容。

    状态机架构见 afk_bot.auto_farm.loop.AFKBotCore 文档。
    """

    def __init__(self, config: Dict[str, Any]):
        """初始化 AFKBot facade。

        创建内部 AFKBotCore 实例，将所有属性代理到 Core。

        Args:
            config: 配置字典，需包含：
                - log_callback: 日志回调
                - status_callback: GUI 状态回调
        """
        # 创建核心实例
        self._core: AFKBotCore = AFKBotCore(config)

        # 代理核心的 threading.Event（同一对象引用，保证同步）
        self._stop_event: threading.Event = self._core._stop_event
        self._pause: threading.Event = self._core._pause

        # 代理日志和状态
        self.log: Any = self._core.log
        self.set_status: Any = self._core.set_status

        # 代理 stats（同一 dict 引用）
        self.stats: Dict[str, Any] = self._core.stats

        # 代理 config（同一 dict 引用）
        self.config: Dict[str, Any] = self._core.config

        # 代理配置常量（v1.5.0：仅保留战斗/导航超时）
        self.navigate_timeout: int = self._core.navigate_timeout
        self.battle_timeout: int = self._core.battle_timeout

        # 代理 MaaOCR 和 OCRMemory（保持向后兼容）
        self._ocr: Any = self._core._ocr
        self._ocr_memory: OCRMemory = self._core._ocr_memory

        # 代理配置文件路径
        self.config_file: str = self._core.config_file

        # 调试目录（兼容旧代码）
        self._debug_dir: str = os.path.join(
            os.environ.get('TEMP', '/tmp'), 'AFKJ_debug'
        )
        os.makedirs(self._debug_dir, exist_ok=True)

        # 未推进计数（从 Core 读取）
        self.manual_no_progress_count: int = 0

    # ── _stop 属性同步 ─────────────────────────────────────

    @property
    def _stop(self) -> bool:
        """同步 _stop 标志（Core 可能通过 stop() 修改）。"""
        return self._core._stop

    @_stop.setter
    def _stop(self, value: bool) -> None:
        self._core._stop = value

    # ── _hwnd 属性同步 ─────────────────────────────────────

    @property
    def _hwnd(self) -> Optional[int]:
        return self._core._hwnd

    @_hwnd.setter
    def _hwnd(self, value: Optional[int]) -> None:
        self._core._hwnd = value

    # ── 资源路径 ────────────────────────────────────────────

    def _res_path(self, relative_path: str) -> str:
        """获取资源文件的绝对路径（兼容开发模式和 PyInstaller exe 模式）。"""
        return self._core._res_path(relative_path)

    # ── 生命周期 ────────────────────────────────────────────

    def run(self) -> None:
        """统一入口：调用 Core._run_main_loop()。"""
        self._core.run()

    def stop(self) -> None:
        """停止自动化。"""
        self._core.stop()

    def run_auto_farm(self, max_time: Optional[int] = None,
                      max_turns: Optional[int] = None,
                      start_turns: int = 0) -> str:
        """自动推图任务入口，委托 Core.run_auto_farm()。

        v1.5.0：模式已固定，无模式参数。
        v1.5.3：支持任务结束指标 max_time（秒）/ max_turns（轮），
        start_turns 为实例启动时的轮次基线（None=无限）。

        Returns:
            'done' / 'failed' / 'stopped'
        """
        return self._core.run_auto_farm(
            max_time=max_time, max_turns=max_turns, start_turns=start_turns
        )

    def recover_to_idle(self) -> bool:
        """恢复游戏到空闲（主）界面。

        供 TaskScheduler 在任务之间调用，做状态隔离，
        避免上一任务残留的界面影响下一任务的起始判定。
        """
        return self._core._recover_to_idle_stage()

    def pause(self) -> None:
        """暂停自动化。"""
        self._core.pause()

    def resume(self) -> None:
        """恢复自动化。"""
        self._core.resume()

    # ── 窗口管理 ────────────────────────────────────────────

    @property
    def last_snap_empty(self) -> bool:
        """最近一帧截图是否为空（窗口未就绪/最小化）——v2.0.2。"""
        try:
            return bool(getattr(self._core._screen_capture,
                                '_last_snap_empty', False))
        except Exception as _e:
            self.log(f"[调试] last_snap_empty() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return False

    def find_hwnd(self) -> Optional[int]:
        """枚举顶层窗口，查找 Unity 类名窗口。"""
        return self._core._screen_capture.find_hwnd()

    def find_and_focus(self) -> Optional[int]:
        """查找并聚焦 Unity 游戏窗口。"""
        return self._core.find_and_focus()

    def _win_dims(self) -> Tuple[int, int, int, int]:
        """获取窗口位置和尺寸，返回 (l, t, w, h)。"""
        return self._core._win_dims()

    def _snap(self) -> Tuple[Any, int, int, int, int]:
        """截图，返回 (img, l, t, w, h)。"""
        return self._core._snap()

    # ── 截图/OCR ────────────────────────────────────────────

    def _ocr_screen(
        self, img: Any = None, no_cache: bool = False
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """全屏 OCR 识别（no_cache=True 强制重识别，见 screen.ocr_screen）。"""
        return self._core._ocr_screen(img=img, no_cache=no_cache)

    def _ocr_img(
        self, img: Any, roi: Any = None
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """对 PIL Image 执行 OCR。"""
        return self._core._ocr_img(img, roi=roi)

    # ── 点击 & 按键 ──────────────────────────────────────────

    def click_at(self, cx: int, cy: int, label: str = '') -> None:
        """图像坐标点击。"""
        self._core.click_at(cx, cy, label=label)

    def click_ratio(
        self, rx: float, ry: float, label: str = ''
    ) -> bool:
        """比例坐标点击。"""
        return self._core.click_ratio(rx, ry, label=label)

    def click_ratio_dual(
        self,
        landscape: Tuple[float, float],
        portrait: Tuple[float, float],
        label: str = '',
    ) -> bool:
        """横竖屏自适应比例坐标点击。"""
        return self._core.click_ratio_dual(
            landscape, portrait, label=label
        )

    def long_press_at(
        self, cx: int, cy: int, duration: float = 2.5, label: str = ''
    ) -> None:
        """图像坐标长按（v1.8.62：终止确认弹窗「长按结束」）。"""
        self._core._input_controller.long_press_at(
            cx, cy, duration=duration, label=label
        )

    def long_press_ratio(
        self, rx: float, ry: float, duration: float = 2.5, label: str = ''
    ) -> None:
        """比例坐标长按。"""
        self._core._input_controller.long_press_ratio(
            rx, ry, duration=duration, label=label
        )

    def move_mouse_to(
        self, rx: float, ry: float, label: str = ''
    ) -> Tuple[int, int]:
        """移动鼠标到比例坐标（0.0~1.0，自动激活窗口）。

        返回移动后的屏幕绝对坐标 (screen_x, screen_y)。
        """
        return self._core._input_controller.move_mouse_to_ratio(
            rx, ry, label=label
        )

    def scroll_wheel(
        self,
        clicks: int,
        rx: Optional[float] = None,
        ry: Optional[float] = None,
        label: str = '',
    ) -> bool:
        """滚轮滚动。clicks 正数向上、负数向下。

        游戏滚动列表需「窗口已激活 + 鼠标在可滚动区内」才响应，
        故建议传入 rx/ry（可滚动区中心比例坐标）。
        """
        return self._core._input_controller.scroll_wheel(
            clicks, rx=rx, ry=ry, label=label
        )

    def _click_abs(self, cx: int, cy: int) -> None:
        """屏幕绝对坐标点击。"""
        self._core._click_abs(cx, cy)

    def _send_esc(self) -> None:
        """发送 ESC 按键。"""
        self._core._send_esc()

    def press_esc(self, label: str = '') -> None:
        """通过 SendMessage 发送 ESC 到游戏窗口（后台方式）。"""
        self._core._input_controller.press_esc(label=label)

    def press_f(self, label: str = '') -> None:
        """发送 F 按键到游戏窗口（v1.7.12：迷宫备战画面按 F 开始战斗）。"""
        self._core._input_controller.send_key_f(label=label)

    def screen_to_image(self, screen_x: int, screen_y: int) -> Tuple[int, int]:
        """屏幕绝对坐标 → 图像相对坐标（v1.8.1：模板命中点对齐 OCR 坐标系）。

        用途：模板匹配返回屏幕绝对坐标，OCR 标题是图像坐标——
        门卡选门（标题 x）+ 按钮点击（按钮 y）组合时需统一坐标系。
        """
        return self._core._input_controller._coord_system.screen_to_image(
            screen_x, screen_y
        )

    def click_back_button(
        self, tpl_paths: List[str], threshold: float = 0.7
    ) -> bool:
        """兜底返回（v1.7.5 新增）：多模板匹配任一返回按钮，命中即点击。

        ESC 失效（弹窗/特殊状态）时使用：依次尝试 tpl_paths，
        找到一个就点击，未找到返回 False。
        """
        return self._core._input_controller.click_back_button(
            tpl_paths=tpl_paths, threshold=threshold
        )

    def _check_pause(self) -> bool:
        """等待恢复暂停，返回 True 表示继续运行。"""
        return self._core._check_pause()

    def _screen_offset(self) -> Tuple[int, int]:
        """返回窗口左上角屏幕坐标 (l, t)。"""
        return self._core._screen_offset()

    def window_rect(self) -> Tuple[int, int, int, int]:
        """返回窗口客户区矩形 (l, t, w, h)（v1.8.78：模板 region 限界用）。

        template_match 的 region 参数是**屏幕绝对坐标**，任务层要按窗口
        比例圈搜索框就必须先拿到原点与尺寸。

        Returns:
            (l, t, w, h)：拿不到时返回 (0, 0, 0, 0)（调用方按此退化全屏）
        """
        try:
            coord = self._core._input_controller._coord_system
            l, t = coord.client_offset
            w, h = coord.window_size
            return (l, t, w, h)
        except Exception as _e:
            self.log(f"[调试] window_rect() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return (0, 0, 0, 0)

    # ── 文本查找 ────────────────────────────────────────────

    def find_text(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """模糊查找：keyword 包含在 OCR 文字中即匹配。"""
        return self._core.find_text(items, keyword, min_conf=min_conf)

    def find_text_exact(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配。"""
        return self._core.find_text_exact(
            items, keyword, min_conf=min_conf
        )

    def find_text_exclude(
        self,
        items: List[Tuple],
        keyword: str,
        exclude_keywords: List[str],
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配 keyword，排除 exclude_keywords。"""
        return self._core.find_text_exclude(
            items, keyword, exclude_keywords, min_conf=min_conf
        )

    def _find_text_impl(
        self,
        items: List[Tuple],
        keyword: str,
        exact: bool = False,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """底层文本查找实现。"""
        return self._core._find_text_impl(
            items, keyword, exact=exact, min_conf=min_conf
        )

    # ── 模板匹配 ────────────────────────────────────────────

    def template_match(
        self,
        tpl_path: str,
        threshold: float = 0.8,
        scale_range: Tuple[float, float, float] = (0.5, 2.0, 0.1),
        silent: bool = False,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """多尺度模板匹配。"""
        return self._core.template_match(
            tpl_path, threshold=threshold, scale_range=scale_range,
            silent=silent, region=region,
        )

    def click_template(
        self, name: str, conf: float = 0.8, label: str = ''
    ) -> bool:
        """按钮比例坐标点击（已弃用，保留兼容）。"""
        pos_landscape = _BTN_POS.get(name)
        pos_portrait = _BTN_POS_PORTRAIT.get(name)
        if pos_landscape and pos_portrait:
            return self.click_ratio_dual(
                pos_landscape, pos_portrait, label=label or name
            )
        elif pos_landscape:
            return self.click_ratio(
                pos_landscape[0], pos_landscape[1],
                label=label or name
            )
        self.log(f'[警告] 按钮位置未知 | {name}')
        return False

    # ── 阶段检测 ────────────────────────────────────────────

    def detect_phase(self) -> str:
        """识别当前游戏阶段。"""
        return self._core.detect_phase()

    def is_on_level0(self) -> bool:
        """检查是否在 0 层（世界/家园，函数名沿用旧称）。"""
        return self._core._phase_detector.is_on_level0()

    # ── 导航 ────────────────────────────────────────────────

    def screen_image(self):
        """截取游戏客户区原始图像（v1.9.15：供任务层做颜色判别）。"""
        return self._core._screen_capture.snap()

    def drag_abs(self, x0: int, y0: int, x1: int, y1: int,
                  duration: float = 0.4) -> None:
        """屏幕绝对坐标拖动（v1.9.20：滑条类 UI）。"""
        return self._core._input_controller.drag_abs(
            x0, y0, x1, y1, duration=duration)

    def navigate_to_idle_stage(self) -> bool:
        """赴战：导航到挂机关卡页（2级）。

        v1.9.11（导航统一化）：把 bot（Facade 自身）注入 Navigator，
        使其 `navigate_to_idle_stage` 优先走**全局导航系统**
        （GlobalNavigator / NAV_TARGETS['idle_stage']）；全局导航失败
        时 Navigator 内部自动降级回原有状态机，行为不回退。
        """
        nav = self._core._navigator
        if getattr(nav, '_bot', None) is None:
            nav._bot = self
        return nav.navigate_to_idle_stage()

    def esc_to_level0(self, max_esc: int = 5) -> bool:
        """ESC 连发回到 0 层（世界/家园）。"""
        return self._core._navigator.esc_to_level0(max_esc=max_esc)

    def wait_for_idle_stage(self, timeout: int = 30) -> bool:
        """等待挂机关卡/备战出现。"""
        return self._core._navigator.wait_for_idle_stage(timeout=timeout)

    # ── 网络检测 ────────────────────────────────────────────

    def _check_network_disconnect(self) -> bool:
        """检测并处理网络断开弹窗。"""
        return self._core._check_network_disconnect()

    # ── 恢复 ────────────────────────────────────────────────

    def _recover_to_idle_stage(self) -> bool:
        """恢复到挂机关卡流程。"""
        return self._core._recover_to_idle_stage()

    def _try_recover_from_stuck(self) -> bool:
        """卡住恢复：ESC 退回后重新导航。"""
        self._core._stats_tracker.increment('recovery_count')
        self._core._push_stats()
        self._core._screen_capture.debug_screenshot('stuck')
        self.log('[警告] 检测到画面无变化，尝试恢复...')
        self._core._navigator.esc_to_level0()
        self._stop_event.wait(1)
        return self._core._navigator.navigate_to_idle_stage()

    # ── 分辨率切换 ──────────────────────────────────────────

    def switch_resolution(self, target_mode: str) -> bool:
        """切换游戏分辨率到目标模式。"""
        return self._core.switch_resolution(target_mode)

    # ── 阵容 ────────────────────────────────────────────────

    def _switch_formation(self, times: int = 1) -> bool:
        """切换阵容（点'>'按钮 N 次）。"""
        return self._core._formation_manager.switch_formation(times=times)

    # ── 配置 ────────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件。"""
        return self._core._load_config()

    def _save_config(self) -> None:
        """保存当前配置到文件。"""
        self._core._save_config()

    # ── 调试截图 ────────────────────────────────────────────

    def _debug_screenshot(self, label: str = 'debug') -> Optional[str]:
        """保存当前画面的调试截图。"""
        return self._core._screen_capture.debug_screenshot(tag=label)

    # ── 推进统计 ────────────────────────────────────────────

    def _push_stats(self) -> None:
        """推送统计数据到 GUI。"""
        self._core._push_stats()

    def _detect_season_stages(self) -> Tuple[int, int]:
        """检测备战界面的赛季关卡数。"""
        return self._core._detect_season_stages()

    def _record_pre_battle_stage(
        self, current_mode: str, mode_prev_stage: Dict[str, int]
    ) -> None:
        """记录进入战斗前的赛季关卡。"""
        self._core._record_pre_battle_stage(
            current_mode, mode_prev_stage
        )

    # ── 通用阶段处理 ─────────────────────────────────────────

    def _handle_common_phase(self, phase: str) -> str:
        """处理通用阶段。"""
        return self._core._navigator._handle_common_phase(phase)

    # ── 任务系统兼容 ─────────────────────────────────────────

    def step_close_result(self, max_attempts: int = None) -> bool:
        """关闭战斗结算界面。"""
        return self._core._battle_monitor.step_close_result(
            max_attempts=max_attempts
        )

    def step_click_challenge_btn(
        self, mode: str = 'huanling', img: Any = None
    ) -> bool:
        """点击挑战按钮，进入备战界面。"""
        return self._core._formation_manager.ocr_click_challenge(mode)

    def _result_fallback_click(self) -> None:
        """结算界面兜底。"""
        return self._core._formation_manager.result_fallback_click()

    def _ocr_click_challenge(self, mode: str) -> bool:
        """用 OCR 找挑战按钮并点击。"""
        return self._core._formation_manager.ocr_click_challenge(mode)

    def _navigate_to_other_challenge(self, current_mode: str) -> None:
        """切回挂机关卡页并切换到另一项挑战。"""
        self._core._formation_manager.navigate_to_other_challenge(
            current_mode
        )

    def _click_challenge_btn(
        self, result: str, mode: str
    ) -> bool | str:
        """胜利/失败后点击按钮，回到备战界面。"""
        return self._core._formation_manager.click_challenge_btn(
            result, mode
        )

    def _prepare_and_start_battle(
        self, switch_count: int = 0
    ) -> bool:
        """备战界面操作：通关阵容 → [>×N] → 一键采用 → 战斗。"""
        return self._core._formation_manager.prepare_and_start_battle(
            switch_count=switch_count
        )

    def _handle_unowned_hero_dialog(self) -> bool:
        """检测并处理未拥有角色确认弹窗。"""
        return self._core._formation_manager.handle_unowned_hero_dialog()

    def _dismiss_confirm_dialog(self) -> None:
        """关闭"是否关闭自动挑战模式？"确认框。"""
        self._core._battle_monitor.dismiss_confirm_dialog()

    def _close_exit_dialog(self) -> None:
        """关闭"是否确认退出游戏？"弹窗。"""
        self._core._battle_monitor._close_exit_dialog()

    def _wait_for_manual_battle_end(self) -> str:
        """等待单次战斗结束。"""
        return self._core._battle_monitor.wait_for_manual_battle_end()

    def _activate_window(self) -> None:
        """激活游戏窗口到前台。"""
        self._core._screen_capture.activate_window()

    def _frame_fingerprint(self, img: Any = None) -> int:
        """快速画面指纹。"""
        return self._core._screen_capture.frame_fingerprint(img)
