"""
afk_bot 导航模块

Navigator 封装赴战导航（到挂机关卡页 2级）、等待挂机关卡/备战、
ESC 回退 0 层（世界/家园）、通用阶段处理等逻辑。从 afk_bot.py 提取，去除对 AFKBot 的直接依赖。

设计原则：
- 所有截图/OCR/点击/检测操作通过注入的子模块完成
- 停止检查通过 stop_check 回调完成
- 日志通过 log_callback 完成
- 按钮位置通过配置字典传入
"""

import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from afk_bot.config import _BTN_POS, _BTN_POS_PORTRAIT


# v1.8.32 日志中文化：detector phase → 中文界面名
_PHASE_ZH = {
    'world': '世界', 'home': '家园', 'guild': '公会',
    'playbook': '玩法目录', 'activity': '活动页',
    'idle_stage': '挂机关卡页', 'labyrinth': '异界迷宫',
    'formation': '备战', 'battle': '战斗中', 'result': '结算',
    'confirm_dialog': '确认弹窗', 'pause': '暂停', 'loading': '加载中',
    'login': '登录', 'options_menu': '选项菜单', 'quest_panel': '任务面板',
    'unknown': '未识别画面',
}


def _phase_zh(phase: str) -> str:
    return _PHASE_ZH.get(phase, phase)


# v1.8.51：同状态心跳间隔（模块级常量，此前两次加锚点均失败）
_NAVLOG_HEARTBEAT: int = 3

