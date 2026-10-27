"""
afk_bot 阵容管理模块

FormationManager 封装阵容切换、备战流程、结算界面挑战按钮点击、
挑战模式切换等逻辑。从 afk_bot.py 提取，去除对 AFKBot 的直接依赖。

设计原则：
- 所有截图/OCR/点击/检测操作通过注入的 screen_capture/recognizer/
  input_controller/detector 完成
- 停止/暂停检查通过 stop_check/pause_check 回调完成
- 资源路径通过 res_path 回调获取
- 导航操作通过 navigate_callback 回调完成
- 日志通过 log_callback 完成
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import keyboard
import pyautogui

from afk_bot.infra.timing import sleep_interruptible


# 结算界面「挑战」按钮的**底部判据**（v2.2.19，用户 2026-09-30 报障实证）：
# 结算界面的按钮文字是「**幻灵挑战**」（实测 1281x1030 截图 @(875.5,961.5)
# rx0.683/ry0.933 conf0.99，下方紧跟战力「10090万」）—— 而旧判据写的是
# `'幻灵' not in _t` ✗ 会把它**排除** → 结算界面永远找不到按钮 → 5 次重试后
# **误判「当前挑战已通关」**✗（日志 12:10~12:30 每次胜利都这样空转约 5 秒 ✗）
# 故改为「两级候选 + 位置约束」：优先无「幻灵」的「挑战」；否则取**底部**的
# 含「挑战」文本（结算/再战按钮都在底部 ✓；备战界面的「幻灵挑战」标题在顶部 ✗）。
_CHALLENGE_BTN_RY_MIN: float = 0.85

class FormationManager:
    """阵容管理器 — 切换阵容、备战流程、挑战按钮点击。

    封装备战界面的完整操作流程：打开阵容面板、切换阵容、
    一键采用、开始战斗，以及结算界面的挑战按钮点击。

    Attributes:
        _screen: ScreenCapture 实例
        _recognizer: TextRecognizer 实例
        _input: InputController 实例
        _detector: PhaseDetector 实例
        _log: 日志回调
        _config: 配置字典
        _stats: StatsTracker 实例
        _stop_check: 停止检查回调
        _pause_check: 暂停检查回调
        _res_path: 资源路径回调
        _navigate_callback: 导航到挂机关卡的回调
        _formation_idx: 当前阵容索引
    """

    def __init__(
        self,
        screen_capture: Any,
        recognizer: Any,
        input_controller: Any,
        detector: Any,
        log_callback: Optional[Callable[[str], None]] = None,
        config: Optional[Dict[str, Any]] = None,
        stats: Any = None,
        stop_check: Optional[Callable[[], bool]] = None,
        pause_check: Optional[Callable[[], bool]] = None,
        res_path: Optional[Callable[[str], str]] = None,
        navigate_callback: Optional[Callable[[], bool]] = None,
        battle_mode: str = 'auto',
    ):
        """初始化 FormationManager。

        Args:
            screen_capture: ScreenCapture 实例
            recognizer: TextRecognizer 实例
            input_controller: InputController 实例
            detector: PhaseDetector 实例
            log_callback: 日志回调
            config: 配置字典
            stats: StatsTracker 实例
            stop_check: 停止检查回调
            pause_check: 暂停检查回调
            res_path: 资源路径回调，接收相对路径返回绝对路径
            navigate_callback: 导航到挂机关卡的回调
            battle_mode: 战斗模式 — 'auto' 或 'manual'
        """
        self._screen: Any = screen_capture
        self._recognizer: Any = recognizer
        self._input: Any = input_controller
        self._detector: Any = detector
        self._log: Callable[[str], None] = log_callback or (lambda msg: None)
        self._config: Dict[str, Any] = config or {}
        self._stats: Any = stats
        self._stop_check: Callable[[], bool] = stop_check or (lambda: False)
        self._pause_check: Callable[[], bool] = pause_check or (lambda: True)
        self._res_path: Callable[[str], str] = res_path or (lambda p: p)
        self._navigate_callback: Callable[[], bool] = (
            navigate_callback or (lambda: False)
        )
        self.battle_mode: str = battle_mode

        # 状态变量
        self._formation_idx: int = 0

    # ── 内部辅助 ────────────────────────────────────────────────

    def _should_stop(self) -> bool:
        """检查是否应该停止。"""
        return self._stop_check()

    def _sleep(self, seconds: float) -> bool:
        """带停止检查的 sleep（v2.1.27 P1-3）。

        本文件原先 25 处 `time.sleep` 裸调，绕过停止信号 —— 点「停止」后
        仍要等完当前等待（编队序列里累积可达数秒）。现统一走分段可中断实现。

        Args:
            seconds: 等待秒数

        Returns:
            bool: True = 睡满；False = 中途检测到停止
        """
        return sleep_interruptible(seconds, self._should_stop)

    def _check_pause(self) -> bool:
        """检查暂停状态。"""
        if not self._pause_check():
            return False
        return True

    # ── 阵容切换 ────────────────────────────────────────────────

    def _switch_btn_region(self) -> Tuple[int, int, int, int]:
        """切换按钮（>）搜索区域：阵容面板右侧竖条。
        实测 > 按钮位于窗口 (x≈0.69, y≈0.54) 处，限定 x=0.60~0.88, y=0.25~0.75
        避免全屏模板匹配误命中底部"一键采用"按钮（y≈0.94 在区域外）。
        """
        l, t, w, h = self._screen.win_dims()
        return (int(l + w * 0.60), int(t + h * 0.25), int(w * 0.28), int(h * 0.50))

    def switch_formation(self, times: int = 1) -> bool:
        """切换阵容（点'>'按钮 N 次）。

        优化：只找一次 '>' 位置，后续直接同坐标连点，不重新 OCR/模板匹配。
        点击完成后验证 '>' 是否消失。

        Args:
            times: 需要点击 '>' 的次数

        Returns:
            bool: True 表示成功切换，False 表示已是最后一套阵容
        """
        # 标记本次切换后是否已到末尾（> 按钮消失）：
        # core 据此判断"推荐阵容用完，本次失败后立即切换挑战模式"
        self._switch_exhausted: bool = False
        self._log(f'[信息] 切换阵容（目标点{times}次">"）')
        tpl_path: str = self._res_path('templates/btn_formation_next.png')

        # 找 '>' 位置（只找一次）
        items: List[Tuple] = self._screen.ocr_screen()
        arrow_pos: Optional[Tuple[int, int]] = None
        for item in items:
            cy, cx, t = item[0], item[1], item[2]
            if t.strip() in ['>', '＞', '>']:
                # v1.8.56 坐标统一：OCR 是图像坐标，模板分支是屏幕坐标，
                # 消费端 click_abs 需屏幕坐标 → 此处先转换再混流
                coord = getattr(self._input, '_coord_system', None)
                if coord is not None:
                    arrow_pos = coord.image_to_screen(cx, cy)
                else:
                    arrow_pos = (cx, cy)
                break
        if not arrow_pos:
            match = self._input.template_match(
                tpl_path, threshold=0.6, silent=True,
                region=self._switch_btn_region(),
            )
            if match:
                arrow_pos = (
                    match if isinstance(match, tuple) else match
                )
        if not arrow_pos:
            self._log('[警告] 未找到>按钮，已是最后一套阵容')
            self._switch_exhausted = True
            return False

        # 已找到位置，连续点击 times 次
        clicked: int = 0
        while clicked < times:
            if self._should_stop():
                return False
            cx, cy = arrow_pos
            self._input.click_abs(cx, cy)
            clicked += 1
            self._log(
                f'[信息] 第{clicked}/{times}次点击>按钮 ({cx},{cy})'
            )
            if clicked < times:
                # 点击后移开鼠标到窗口中心，避免鼠标指针遮挡 > 按钮
                # 影响后续 OCR 检测（否则会误判"推荐阵容已用完"）
                l, t, w, h = self._screen.win_dims()
                pyautogui.moveTo(l + w // 2, t + h // 2)
                self._sleep(0.5)  # 0.3s 仍会漏点，加大到 0.5s

        # 验证 '>' 是否消失（面板已打开，has_switch_button 会先移开鼠标防遮挡）
        l, t, w, h = self._screen.win_dims()
        pyautogui.moveTo(l + w // 2, t + h // 2)
        self._sleep(0.5)

        still_match = self._input.template_match(
            tpl_path, threshold=0.6, silent=True,
            region=self._switch_btn_region(),
        )
        if not still_match and not self.has_switch_button(region=self._switch_btn_region()):
            self._log('[警告] >按钮消失，已到最后一套阵容')
            self._switch_exhausted = True
            return False

        self._log(f'[成功] 阵容切换完成（{times}次）')
        return True

    # ── 复选框检测 ──────────────────────────────────────────────

    def has_switch_button(self, region: Optional[Tuple[int, int, int, int]] = None) -> bool:
        """检测备战界面是否还有 > 切换阵容按钮（推荐阵容是否还有可切换的）。

        极限推图场景：推荐阵容不足 max_formation_index 时（如只有 5 套），
        切换到末尾后 > 按钮会消失。返回 False 表示已无推荐阵容可切。

        注意：检测前先把鼠标移开，避免鼠标指针遮挡 > 按钮导致 OCR 误判。

        Args:
            region: 可选屏幕绝对坐标 (rx,ry,rw,rh)，限定 OCR 找 > 的位置范围
                    （避免其他区域的 > 字符被误判为切换按钮）

        Returns:
            bool: True = 还有可切换的推荐阵容；False = 推荐阵容已用完
        """
        # 移开鼠标到窗口中心，避免遮挡 > 按钮
        try:
            wl, wt, ww, wh = self._screen.win_dims()
            pyautogui.moveTo(wl + ww // 2, wt + wh // 2)
            self._sleep(0.2)
        except Exception:
            pass
        # 用无缓存 OCR（_ocr_img），避免 ocr_screen 缓存里含鼠标遮挡的画面
        img, img_l, img_t, img_w, img_h = self._screen.snap()
        items: List[Tuple] = self._screen._ocr_img(img)
        for cy, cx, txt, c, x_min, x_max in items:
            if c >= 0.3 and txt.strip() in ['>', '＞', '>']:
                if region is not None:
                    rx, ry, rw, rh = region
                    ix = rx - img_l   # region 转图像坐标
                    if not (ix <= cx <= ix + rw):
                        continue
                return True
        return False

    # ── 未拥有角色弹窗 ──────────────────────────────────────────

    def handle_unowned_hero_dialog(self) -> bool:
        """检测并处理"未拥有角色确认弹窗"（一键采用后可能出现）。

        弹窗标志：同时存在 '未拥有' + ('取消' and '确定')
        处理：点击'取消' → 点击一次 '>'

        Returns:
            True  — 检测到弹窗并已处理
            False — 未检测到弹窗
        """
        items: List[Tuple] = self._screen.ocr_screen()
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.4
        ]
        has_unowned: bool = any('未拥有' in t for t in texts)
        has_cancel: bool = any('取消' in t for t in texts)
        has_confirm: bool = any('确定' in t for t in texts)
        if has_unowned and has_cancel and has_confirm:
            self._log('[信息] 检测到未拥有角色弹窗，点击取消')
            cancel_pos: Optional[Tuple[int, int]] = (
                self._recognizer.find_text(items, '取消')
            )
            if cancel_pos:
                self._input.click_at(
                    cancel_pos[0], cancel_pos[1],
                    '取消（未拥有弹窗）'
                )
                self._sleep(1)
            # 点击一次 '>' 切换到下一套阵容
            self._log('[信息] 切换到下一套阵容')
            ok: bool = self.switch_formation(times=1)
            if ok:
                self._formation_idx += 1
            else:
                # '>' 已消失，所有阵容试完
                self._formation_idx = 5
            return True
        return False

    # ── 备战与开始战斗 ──────────────────────────────────────────

    def prepare_and_start_battle(
        self, switch_count: int = 0, battle_mode: Optional[str] = None
    ) -> bool:
        """备战界面操作：通关阵容 → [>×N] → 一键采用 → 战斗。

        Args:
            switch_count: 需要点 '>' 的次数（0=不切换，N=点N次切换到第N+1套）
            battle_mode: 兼容参数，暂不使用（统一走OCR找"战斗"/"自动挑战"按钮）

        Returns:
            bool: True 表示成功开始战斗，False 表示失败
        """
        # 每次备战都重置"切换阵容用完"标志，
        # 避免 switch_count=0 时不调用 switch_formation 导致残留上次的值
        self._switch_exhausted = False
        log_msg: str = '备战：通关阵容'
        if switch_count > 0:
            log_msg += f'→>"×{switch_count}→'
        log_msg += '一键采用→战斗'
        self._log(f'[信息] {log_msg}')

        # 1. 点击"通关阵容"（如果面板已开则跳过）
        items: List[Tuple] = self._screen.ocr_screen()
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]
        _panel_kws: List[str] = [
            '一键采用', '阵容匹配', '只显示已拥有'
        ]
        _panel_already_open: bool = any(
            any(kw in t for kw in _panel_kws) for t in texts
        )
        if _panel_already_open:
            self._log(
                '[信息] 阵容面板已开（检测到"一键采用"），'
                '跳过点击"通关阵容"'
            )
        else:
            for attempt in range(5):
                if self._should_stop():
                    return False
                items = self._screen.ocr_screen()
                pos: Optional[Tuple[int, int]] = (
                    self._recognizer.find_text(items, '通关阵容')
                )
                if pos:
                    self._input.click_at(
                        pos[0], pos[1], '通关阵容'
                    )
                    self._sleep(0.5)
                    break
                else:
                    self._log(
                        f'[警告] 未找到通关阵容按钮，重试 #{attempt}'
                    )
                    self._sleep(1)
            else:
                self._log('[错误] 未找到通关阵容按钮')
                return False

        # 2. 如果需要切换阵容，点 '>' 按钮 N 次
        if switch_count > 0:
            switched: bool = self.switch_formation(times=switch_count)
            if not switched:
                self._log(
                    '[警告] 切换阵容：>消失，已到最后一套，'
                    '继续用当前阵容战斗'
                )

        # 3. 点击"一键采用"（面板已开时复用初检 OCR 结果）
        for attempt in range(5):
            if self._should_stop():
                return False
            if not _panel_already_open or attempt > 0 or switch_count > 0:
                items = self._screen.ocr_screen()
            pos = self._recognizer.find_text(
                items, '一键采用', min_conf=0.3
            )
            if pos:
                self._input.click_at(pos[0], pos[1], '一键采用')
                self._sleep(0.5)
                # 检测未拥有角色弹窗
                handled: bool = self.handle_unowned_hero_dialog()
                if handled:
                    continue
                break
            else:
                self._log(
                    f'[警告] 未找到一键采用按钮，重试 #{attempt}'
                )
                self._sleep(1)
        else:
            self._log('[错误] 未找到一键采用按钮')
            return False

        # 4. 开始战斗
        _battle_btn_mode: str = (
            battle_mode if battle_mode is not None else self.battle_mode
        )
        self._sleep(0.3)
        if _battle_btn_mode == 'auto':
            # 自动模式：OCR 找"自动挑战"按钮点击
            for attempt in range(5):
                if self._should_stop():
                    return False
                items = self._screen.ocr_screen()
                pos = self._recognizer.find_text(items, '自动挑战')
                if pos:
                    self._input.click_at(pos[0], pos[1], '自动挑战')
                    self._sleep(2)
                    return True
                else:
                    self._log(
                        f'[警告] 未找到自动挑战按钮，重试 #{attempt}'
                    )
                    self._sleep(1)
            self._log('[错误] 未找到自动挑战按钮')
            return False
        else:
            # 逐次模式：游戏官方把"战斗"绑定 F 键（按钮现显示"战斗+F"标签）
            # 直接按 F 键绕过 OCR —— 绿底白字 + F 标签导致 OCR 识别不稳定
            self._screen.activate_window()  # 确保游戏窗口在前台
            keyboard.send('f')  # 小写！= 物理 F 键（大写会被识别为 Shift+F）
            self._log('[信息] 按 F 键开始战斗（逐次模式）')
            self._sleep(2)
            return True

    # ── 挂机关卡页 挑战模式检测 ────────────────────────────────

    def detect_available_challenge_mode(self) -> Optional[str]:
        """检测挂机关卡页有哪些挑战按钮可用。

        通过 OCR 识别挂机关卡页的文字，判断"幻灵挑战"和"挑战"按钮是否出现。

        Returns:
            'both' — 两种按钮都存在
            'huanling' — 只有幻灵挑战
            'normal' — 只有挑战
            None — 两者都没有（可能需要重试）
        """
        items = self._screen.ocr_screen()
        # v1.8.34：先剔除「已完成全部挑战」横幅——它含"挑战"且不含"幻灵"，
        # 曾被 has_normal 误判为存在普通挑战按钮（真机 9/6：赛季全通关后
        # 死循环找按钮）。剔除后 available=None → 上层走全部通关停止分支
        texts = [
            t for _, _, t, c, *_ in items
            if c >= 0.3 and '已完成全部挑战' not in t
        ]
        has_huanling: bool = any('幻灵挑战' in t for t in texts)
        has_normal: bool = any(
            '挑战' in t and '幻灵' not in t for t in texts
        )
        if has_huanling and has_normal:
            return 'both'
        elif has_huanling:
            return 'huanling'
        elif has_normal:
            return 'normal'
        return None

    # ── 结算界面挑战按钮 ────────────────────────────────────────

    def click_challenge_btn(
        self, result: str, mode: str
    ) -> Union[bool, str]:
        """胜利/失败后点击按钮，回到备战界面。

        Args:
            result: 'victory' 或 'defeat'
            mode: 'huanling' 或 'normal'

        Returns:
            True 表示已进入备战界面
            False 表示失败
            'all_completed' 表示全部挑战已完成
        """
        if not self._check_pause():
            return False

        # 逐次战斗模式：不论胜利失败，都只点"挑战"
        if self.battle_mode == 'manual':
            self._log('[信息] 逐次战斗模式，点击"挑战"按钮')
            # 等待结果界面
            for attempt in range(3):
                if self._should_stop():
                    return False
                phase: str = self._detector.detect_phase()
                if phase == 'result':
                    break
                self._sleep(1)

            # 点击"挑战"按钮（带重试）
            for attempt in range(5):
                if self._should_stop():
                    return False
                items: List[Tuple] = self._screen.ocr_screen()
                texts: List[str] = [
                    t for _, _, t, c, *_ in items if c >= 0.5
                ]
                if any('已完成全部挑战' in t for t in texts):
                    self._log('[信息] 检测到"已完成全部挑战"，停止脚本')
                    return 'all_completed'
                # 找"挑战"按钮（模糊匹配 + 排除"幻灵挑战"；允许"再次挑战"——
                # 逐次模式失败结算按钮是"再次挑战"，点击后同样退回备战界面）
                pos: Optional[Tuple[int, int]] = None
                # v2.2.19：两级候选（见 _CHALLENGE_BTN_RY_MIN 处的实测依据）
                #   ① 无「幻灵」的「挑战」→ 直接用（挂机关卡页按钮 ✓）
                #   ② 否则取**底部**含「挑战」的文本 → 结算/再战按钮 ✓
                #      （备战界面的「幻灵挑战」标题在顶部 → ry 不够 → 不误点 ✓）
                _bottom: Optional[Tuple[int, int]] = None
                for _cy, _cx, _t, _c, *_rest in items:
                    if _c < 0.5 or '挑战' not in _t:
                        continue
                    if '幻灵' not in _t:
                        pos = (_cx, _cy)
                        break
                    if (_bottom is None
                            and _cy / 1024.0 >= _CHALLENGE_BTN_RY_MIN):
                        _bottom = (_cx, _cy)
                if pos is None:
                    pos = _bottom
                if pos:
                    self._input.click_at(pos[0], pos[1], '挑战')
                    # 等待进入备战界面
                    for wait in range(20):
                        if self._should_stop():
                            return False
                        self._sleep(0.5)
                        items2: List[Tuple] = (
                            self._screen.ocr_screen()
                        )
                        texts2: List[str] = [
                            t for _, _, t, c, *_ in items2
                            if c >= 0.5
                        ]
                        if any(
                            '通关阵容' in t for t in texts2
                        ):
                            self._log(
                                '[成功] 已进入备战界面'
                            )
                            return True
                    self._log('[警告] 等待备战界面超时')
                    return False
                else:
                    self._log(
                        f'[警告] 未找到挑战按钮，重试 #{attempt}'
                    )
                    self._sleep(1)

            self._log('[信息] 未找到挑战按钮，判定当前挑战已通关，返回挂机关卡页切换挑战类型')
            return 'mode_completed'

        # 自动战斗模式：根据结果选择按钮
        if result == 'victory':
            if mode == 'huanling':
                label: str = '幻灵挑战'
                find_fn = lambda its: self._recognizer.find_text_exact(
                    its, '幻灵挑战'
                )
                fallback: str = 'huanling'
            else:
                label = '挑战'
                find_fn = lambda its: self._recognizer.find_text_exclude(
                    its, '挑战', exclude_keywords=['幻灵']
                )
                fallback = 'challenge'
        else:
            label = '再次挑战'
            find_fn = lambda its: self._recognizer.find_text_exact(
                its, '再次挑战'
            )
            fallback = 'retry'

        # 等待结果界面
        for attempt in range(10):
            if self._should_stop():
                return False
            phase = self._detector.detect_phase()
            if phase == 'result':
                break
            self._sleep(1)

        # 点击按钮（带重试）
        clicked: bool = False
        for attempt in range(3):
            if self._should_stop():
                return False
            items = self._screen.ocr_screen()
            texts: List[str] = [
                t for _, _, t, c, *_ in items if c >= 0.5
            ]
            if any('已完成全部挑战' in t for t in texts):
                self._log('[信息] 检测到"已完成全部挑战"，停止脚本')
                return 'all_completed'
            pos = find_fn(items)
            if pos:
                self._input.click_at(pos[0], pos[1], label)
                clicked = True
                break
            self._log(f'[警告] 未找到 | {label} #{attempt}')
            self._sleep(1)

        if not clicked:
            self._log(f'[错误] 未找到{label}按钮（OCR失败）')
            return False

        # 等待进入备战界面
        for wait in range(20):
            if self._should_stop():
                return False
            self._sleep(0.5)
            items2 = self._screen.ocr_screen()
            texts2: List[str] = [
                t for _, _, t, c, *_ in items2 if c >= 0.5
            ]
            if any('通关阵容' in t for t in texts2):
                self._log('[成功] 已进入备战界面')
                return True

        self._log('[警告] 等待备战界面超时')
        return False

    # ── OCR 找挑战按钮 ──────────────────────────────────────────

    def ocr_click_challenge(self, mode: str) -> bool:
        """用 OCR 找挑战/幻灵挑战按钮并点击。

        Args:
            mode: 'huanling' 或 'normal'

        Returns:
            bool: True 表示找到并点击成功
        """
        items: List[Tuple] = self._screen.ocr_screen()
        if mode == 'huanling':
            pos: Optional[Tuple[int, int]] = (
                self._recognizer.find_text_exact(items, '幻灵挑战')
            )
            label: str = '幻灵挑战'
        else:
            pos = self._recognizer.find_text_exclude(
                items, '挑战', exclude_keywords=['幻灵']
            )
            label = '挑战'
        if pos:
            self._input.click_at(pos[0], pos[1], label)
            return True
        self._log(f'[警告] 未找到{label}按钮')
        return False

    # ── 结算兜底 ────────────────────────────────────────────────

    def result_fallback_click(self) -> None:
        """结算界面兜底：OCR 找"挑战"→点击，失败则 ESC。"""
        items: List[Tuple] = self._screen.ocr_screen()
        pos: Optional[Tuple[int, int]] = (
            self._recognizer.find_text_exact(items, '挑战')
        )
        if pos:
            self._log(f'[兜底] OCR找到 | 挑战 {pos}')
            self._input.click_at(pos[0], pos[1], '挑战')
            self._sleep(2)
            return

        self._log('[警告] 兜底OCR失败，ESC关闭结算')
        self._input.send_esc()
        self._sleep(2)

    # ── 交替模式切换 ────────────────────────────────────────────

    def navigate_to_other_challenge(
        self, current_mode: str
    ) -> None:
        """切回挂机关卡页并切换到另一项挑战。

        Args:
            current_mode: 当前模式 — 'huanling' 或 'normal'
        """
        self._log(
            f'[信息] 切换到另一项挑战（从{current_mode}）'
        )

        # 按 ESC 返回挂机关卡页
        self._input.send_esc()
        self._sleep(1)

        # 检测当前 phase
        phase: str = self._detector.detect_phase()
        self._log(f'[阶段] ESC后: {phase}')

        if phase == 'idle_stage':
            self._log('[信息] 已在挂机关卡页')
            other_mode: str = (
                'normal' if current_mode == 'huanling' else 'huanling'
            )
            self._log(f'[信息] 切换到{other_mode}')
            btn_text: str = (
                '挑战' if other_mode == 'normal' else '幻灵挑战'
            )
            for attempt in range(5):
                if self._should_stop():
                    return
                items: List[Tuple] = self._screen.ocr_screen()
                pos: Optional[Tuple[int, int]] = (
                    self._recognizer.find_text_exclude(
                        items, btn_text,
                        exclude_keywords=(
                            ['幻灵'] if btn_text == '挑战' else []
                        ),
                    )
                )
                if pos:
                    self._input.click_at(
                        pos[0], pos[1], f'{btn_text}'
                    )
                    self._sleep(2)
                    return
                else:
                    self._log(
                        f'[警告] 未找到{btn_text}按钮，'
                        f'重试 #{attempt}'
                    )
                    self._sleep(1)
            self._log('[错误] 未找到挑战按钮')
        else:
            # 需要导航到挂机关卡页
            self._log('[信息] 导航到挂机关卡页')
            if self._navigate_callback():
                self._log('[成功] 导航到挂机关卡页')
                other_mode = (
                    'normal' if current_mode == 'huanling'
                    else 'huanling'
                )
                btn_text = (
                    '挑战' if other_mode == 'normal'
                    else '幻灵挑战'
                )
                for attempt in range(5):
                    if self._should_stop():
                        return
                    items = self._screen.ocr_screen()
                    pos = self._recognizer.find_text_exclude(
                        items, btn_text,
                        exclude_keywords=(
                            ['幻灵'] if btn_text == '挑战'
                            else []
                        ),
                    )
                    if pos:
                        self._input.click_at(
                            pos[0], pos[1], f'{btn_text}'
                        )
                        self._sleep(2)
                        return
                    else:
                        self._log(
                            f'[警告] 未找到{btn_text}按钮，'
                            f'重试 #{attempt}'
                        )
                        self._sleep(1)
                self._log('[错误] 未找到挑战按钮')
            else:
                self._log('[错误] 导航到挂机关卡页失败')
