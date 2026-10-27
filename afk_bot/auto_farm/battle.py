"""
afk_bot 战斗监控模块

BattleMonitor 封装战斗等待、结算关闭、确认框处理等逻辑。
从 afk_bot.py 提取，去除对 AFKBot 的直接依赖，改为构造器注入。

设计原则：
- 所有截图/OCR/点击/检测操作通过注入的 screen_capture/recognizer/
  input_controller/detector 完成
- 停止/暂停检查通过 stop_check/pause_check 回调完成
- 统计推送通过 stats_tracker 完成
- 日志/状态通过 log_callback/status_callback 完成
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import pyautogui

from afk_bot.infra.timing import sleep_interruptible


class BattleMonitor:
    """战斗监控器 — 等待战斗结束、关闭结算界面、处理弹窗。

    封装自动战斗和逐次战斗的等待逻辑，处理战斗结果界面
    的关闭（ESC、空白处点击、确认框取消等）。

    Attributes:
        _screen: ScreenCapture 实例
        _recognizer: TextRecognizer 实例
        _detector: PhaseDetector 实例
        _input: InputController 实例
        _stats: StatsTracker 实例
        _log: 日志回调
        _set_status: 状态回调
        _config: 配置字典
        _stop_check: 停止检查回调
        _pause_check: 暂停检查回调
    """

    def __init__(
        self,
        screen_capture: Any,
        recognizer: Any,
        detector: Any,
        input_controller: Any,
        stats_tracker: Any,
        log_callback: Optional[Callable[[str], None]] = None,
        status_callback: Optional[Callable[..., None]] = None,
        config: Optional[Dict[str, Any]] = None,
        stop_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
        battle_mode: str = 'manual',
    ):
        """初始化 BattleMonitor。

        Args:
            screen_capture: ScreenCapture 实例，提供 snap(), ocr_screen(),
                            _ocr_img(), frame_fingerprint(), debug_screenshot(),
                            activate_window(), hwnd 属性
            recognizer: TextRecognizer 实例，提供 find_text() 等
            detector: PhaseDetector 实例，提供 detect_phase()
            input_controller: InputController 实例，提供 click_abs(),
                              click_ratio_dual(), send_esc()
            stats_tracker: StatsTracker 实例，提供 push()
            log_callback: 日志回调
            status_callback: 状态回调
            config: 配置字典（含 battle 子配置）
            stop_check: 停止检查回调，返回 True 表示需要停止
            pause_check: 暂停检查回调，返回 True 表示继续，False 表示暂停
            battle_mode: 战斗模式（v1.5.0 固定 'manual'，保留参数兼容）
        """
        self._screen: Any = screen_capture
        self._recognizer: Any = recognizer
        self._detector: Any = detector
        self._input: Any = input_controller
        self._stats: Any = stats_tracker
        self._log: Callable[[str], None] = log_callback or (lambda msg: None)
        self._set_status: Callable[..., None] = (
            status_callback or (lambda **kw: None)
        )
        self._config: Dict[str, Any] = config or {}
        self._stop_check: Callable[[], bool] = stop_check or (lambda: False)
        self._pause_check: Callable[[], bool] = pause_check or (lambda: True)
        self._battle_mode: str = battle_mode
        # 最近一次结算结果（step_close_result 检测到胜负时更新），
        # 供 core 判断 _pending_defeat 是否真实失败，
        # 避免胜利结算被 _handle_common_phase 抢先时被误判为失败换阵
        self.last_result: Optional[str] = None

    # ── 内部辅助 ────────────────────────────────────────────────

    def _should_stop(self) -> bool:
        """检查是否应该停止。

        Returns:
            bool: True 表示需要停止
        """
        return self._stop_check()

    def _check_pause(self) -> bool:
        """检查暂停状态。

        Returns:
            bool: True 表示继续运行，False 表示已暂停
        """
        if not self._pause_check():
            # 暂停中，等待恢复
            return False
        return True

    def _sleep(self, seconds: float) -> bool:
        """带停止检查的 sleep（v2.1.27 P1-3：实现收敛到 infra.timing）。

        按 `SLEEP_STEP`(0.1s) 分段等待，中途检测到停止信号立即返回；
        与旧 `_stop_event.wait()` 行为兼容。

        Args:
            seconds: 等待秒数

        Returns:
            bool: True 表示正常结束，False 表示中途停止
        """
        return sleep_interruptible(seconds, self._should_stop)

    # ── 逐次战斗等待 ────────────────────────────────────────────

    def wait_for_manual_battle_end(self) -> str:
        """等待单次战斗结束（逐次战斗模式）。

        Returns:
            'victory': 战斗胜利
            'defeat': 战斗失败
            'stopped': 收到停止信号
            'unknown': 不确定的结果
        """
        self._log('[信息] 等待战斗结束（逐次模式）')
        start_time: float = time.time()
        last_fp: Optional[int] = None
        next_ocr_check: float = 0.0
        battle_cfg: Dict[str, Any] = self._config.get('battle', {})
        # 最大战斗等待时长（秒）：四倍速30s/单倍速90s，
        # 60s为硬兜底，超时强制判定避免无限卡在战斗中
        max_wait: int = int(battle_cfg.get('max_wait', 60))

        while True:
            if self._should_stop():
                return 'stopped'
            if not self._check_pause():
                if not self._pause_check():
                    return 'stopped'

            elapsed: int = int(time.time() - start_time)
            self._set_status(phase='battle')

            # ── 超时兜底：强制判定，避免无限等待 ──
            if elapsed >= max_wait:
                self._log(
                    f'[警告] 战斗等待超时（{max_wait}s），强制判定'
                )
                self._screen.debug_screenshot('battle_wait_timeout')
                img, l, t, w, h = self._screen.snap()
                items: List[Tuple] = self._screen._ocr_img(img)
                texts: List[str] = [
                    txt for _, _, txt, c, *_ in items if c >= 0.3
                ]
                if any(
                    '胜利' in t or '挂机收益提升' in t for t in texts
                ):
                    self._log(f'[结果] 战斗胜利（超时判定）')
                    return 'victory'
                if any(
                    '失败' in t or '战败' in t or '再次挑战' in t
                    for t in texts
                ):
                    self._log(f'[结果] 战斗失败（超时判定）')
                    return 'defeat'
                self._log('[警告] 超时无法判定，按失败处理')
                return 'defeat'

            now: float = time.time()
            if now >= next_ocr_check:
                img, l, t, w, h = self._screen.snap()
                fp: int = self._screen.frame_fingerprint(img)

                if last_fp is None or fp != last_fp:
                    last_fp = fp
                    next_ocr_check = now + 2
                else:
                    # 画面稳定也可能是结算界面（静止），
                    # 降低 OCR 频率但继续检测，避免漏掉结算
                    next_ocr_check = now + 5

                # 用无缓存 OCR（_ocr_img），避免 ocr_screen 60s 缓存
                # 在画面指纹碰撞时返回旧的战斗画面结果
                items: List[Tuple] = self._screen._ocr_img(img)
                # 置信度阈值降到 0.3：结算界面艺术字/发光字识别率更高
                texts: List[str] = [
                    txt for _, _, txt, c, *_ in items if c >= 0.3
                ]

                has_victory: bool = any(
                    '胜利' in t or '战斗胜利' in t
                    or '挂机收益提升' in t  # 胜利结算特有副标题，OCR 高置信度
                    for t in texts
                )
                has_defeat: bool = any(
                    '失败' in t or '战败' in t or '战斗失败' in t
                    for t in texts
                )
                has_retry: bool = any(
                    '再次挑战' in t for t in texts
                )
                has_close: bool = any(
                    '点击空白处关闭' in t or '点击关闭' in t
                    for t in texts
                )
                # 结算界面出现"挑战"按钮（排除"再次挑战"/"幻灵挑战"）
                # = 胜利且可继续下一关
                has_challenge_btn: bool = any(
                    ('挑战' in t and '幻灵' not in t and '再次' not in t)
                    for t in texts
                )

                if has_victory:
                    self._log(f'[结果] 战斗胜利')
                    return 'victory'
                if has_defeat or has_retry:
                    self._log(f'[结果] 战斗失败')
                    return 'defeat'
                if has_close:
                    if any('胜利' in t for t in texts):
                        self._log(
                            f'[结果] 战斗胜利（结算）'
                        )
                        return 'victory'
                    elif any(
                        '失败' in t or '战败' in t for t in texts
                    ):
                        self._log(
                            f'[结果] 战斗失败（结算）'
                        )
                        return 'defeat'
                if has_challenge_btn:
                    self._log(f'[结果] 战斗胜利（结算挑战按钮）')
                    return 'victory'

            self._sleep(0.5)

        return 'unknown'

    # ── 关闭结算界面 ────────────────────────────────────────────

    def step_close_result(self, max_attempts: Optional[int] = None) -> bool:
        """关闭战斗结算界面。

        检测结算界面的各种标志（战斗胜利、点击关闭、再次挑战等），
        通过点击空白处、ESC 等方式关闭弹窗回到挂机关卡页。

        Args:
            max_attempts: 最大尝试次数，None 时从配置读取

        Returns:
            bool: True 表示已回到挂机关卡页，False 表示失败
        """
        self._log('[信息] 关闭结算')
        if max_attempts is None:
            battle_cfg: Dict[str, Any] = self._config.get('battle', {})
            max_attempts = int(
                battle_cfg.get('max_attempts_close_result', 10)
            )

        for attempt in range(max_attempts):
            if self._should_stop():
                return False
            if not self._check_pause():
                return False

            img, l, t, w, h = self._screen.snap()
            items: List[Tuple] = self._screen._ocr_img(img)
            texts: List[str] = [
                txt for _, _, txt, c, *_ in items if c >= 0.5
            ]

            # 优先检查确认框
            if any(
                '是否关闭' in t and '自动挑战' in t for t in texts
            ):
                self._log('[信息] 取消确认框')
                self.dismiss_confirm_dialog()
                self._sleep(1)
                continue

            # ── 逐次模式：结算界面直接点"挑战" ──────────────
            # 自动模式的"再次挑战"/关闭弹窗逻辑不适用于逐次模式：
            # 逐次模式无论胜负，结算界面都应点"挑战"（胜利继续下一关，
            # 失败回到备战界面换阵容重试）。同时记录结算结果，
            # 供 core 判断 _pending_defeat 是否真实失败。
            if self._battle_mode == 'manual':
                has_victory_t: bool = any(
                    '战斗胜利' in t or '挂机收益提升' in t
                    for t in texts
                )
                has_defeat_t: bool = any(
                    '失败' in t or '战败' in t for t in texts
                )
                # "再次挑战"按钮是绿底白字，OCR 置信度极高（约 0.99），
                # 比艺术字"战斗失败"（约 0.90，动画/渲染帧易失败）稳定得多。
                # 若只识别到"再次挑战"而未识别到"失败"，也必须进入结算处理
                # 直接点按钮——否则会落到下方兜底"点空白处"把结算界面关掉
                # → 游戏退回挂机关卡页 → 绕路重进，浪费数秒。
                has_retry_t: bool = any(
                    '再次挑战' in t for t in texts
                )
                if has_victory_t or has_defeat_t or has_retry_t:
                    self.last_result = (
                        'victory' if has_victory_t else 'defeat'
                    )
                    # 逐次模式：结算界面统一找"挑战"按钮——
                    # 模糊匹配命中"再次挑战"/"挑战"，一套逻辑覆盖胜利和失败
                    # （胜利→点"挑战"继续下一关，失败→点"再次挑战"退回备战换阵容）
                    pos: Optional[Tuple[int, int]] = None
                    btn_text: str = ''
                    for _cy, _cx, _t, _c, *_rest in items:
                        if (
                            _c >= 0.5
                            and '挑战' in _t
                            and '幻灵' not in _t
                        ):
                            pos, btn_text = (_cx, _cy), _t
                            break
                    if pos:
                        self._log(
                            f'[信息] 逐次模式结算'
                            f'（{"胜利" if has_victory_t else "失败"}），'
                            f'点"{btn_text}"'
                        )
                        self._input.click_at(pos[0], pos[1], btn_text)
                        # 点按钮后立即返回，让主循环重新检测阶段；
                        # 继续循环会在备战界面点空白处/再次误判，浪费十几秒
                        self._sleep(2)
                        return True
                    self._log(
                        '[信息] 逐次模式结算，'
                        '未找到按钮，走关闭逻辑'
                    )

            # 胜利结算界面
            if any('战斗胜利' in t for t in texts):
                self._log('[信息] 胜利结算界面，关闭弹窗')
                self._input.click_ratio_dual(
                    (0.5, 0.75), (0.5, 0.88), label='空白处'
                )
                self._sleep(2)
                continue

            # 已回到挂机关卡页
            if any(
                '幻灵挑战' in t for t in texts
            ) or any(
                t == '挑战' for t in texts
            ):
                self._log('[成功] 已回到挂机关卡')
                return True

            has_close: bool = any(
                '点击空白处关闭' in t or '点击关闭' in t for t in texts
            )
            has_defeat: bool = any(
                '失败' in t or '战败' in t for t in texts
            )
            has_auto_end: bool = any(
                '自动挑战结束' in t or '自动战斗结束' in t for t in texts
            )
            has_retry: bool = any('再次挑战' in t for t in texts)

            if has_close or has_defeat or has_auto_end:
                self._input.click_ratio_dual(
                    (0.5, 0.75), (0.5, 0.88), label='空白处'
                )
                self._sleep(2)
                continue

            # 找 × 按钮
            y_min: int = int(0.4 * h)
            closed: bool = False
            for cy, cx, text, conf, *_ in items:
                if conf >= 0.5 and '×' in text and cy > y_min:
                    self._input.click_abs(l + cx, t + cy)
                    self._log(
                        f'[信息] 关闭 | ({l + cx},{t + cy})'
                    )
                    self._sleep(2)
                    closed = True
                    break

            if not closed:
                if attempt < max_attempts - 1:
                    self._input.click_ratio_dual(
                        (0.5, 0.75), (0.5, 0.88), label='空白处'
                    )
                    self._sleep(2)

        return False

    # ── 确认框处理 ──────────────────────────────────────────────

    def dismiss_confirm_dialog(self) -> None:
        """关闭"是否关闭自动挑战模式？"确认框。

        尝试 OCR 识别 × 按钮并点击，OCR 失败时发送 ESC 取消。
        保持自动挑战继续运行。
        """
        img, l, t, w, h = self._screen.snap()
        items: List[Tuple] = self._screen._ocr_img(img)
        y_min: int = int(0.4 * h)
        for cy, cx, text, conf, *_ in items:
            if conf >= 0.4 and ('×' in text or 'X' in text) and cy > y_min:
                self._input.click_abs(l + cx, t + cy)
                self._log(
                    f'[信息] 关闭确认框 | ({l + cx},{t + cy})'
                )
                return
        # 兜底：按 ESC 取消
        self._input.send_esc()
        self._log('[信息] 取消确认框')

    def _close_exit_dialog(self) -> None:
        """关闭"是否确认退出游戏？"弹窗。

        点 × 按钮取消退出，最多重试 5 次，等待弹窗消失。
        """
        self._screen.activate_window()
        img, l, t, w, h = self._screen.snap()
        items: List[Tuple] = self._screen._ocr_img(img)

        # 找 × 按钮
        # v1.8.56 轴修正：items=(cy,cx,...)，此前解包名反置（cy 实为 x），
        # 筛选带实际比对 x、点击 x/y 互换
        x_btn_pos: Optional[Tuple[int, int]] = None
        for cy, cx, text, conf, *_ in items:
            if conf >= 0.3 and (
                'X' in text or '×' in text or 'x' in text
            ):
                if 0.55 * h < cy < 0.7 * h:
                    x_btn_pos = (cx, cy)
                    break

        for retry in range(5):
            if x_btn_pos:
                sx: int = l + x_btn_pos[0]
                sy: int = t + x_btn_pos[1]
                pyautogui.click(sx, sy)
                self._log(f'[信息] 关闭弹窗 | ({sx},{sy})')
            else:
                if retry == 0:
                    self._log('[信息] 弹窗未识别，默认位置关闭')
                self._input.click_ratio_dual(
                    (0.53, 0.64), (0.30, 0.62), label='退出弹窗X'
                )
            self._sleep(1.0)

            # 检查弹窗是否还在
            phase: str = self._detector.detect_phase()
            if phase != 'confirm_dialog':
                self._log(f'[成功] 弹窗已关闭 | {phase}')
                return
            self._log(
                f'[警告] 弹窗未关闭 | 重试{retry + 1}/5'
            )

        self._log('[警告] 弹窗无法关闭')