class Navigator:
    """导航器 — 赴战（到挂机关卡页）、ESC 回退 0 层（世界/家园）、待战/清障。

    封装导航状态机、OCR 查找点击、ESC 退回、通用阶段处理等逻辑。

    Attributes:
        _screen: ScreenCapture 实例
        _recognizer: TextRecognizer 实例
        _input: InputController 实例
        _detector: PhaseDetector 实例
        _log: 日志回调
        _config: 配置字典
        _stop_check: 停止检查回调
        _close_result_fn: 结算界面关闭回调（委托给 BattleMonitor）
        _battle_monitor: BattleMonitor 实例引用（用于 step_close_result 等）
    """

    def __init__(
        self,
        screen_capture: Any,
        recognizer: Any,
        input_controller: Any,
        detector: Any,
        log_callback: Optional[Callable[[str], None]] = None,
        config: Optional[Dict[str, Any]] = None,
        stop_check: Optional[Callable[[], bool]] = None,
        close_result_fn: Optional[Callable[[], bool]] = None,
        handle_login_fn: Optional[Callable[[], bool]] = None,
        battle_monitor: Any = None,
        res_path: Optional[Callable[[str], str]] = None,
        bot: Any = None,   # v1.9.11：可选 bot（facade 自身），供全局导航委派
    ):
        """初始化 Navigator。

        Args:
            screen_capture: ScreenCapture 实例，提供 snap(), ocr_screen()
            recognizer: TextRecognizer 实例，提供 find_text(), find_with_memory()
            input_controller: InputController 实例，提供 click_at(),
                              click_ratio_dual(), send_esc(), template_match()
            detector: PhaseDetector 实例，提供 detect_phase(),
                      is_on_level0()
            log_callback: 日志回调
            config: 配置字典（含 battle 子配置）
            stop_check: 停止检查回调，返回 True 表示需要停止
            close_result_fn: 结算界面关闭回调
            handle_login_fn: 登录界面处理回调
            battle_monitor: BattleMonitor 实例引用
            res_path: 资源路径解析回调，返回资源文件绝对路径
        """
        self._screen: Any = screen_capture
        self._recognizer: Any = recognizer
        self._input: Any = input_controller
        self._detector: Any = detector
        self._log: Callable[[str], None] = log_callback or (lambda msg: None)
        self._config: Dict[str, Any] = config or {}
        # v1.8.51：状态日志去重与心跳（此前误加到不存在的锚点，未落地 →
        # 真机 AttributeError: 'Navigator' object has no attribute '_last_navlog'）
        self._last_navlog: str = ''
        self._navlog_repeat: int = 0
        self._stop_check: Callable[[], bool] = stop_check or (lambda: False)
        self._close_result_fn: Callable[[], bool] = (
            close_result_fn or (lambda: False)
        )
        self._handle_login_fn: Callable[[], bool] = (
            handle_login_fn or (lambda: False)
        )
        self._battle_monitor: Any = battle_monitor
        self._res_path: Callable[[str], str] = res_path or (lambda p: p)
        # v1.9.11：bot（facade）引用——注入后 navigate_to_idle_stage 会
        # 优先走全局导航系统（GlobalNavigator），失败再降级本状态机
        self._bot: Any = bot

        # 导航相关配置
        self._navigate_timeout: int = int(
            self._config.get('battle', {}).get('max_iter_navigate', 40)
        )
        self._battle_timeout: int = int(
            self._config.get('battle_timeout', 90)
        )

    # ── 内部辅助 ────────────────────────────────────────────────

    def _navlog(self, msg: str) -> None:
        """v1.8.50：状态日志去重 + 心跳——相同文案首次打印，之后每
        _NAVLOG_HEARTBEAT 轮补一条带轮次的心跳，避免长时间无日志被误判卡死。"""
        if msg == getattr(self, '_last_navlog', ''):
            self._navlog_repeat = getattr(self, '_navlog_repeat', 0) + 1
            if self._navlog_repeat % _NAVLOG_HEARTBEAT == 0:
                self._log(f'{msg}（已等待 {self._navlog_repeat} 轮）')
            return
        self._last_navlog = msg
        self._navlog_repeat = 0
        self._log(msg)

    def _should_stop(self) -> bool:
        """检查是否应该停止（v1.8.49：命中时留痕，避免静默退出难排查）。"""
        if self._stop_check():
            self._log('[信息] 收到停止信号，导航中止')
            return True
        return False

    def _sleep(self, seconds: float) -> bool:
        """带停止检查的 sleep。

        按 0.1s 步长分段等待，中途检测到停止信号立即返回。

        Args:
            seconds: 等待秒数

        Returns:
            bool: True 表示正常结束，False 表示中途停止
        """
        elapsed: float = 0.0
        while elapsed < seconds:
            if self._should_stop():
                return False
            step: float = min(0.1, seconds - elapsed)
            time.sleep(step)
            elapsed += step
        return True

    def _handle_confirm_dialog(self) -> bool:
        """处理退出确认弹窗：优先点击右上角 X，否则 ESC 取消。

        Returns:
            bool: True 表示已尝试处理，False 表示已停止
        """
        if self._should_stop():
            return False
        self._screen.activate_window()
        try:
            tpl_path: str = self._res_path('templates/btn_confirm_cancel.png')
            pos: Optional[Tuple[int, int]] = (
                self._input.template_match(
                    tpl_path, threshold=0.7, silent=True
                )
            )
        except Exception as _e:
            self.log(f"[调试] _handle_confirm_dialog() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            pos = None
        if pos:
            self._log(
                '[信息] 检测到退出确认弹窗，'
                '点击右上角X关闭 | 操作=点击X'
            )
            # template_match 返回屏幕绝对坐标，必须用 click_abs
            # （click_at 期望图像相对坐标，二次换算出 bug 点空）
            self._input.click_abs(pos[0], pos[1])
        else:
            self._log(
                '[信息] 检测到退出确认弹窗，'
                '按退出键取消 | 操作=退出键取消'
            )
            self._input.send_esc()
        self._sleep(1.5)
        return True

    # ── OCR 查找并点击（导航用）─────────────────────────────────

    def _ocr_and_click_for_navigate(
        self,
        target_text: str,
        tmpl_name: Optional[str] = None,
        y_min_ratio: Optional[float] = None,
        max_retries: int = 3,
    ) -> bool:
        """OCR 找到文字并点击，返回是否成功（用于导航场景）。

        使用 TextRecognizer.find_with_memory() 带 OCR 记忆的查找，
        失败时回退到比例坐标模板匹配。

        Args:
            target_text: 要查找的目标文字
            tmpl_name: 可选模板名，对应 _BTN_POS / _BTN_POS_PORTRAIT 的键
            y_min_ratio: 可选 Y 方向比例阈值，只匹配此比例以下的文字
            max_retries: 最大重试次数

        Returns:
            bool: 找到并点击成功返回 True，否则返回 False
        """
        if self._should_stop():
            return False

        for attempt in range(1, max_retries + 1):
            pos: Optional[Tuple[int, int]] = (
                self._recognizer.find_with_memory(
                    target_text, scene='navigate', exact=False,
                    y_min_ratio=y_min_ratio,
                )
            )
            if pos:
                self._input.click_at(pos[0], pos[1], target_text)
                self._sleep(1.5)
                return True
            if attempt < max_retries:
                self._log(
                    f'[警告] OCR未找到，重试 | {target_text} #{attempt}'
                )
                self._sleep(1)
                continue
            if tmpl_name:
                self._log(
                    f'[警告] OCR失败，模板匹配 | {target_text}'
                )
                pos_landscape: Optional[Tuple[float, float]] = (
                    _BTN_POS.get(tmpl_name)
                )
                pos_portrait: Optional[Tuple[float, float]] = (
                    _BTN_POS_PORTRAIT.get(tmpl_name)
                )
                if pos_landscape and pos_portrait:
                    self._input.click_ratio_dual(
                        pos_landscape, pos_portrait,
                        label=f'{target_text} [模板]',
                    )
                    self._sleep(1.5)
                    return True
            self._log(f'[警告] 未找到目标 | {target_text}')
        return False

    # ── 目标检测 ────────────────────────────────────────────────

    def _scroll_find_and_click(self, target_text: str) -> bool:
        """玩法目录内滚动查找卡片文字并点击（v1.8.94）。

        真机 14:23：玩法目录记住上次滚动位置，「挂机关卡」不在当前屏
        → OCR 3 次重试全空 → 导航失败。labyrinth/goddess 均有滚动自愈，
        navigator 此前漏配。

        流程：先向下滚（-30 大步，v1.7.4 提速版）沿途找；画面文本不再
        变化 = 已到底仍未找到 → 向上滚回顶再找一轮（覆盖卡片在当前屏
        上方的情况）。两轮都未命中 → False（由调用方 no_progress 收敛）。
        终止全部为**状态判定**（画面无变化），无次数上限（用户定案）。

        Returns:
            bool: 找到并点击返回 True
        """
        self._log(f'[信息] 目标不在当前屏，滚动查找 | {target_text}')
        if self._scroll_dir_click(target_text, -30):
            return True
        self._log(f'[信息] 已滚到底未找到，向上滚回找 | {target_text}')
        return self._scroll_dir_click(target_text, 30)

    def _scroll_dir_click(self, target_text: str, clicks: int) -> bool:
        """单方向滚动查找：OCR 找 target_text 子串 → 命中即点；
        滚动后画面文本与上一屏相同（滚不动了）→ 返回 False。"""
        prev_texts: Optional[List[str]] = None
        while True:
            if self._should_stop():
                return False
            items: List[Tuple] = self._screen.ocr_screen()
            hit: Optional[Tuple[int, int]] = next(
                ((it[1], it[0]) for it in items
                 if len(it) >= 4 and it[3] >= 0.55
                 and target_text in it[2]),
                None,
            )
            if hit:
                self._input.click_at(hit[0], hit[1], target_text)
                self._sleep(1.5)
                return True
            cur: List[str] = sorted(
                it[2] for it in items if len(it) >= 4 and it[3] >= 0.5)
            if prev_texts is not None and cur == prev_texts:
                return False
            prev_texts = cur
            self._input.scroll_wheel(
                clicks, rx=0.5, ry=0.4,
                label=f'玩法目录_滚动{"下" if clicks < 0 else "上"}',
            )
            self._sleep(1.0)

    def is_target(self) -> bool:
        """检查是否已在挂机关卡页（phase 'idle_stage'，2级）。委托 detector，单一真相源。"""
        return self._detector.detect_phase() == 'idle_stage'

    def _is_target_detailed(self) -> bool:
        """旧版详细检查（保留供参考，实际不再使用）。"""
        items: List[Tuple] = self._screen.ocr_screen()
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.5
        ]
        has_challenge: bool = any('挑战' in t for t in texts)
        has_formation: bool = any(
            '通关阵容' in t or '自动挑战' in t for t in texts
        )
        is_activity: bool = any('常规玩法' in t for t in texts)
        return has_challenge and not has_formation and not is_activity

    # ── ESC 回退 0 层（世界/家园）────────────────────────────────

    def esc_to_level0(self, max_esc: int = 5) -> bool:
        """连续发送 ESC 直到回到 0 层（世界/家园） 或达到上限。

        处理退出游戏弹窗：检测到"退出游戏"时再按 ESC 取消弹窗。

        Args:
            max_esc: 最大 ESC 发送次数

        Returns:
            bool: True 表示已回到 0 层（世界/家园），False 表示失败
        """
        if self._detector.is_on_level0():
            return True

        exit_count: int = 0
        for i in range(max_esc):
            if self._should_stop():
                return False

            items: List[Tuple] = self._screen.ocr_screen()
            if self._recognizer.find_text(items, '退出游戏'):
                exit_count += 1
                self._log(f'[信息] 弹窗已关闭 | {exit_count}次')
                self._input.send_esc()
                self._sleep(0.8)
                if exit_count >= 2:
                    self._log('[成功] 已回到 0 层')
                    return True
                continue

            if self._detector.is_on_level0():
                return True

            exit_count = 0
            self._log(f'[信息] 退出键返回 | {i + 1}/{max_esc}')
            self._input.send_esc()
            self._sleep(0.8)

        return False

    # ── 等待挂机关卡/备战 ──────────────────────────────────────

    def wait_for_idle_stage(
        self, timeout: int = 30
    ) -> bool:
        """等待挂机关卡页（2级）或备战态出现。

        持续 OCR 检测画面，画面无变化超阈值时发送 ESC。

        Args:
            timeout: 超时时间（秒）

        Returns:
            bool: True 表示已进入挂机关卡页/备战界面，False 表示超时或停止
        """
        self._log('[信息] 等待挂机关卡/备战 | 状态=等待')
        battle_cfg: Dict[str, Any] = self._config.get('battle', {})
        deadline: float = time.time() + timeout
        miss_count: int = 0
        miss_threshold: int = int(battle_cfg.get('miss_threshold', 3))
        last_fp: Optional[str] = None

        while time.time() < deadline:
            if self._should_stop():
                return False

            items: List[Tuple] = self._screen.ocr_screen()
            texts: List[str] = [
                t for _, _, t, c, *_ in items if c >= 0.5
            ]
            fp: str = '|'.join(sorted(set(texts)))

            if any(
                '幻灵挑战' in t for t in texts
            ) or any(
                t == '挑战' for t in texts
            ):
                self._log('[成功] 已在挂机关卡')
                return True
            if any(
                '通关阵容' in t for t in texts
            ) or any(
                '自动挑战' in t for t in texts
            ):
                self._log('[成功] 已在备战界面')
                return True

            # 连续 N 次画面无变化 → ESC
            if fp == last_fp and last_fp is not None:
                miss_count += 1
                if miss_count >= miss_threshold:
                    self._log(
                        f'[警告] 画面无变化，发送退出键 | '
                        f'次数={miss_count}'
                    )
                    self._input.send_esc()
                    self._sleep(1)
                    miss_count = 0
            else:
                miss_count = 0
            last_fp = fp

            self._sleep(1)
        return False

    # ── 通用阶段处理 ────────────────────────────────────────────

    def _handle_common_phase(self, phase: str) -> str:
        """处理通用阶段（loading/login/confirm_dialog/result/options_menu）。

        返回处理后的新 phase（如果退出了通用阶段），
        或者保持原 phase（如果不需要处理）。

        Args:
            phase: 当前阶段（由 detect_phase() 返回）

        Returns:
            str: 处理后的新 phase
        """
        if phase == 'loading':
            self._log('[信息] 加载中...')
            self._sleep(5)
            return self._detector.detect_phase()

        if phase == 'login':
            self._log('[信息] 登录界面，点"开始游戏"')
            for attempt in range(5):
                if self._should_stop():
                    return phase
                items: List[Tuple] = self._screen.ocr_screen()
                pos: Optional[Tuple[int, int]] = (
                    self._recognizer.find_text_exact(items, '开始游戏')
                )
                if pos:
                    self._input.click_at(
                        pos[0], pos[1], '开始游戏'
                    )
                    self._log('[信息] 点击开始游戏')
                    self._sleep(3)
                    return self._detector.detect_phase()
                self._log(
                    f'[警告] 未找到开始游戏按钮，重试 #{attempt}'
                )
                self._sleep(1)
            self._log('[错误] 未找到开始游戏按钮')
            return phase

        if phase == 'confirm_dialog':
            self._handle_confirm_dialog()
            return self._detector.detect_phase()

        if phase == 'result':
            self._log('[信息] 结算界面，尝试关闭')
            # 委托给 BattleMonitor 的 step_close_result
            if self._battle_monitor is not None:
                self._battle_monitor.step_close_result()
            else:
                self._close_result_fn()
            return self._detector.detect_phase()

        if phase == 'pause':
            self._log('[信息] 战斗暂停界面，点"继续"')
            items = self._screen.ocr_screen()
            pos = self._recognizer.find_text(items, '继续')
            if pos:
                self._input.click_at(pos[0], pos[1], '暂停_继续')
                self._sleep(2)
                return self._detector.detect_phase()
            # 兜底：再次挑战/退出战斗
            for label in ('再次挑战', '退出战斗'):
                pos = self._recognizer.find_text(items, label)
                if pos:
                    self._input.click_at(pos[0], pos[1], f'暂停_{label}')
                    self._sleep(2)
                    return self._detector.detect_phase()
            # 最终兜底：ESC
            self._input.send_esc()
            self._sleep(1)
            return self._detector.detect_phase()

        if phase == 'options_menu':
            self._log('[信息] 选项菜单，按ESC关闭')
            self._input.send_esc()
            self._sleep(1)
            return self._detector.detect_phase()

        # 不是通用阶段，直接返回
        return phase

    # ── 赴战：导航到挂机关卡页 ─────────────────────────────────

    def _global_screen_hint(self) -> str:
        """v1.8.29：bot core 导航遇未知画面时，查**全局界面注册表**补画面名。

        背景（真机 9/6 用户反馈"仍然显示 unknown"）：全局注册表
        （tasks/screen_registry.py）已录入全部界面，但此前只有任务层在用；
        bot core 导航仍只看 detector phase，遇到女神选关页等界面就打
        unknown。这里做**只读补日志**，动作仍按 phase（unknown → ESC
        重试，对导航语义正确）。

        分层：afk_bot 不顶层依赖 tasks → 函数内**惰性 import**，
        且失败静默（保证 bot core 独立性不受影响）。

        Returns:
            str: '（全局识别：goddess.select_page）' 或 ''（unknown/异常）
        """
        try:
            from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY
            img, _l, _t, _w, _h = self._screen.snap()
            items = self._screen._ocr_img(img)
            screen_id: str = GLOBAL_SCREEN_REGISTRY.detect(items)
            if screen_id == 'unknown':
                return ''
            return f'（全局识别：{screen_id}）'
        except Exception as _e:
            self.log(f"[调试] _global_screen_hint() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return ''   # 注册表不可用/异常 → 静默，不改变原有行为

    def _navigate_via_global(self) -> bool:
        """v1.9.11：委派全局导航系统到挂机关卡页（idle_stage）。

        统一入口：与迷宫/女神/家园任务共用 GlobalNavigator，判据走全局
        画面注册表（screen_registry），脱离走 ESC/返回按钮交替链。
        `self._bot` 由装配方注入（facade 把自己传进来）；未注入或全局
        导航失败时返回 False，交由原有状态机降级处理（不引入回归）。

        Returns:
            bool: True = 全局导航成功；False = 降级到原状态机
        """
        bot = getattr(self, '_bot', None)
        if bot is None:
            return False
        try:
            from tasks.global_nav import GlobalNavigator
            nav = GlobalNavigator(
                bot, logger=self._log, stop_check=self._stop_check,
            )
            return bool(nav.navigate_to('idle_stage'))
        except Exception as _e:      # 全局导航不可用/异常 → 静默降级
            self._log(f'[警告] 全局导航不可用，改用旧导航: {_e}')
            return False

    def navigate_to_idle_stage(self) -> bool:
        """导航到挂机关卡页（2级，推图战斗入口；状态机驱动）。

        循环：
        1. 检测当前画面（detect_phase）
        2. 根据画面决定操作
        3. 无法识别 → ESC退回上一层，回到1

        导航状态机（状态 → 操作 → 下一状态）：
          town         → 点"玩法目录"     → playbook
          playbook     → 点"挂机关卡"     → main
          activity     → 点"常规玩法"     → playbook
          options_menu → ESC              → 重新detect
          quest_panel  → ESC              → 重新detect
          battle       → 等待结束         → 重新detect
          result       → 关闭             → 重新detect
          unknown      → ESC (no_progress) → 重新detect (连续3次→停止)

        Returns:
            bool: True 表示导航成功到达挂机关卡页（2级），False 表示失败
        """
        # v1.9.11（导航统一化）：**优先走全局导航系统**——所有页面跳转
        # 收敛到 GlobalNavigator（统一判据/最短路径/统一脱离链）。全局
        # 导航失败时降级回本状态机（保持原有行为，不引入回归风险）。
        if self._navigate_via_global():
            return True

        max_iter: int = self._navigate_timeout
        no_progress: int = 0
        last_phase: Optional[str] = None

        # 预检查：已在挂机关卡页
        if self.is_target():
            self._log('[成功] 已在挂机关卡')
            return True

        for i in range(max_iter):
            if self._should_stop():
                return False

            phase: str = self._detector.detect_phase()
            # v1.8.29：未知画面补全局注册表识别结果（日志可诊断，动作不变）
            if phase == 'unknown':
                phase_hint: str = self._global_screen_hint()
                if phase_hint:
                    self._log(f'[信息] 导航中 | unknown{phase_hint}')
                else:
                    self._navlog(f'[信息] 导航中 | {_phase_zh(phase)}')
            else:
                self._navlog(f'[信息] 导航中 | {_phase_zh(phase)}')

            # ── 目标状态：已在挂机关卡页（2级） ──
            if phase == 'idle_stage':
                if self.is_target():
                    self._log(f'[成功] 导航完成 | {_phase_zh(phase)}')
                    return True
                self._navlog(f'[信息] 等待中 | {_phase_zh(phase)}')
                self._sleep(1)
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                continue

            # ── 备战界面（formation）：ESC退回挂机关卡页 ──
            if phase == 'formation':
                self._log('[信息] 在备战界面，ESC退回')
                self._input.send_esc()
                self._sleep(1)
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                continue

            # ── 公会/0 层界面：点"玩法目录" ──
            # v1.8.47：补 world/home——v1.7.0 拆分第二处尾巴（auto_farm
            # 主循环已修，navigator 漏网）；世界/家园缺分支曾落
            # unknown 兜底按 ESC → 0 层触发「退出游戏」弹窗死循环
            if phase in ('guild', 'town', 'world', 'home'):
                label: str = '公会' if phase == 'guild' else '世界/家园'
                self._log(f'[信息] 在{label}界面，点"玩法目录"')
                if self._ocr_and_click_for_navigate(
                    '玩法目录', tmpl_name='wanfa', y_min_ratio=0.2
                ):
                    self._sleep(1)
                    continue
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                self._sleep(1.5)
                continue

            # ── 玩法目录（playbook）：点"挂机关卡" ──
            if phase == 'playbook':
                self._log('[信息] 在玩法目录，点"挂机关卡"')
                if self._ocr_and_click_for_navigate(
                    '挂机关卡', y_min_ratio=0.2
                ):
                    self._sleep(1)
                    continue
                # v1.8.94（真机 14:23 反馈）：卡片不在当前屏（玩法目录
                # 记住上次滚动位置）→ 滚动查找（下滚到底 + 回滚到顶），
                # 画面不再变化即收敛，labyrinth/goddess 同款自愈
                if self._scroll_find_and_click('挂机关卡'):
                    self._sleep(1)
                    continue
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                self._sleep(1.5)
                continue

            # ── 活动菜单（activity）：点"常规玩法" ──
            if phase == 'activity':
                self._log('[信息] 在活动菜单，点"常规玩法"')
                if self._ocr_and_click_for_navigate(
                    '常规玩法', tmpl_name='normal', y_min_ratio=0.2
                ):
                    self._sleep(1)
                    continue
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                continue

            # ── 选项菜单（options_menu）：ESC关闭 ──
            if phase == 'options_menu':
                self._log('[信息] 在选项菜单，ESC关闭')
                self._input.send_esc()
                self._sleep(1)
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                continue

            # ── 任务面板（quest_panel）：ESC关闭 ──
            if phase == 'quest_panel':
                self._log('[信息] 在任务面板，ESC关闭')
                self._input.send_esc()
                self._sleep(1)
                if phase == last_phase:
                    no_progress += 1
                else:
                    no_progress = 0
                last_phase = phase
                if no_progress >= 3:
                    self._log(
                        f'[警告] 导航卡住 | {_phase_zh(phase)} ({no_progress}次)'
                    )
                    return False
                continue

            # ── 战斗中（battle）：等待结束 ──
            if phase == 'battle':
                self._log(
                    f'[信息] 在战斗中，等待结束'
                    f'（最多{self._battle_timeout}秒）...'
                )
                for _ in range(self._battle_timeout):
                    if self._should_stop():
                        return False
                    self._sleep(1)
                    if _ % 3 == 2 and (
                        self._detector.detect_phase() != 'battle'
                    ):
                        break
                continue

            # ── 结算界面（result）：关闭 ──
            if phase == 'result':
                self._log('[信息] 在结算界面，关闭')
                if self._battle_monitor is not None:
                    self._battle_monitor.step_close_result()
                else:
                    self._close_result_fn()
                continue

            # ── 加载中（loading）：等待 ──
            if phase == 'loading':
                self._sleep(5)
                continue

            # ── 登录界面（login）：点击开始游戏 ──
            if phase == 'login':
                if self._handle_login_fn():
                    continue
                # fallback: simple login click
                for attempt in range(3):
                    if self._should_stop():
                        return False
                    items = self._screen.ocr_screen()
                    pos = self._recognizer.find_text_exact(
                        items, '开始游戏'
                    )
                    if pos:
                        self._input.click_at(
                            pos[0], pos[1], '开始游戏'
                        )
                        self._sleep(3)
                        break
                    self._sleep(1)
                continue

            # ── 退出确认弹窗（confirm_dialog）：优先点X关闭，否则ESC取消退出 ──
            if phase == 'confirm_dialog':
                self._handle_confirm_dialog()
                continue

            # ── 无法识别（unknown 或未知 phase）：ESC + 等待 ──
            if phase == last_phase:
                no_progress += 1
            else:
                no_progress = 0
            last_phase = phase

            if no_progress >= 3:
                self._log(
                    f'[警告] 无法识别画面 | {_phase_zh(phase)} ({no_progress}次)'
                )
                return False

            self._navlog(f'[信息] 等待重试 | {_phase_zh(phase)}'
                                 f'{self._global_screen_hint()}')
            self._input.send_esc()
            self._sleep(3)

        self._log('[警告] 导航超时')
        return False
