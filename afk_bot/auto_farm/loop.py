"""
afk_bot 核心编排模块

AFKBotCore 整合所有子模块，提供主循环编排、生命周期管理、
分辨率切换、网络检测等功能。

从 afk_bot.py 提取，协调 Navigator / BattleMonitor / FormationManager /
PhaseDetector / StatsTracker / ScreenCapture / InputController /
TextRecognizer / OCRMemory / CoordSystem 等子模块。
"""

import os
import sys
import time
import json
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import win32gui
import win32con
import win32api

from afk_bot.config import LEVEL_PREFIXES
from afk_bot.config import VERSION, _SNAP_TRIM, _BTN_POS, _BTN_POS_PORTRAIT
from afk_bot.infra.coordinate import CoordSystem
from afk_bot.infra.screen import ScreenCapture
from afk_bot.infra.input import InputController
from afk_bot.infra.ocr_memory import OCRMemory, OCRStrategy
from afk_bot.infra.ocr_engine import TextRecognizer
from afk_bot.services.detector import PhaseDetector
from afk_bot.auto_farm.battle import BattleMonitor
from afk_bot.auto_farm.formation import FormationManager
from afk_bot.infra.stats import StatsTracker
from afk_bot.services.navigator import Navigator


class AFKBotCore:
    """核心编排器 — 整合所有子模块，提供主循环和生命周期管理。

    构造器中创建所有子模块（CoordSystem → ScreenCapture → InputController →
    OCRMemory → TextRecognizer → PhaseDetector → StatsTracker →
    BattleMonitor → FormationManager → Navigator），
    持有 _stop / _pause / _hwnd 等全局状态。

    Attributes:
        _stop: 停止标志（bool）
        _stop_event: 停止事件（threading.Event）
        _pause: 暂停事件（threading.Event，set=运行，clear=暂停）
        _hwnd: Unity 窗口句柄
        _cfg: 原始配置字典
        config: 运行时配置字典
        stats: 统计字典（StatsTracker.stats 引用）
        log: 日志回调
        set_status: GUI 状态回调
    """

    VERSION: str = VERSION

    def __init__(self, cfg: Dict[str, Any]):
        """初始化 AFKBotCore 及所有子模块。

        Args:
            cfg: 配置字典，需包含：
                - log_callback: 日志回调
                - status_callback: GUI 状态回调
        """
        # ── 全局状态 ──────────────────────────────────────────
        self._stop: bool = False
        self._stop_event: threading.Event = threading.Event()
        self._pause: threading.Event = threading.Event()
        self._pause.set()
        self._hwnd: Optional[int] = None
        self._cfg: Dict[str, Any] = cfg

        # 日志和状态回调
        self.log: Callable[[str], None] = cfg.get(
            'log_callback', print
        )
        self.set_status: Callable[..., None] = cfg.get(
            'status_callback', lambda **kw: None
        )

        # ── 配置加载 ──────────────────────────────────────────
        exe_dir: str = os.path.dirname(os.path.abspath(sys.argv[0]))
        self.config_file: str = os.path.join(exe_dir, 'afkj_config.json')
        self._config_dir: str = exe_dir

        # MaaOCR 引擎（全局唯一）
        from maa_ocr import MaaOCR
        self._ocr: MaaOCR = MaaOCR()
        self._log('info', '[配置] OCR引擎已初始化')

        self.config: Dict[str, Any] = self._load_config()

        # 配置常量
        # v1.5.0 精简：删除阈值设定（auto_swap/alternate_switch），
        # 切换挑战模式唯一依据 = 推荐阵容用完（> 按钮消失）
        self.max_formation_index: int = int(
            self._cfg.get('max_formation_index', 10)
        )
        self.navigate_timeout: int = int(
            self._cfg.get('navigate_timeout', 40)
        )
        self.battle_timeout: int = int(
            self._cfg.get('battle_timeout', 90)
        )

        # 战斗模式（v1.5.0 固定：逐次战斗 + 交替模式）
        self.battle_mode: str = 'manual'
        self.run_mode: str = 'alternate'

        # 调试日志开关（正式版关闭，测试版开启）
        self._verbose: bool = self._cfg.get('verbose', False)

        # 未推进计数
        self.manual_no_progress_count: int = 0
        # 战斗开始时间戳（用于 unknown 阶段防护）
        self._round_start_time: float = 0.0
        # result 循环计数器
        self._result_loop_count: int = 0
        # 任务结束指标（由 run_auto_farm 注入；None = 无限）
        self._task_max_time: Optional[int] = None
        self._task_max_turns: Optional[int] = None
        self._task_start_time: float = 0.0
        self._task_start_turns: int = 0
        # 全部通关标记（区分"自然完成"与"用户停止"）
        self._task_completed: bool = False

        # ── 创建子模块 ────────────────────────────────────────
        # 1. 坐标系统
        self._coord_system: CoordSystem = CoordSystem(
            hwnd=self._hwnd, trim=_SNAP_TRIM, log_callback=self._log
        )

        # 2. 截图引擎
        self._screen_capture: ScreenCapture = ScreenCapture(
            hwnd=self._hwnd,
            ocr_engine=self._ocr,
            coord_system=self._coord_system,
            log_callback=self._log,
        )

        # 3. 输入控制器
        self._input_controller: InputController = InputController(
            coord_system=self._coord_system,
            screen_capture=self._screen_capture,
            log_callback=self._log,
        )

        # 4. OCR 记忆
        self._ocr_memory: OCRMemory = OCRMemory(
            os.path.join(self._config_dir, 'ocr_memory.json')
        )
        self._log(
            'info',
            f'[数据] OCR记忆已加载 | '
            f'条数={len(self._ocr_memory.memory)}'
        )

        # 5. 文本识别器
        self._text_recognizer: TextRecognizer = TextRecognizer(
            screen_capture=self._screen_capture,
            ocr_memory=self._ocr_memory,
            log_callback=self._log,
        )

        # 6. 阶段检测器
        self._phase_detector: PhaseDetector = PhaseDetector(
            screen_capture=self._screen_capture,
            recognizer=self._text_recognizer,
            log_callback=self.log,
        )

        # 7. 统计追踪器
        self._stats_tracker: StatsTracker = StatsTracker(
            status_callback=self.set_status,
            log_callback=self.log,
        )
        self.stats: Dict[str, Any] = self._stats_tracker.stats

        # 8. 战斗监控器
        self._battle_monitor: BattleMonitor = BattleMonitor(
            screen_capture=self._screen_capture,
            recognizer=self._text_recognizer,
            detector=self._phase_detector,
            input_controller=self._input_controller,
            stats_tracker=self._stats_tracker,
            log_callback=self.log,
            status_callback=self.set_status,
            config=self.config,
            stop_check=self._should_stop,
            pause_check=self._should_continue,
            battle_mode=self.battle_mode,
        )

        # 9. 阵容管理器
        self._formation_manager: FormationManager = FormationManager(
            screen_capture=self._screen_capture,
            recognizer=self._text_recognizer,
            input_controller=self._input_controller,
            detector=self._phase_detector,
            log_callback=self.log,
            config=self.config,
            stats=self._stats_tracker,
            stop_check=self._should_stop,
            pause_check=self._should_continue,
            res_path=self._res_path,
            navigate_callback=lambda: (
                self._navigator.navigate_to_idle_stage()
                if hasattr(self, '_navigator') else False
            ),
            battle_mode=self.battle_mode,
        )

        # 10. 导航器（需要 battle_monitor 引用）
        self._navigator: Navigator = Navigator(
            screen_capture=self._screen_capture,
            recognizer=self._text_recognizer,
            input_controller=self._input_controller,
            detector=self._phase_detector,
            log_callback=self.log,
            config=self.config,
            stop_check=self._should_stop,
            battle_monitor=self._battle_monitor,
            res_path=self._res_path,
        )

        # 更新 FormationManager 的 navigate_callback
        self._formation_manager._navigate_callback = (
            lambda: self._navigator.navigate_to_idle_stage()
        )

    # v1.8.32 日志中文化：detector phase → 中文界面名
    _PHASE_ZH = {
        'world': '世界', 'home': '家园', 'guild': '公会',
        'playbook': '玩法目录', 'activity': '活动页',
        'idle_stage': '挂机关卡页', 'labyrinth': '异界迷宫',
        'formation': '备战', 'battle': '战斗中', 'result': '结算',
        'confirm_dialog': '确认弹窗', 'pause': '暂停', 'loading': '加载中',
        'login': '登录', 'options_menu': '选项菜单',
        'quest_panel': '任务面板', 'unknown': '未识别画面',
    }

    def _phase_zh(self, phase: str) -> str:
        return self._PHASE_ZH.get(phase, phase)

    # ── 日志分层方法 ──────────────────────────────────────────

    def _log(self, level: str, msg: str) -> None:
        """统一日志输出，自动添加级别前缀。

        Args:
            level: 日志级别 — 'info'/'warn'/'error'/'debug'
            msg: 日志消息内容
        """
        # 非调试模式下过滤掉 ℹ️ 类调试日志（保持日志简洁）
        if not getattr(self, '_verbose', True) and msg.startswith('ℹ️'):
            return
        # v1.8.49：改用中性层单一真相源（此前本地重复一份，易漂移）
        prefix: str = LEVEL_PREFIXES.get(level, '[信息]')
        self.log(f'{prefix} {msg}')

    # ── 停止/暂停检查回调 ────────────────────────────────────

    def _should_stop(self) -> bool:
        """停止检查回调。"""
        return self._stop

    def _should_continue(self) -> bool:
        """暂停检查回调 — True 表示继续运行。"""
        return self._pause.is_set() and not self._stop

    def _check_pause(self) -> bool:
        """等待恢复暂停，返回 True 表示继续运行。"""
        while not self._pause.wait(timeout=0.2):
            if self._stop:
                return False
        return not self._stop

    # ── 资源路径 ──────────────────────────────────────────────

    def _res_path(self, relative_path: str) -> str:
        """获取资源文件的绝对路径（兼容开发模式和 PyInstaller exe 模式）。

        PyInstaller 打包后，资源文件被解压到 sys._MEIPASS 临时目录。
        开发模式下，使用当前文件所在目录作为基准路径。

        Args:
            relative_path: 相对路径

        Returns:
            str: 绝对路径
        """
        try:
            base_path: str = sys._MEIPASS
        except Exception as _e:
            self.log(f"[调试] _res_path() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            # v1.8.49：core.py 已并入 auto_farm/loop.py（原注释过时）
            base_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_path, relative_path)

    # ── 配置加载 ──────────────────────────────────────────────

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件，如果不存在则创建默认配置。

        Returns:
            Dict: 合并后的配置字典
        """
        default_config: Dict[str, Any] = {
            'stuck': {
                'sample_interval': 0.3,
                'threshold': 10,
                'max_recovery_attempts': 3,
            },
            'battle': {
                'miss_threshold': 3,
                'min_duration': 30,
                'ocr_interval_initial': 1,
                'ocr_interval_stable': 3,
                'max_diff': 10,
                'max_iter_navigate': 40,
                'max_attempts_close_result': 5,
            },
        }
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    user_config: dict = json.load(f)
                for section in user_config:
                    if section in default_config:
                        if (
                            isinstance(default_config[section], dict)
                            and isinstance(user_config[section], dict)
                        ):
                            default_config[section].update(
                                user_config[section]
                            )
                        else:
                            default_config[section] = user_config[section]
                    else:
                        default_config[section] = user_config[section]
        except Exception as e:
            self._log('warn', f'配置加载失败，使用默认配置 | {e}')
        if not os.path.exists(self.config_file):
            self.config = default_config
            self._save_config()
        return default_config

    def _save_config(self) -> None:
        """保存当前配置到文件（供 GUI 调用）。"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self._log('error', f'保存配置失败 | {e}')

    # ── 生命周期 ──────────────────────────────────────────────

    def stop(self) -> None:
        """停止自动化。"""
        self._stop = True
        self._stop_event.set()
        self._pause.set()

    def pause(self) -> None:
        """暂停自动化。"""
        if not self._pause.is_set():
            return
        self._pause.clear()
        self._log('info', '已暂停')

    def resume(self) -> None:
        """恢复自动化。"""
        if self._pause.is_set():
            return
        self._pause.set()
        self._log('info', '已恢复')

    # ── 窗口管理 ──────────────────────────────────────────────

    def find_and_focus(self) -> Optional[int]:
        """查找并聚焦 Unity 游戏窗口。

        Returns:
            int: 窗口句柄，失败返回 None
        """
        hwnd: Optional[int] = self._screen_capture.find_and_focus()
        if hwnd is not None:
            self._hwnd = hwnd
            self._coord_system._hwnd = hwnd
            self._screen_capture._hwnd = hwnd
        return hwnd

    # ── 自动模式结算关闭 ──────────────────────────────────────

    def _close_result_and_go_idle_stage(self) -> None:
        """自动战斗结束后关闭结算/结果界面，ESC退回挂机关卡页。"""
        self._log('info', '关闭结算界面...')
        # 等待结算界面出现
        for _ in range(10):
            if self._stop:
                return
            phase = self._phase_detector.detect_phase()
            if phase in ('result', 'formation', 'idle_stage', 'battle'):
                break
            time.sleep(1)
        # ESC 逐层退出
        for _ in range(5):
            if self._stop:
                return
            self._input_controller.send_esc()
            time.sleep(1.5)
            phase = self._phase_detector.detect_phase()
            if phase == 'idle_stage':
                self._log('info', '已返回挂机关卡')
                return
            if phase == 'confirm_dialog':
                # ESC 触发了"退出游戏"确认弹窗（ESC陷阱），点X关闭后重检
                self._log('info', 'ESC触发退出确认弹窗，点击X关闭')
                self._navigator._handle_confirm_dialog()
                phase = self._phase_detector.detect_phase()
                if phase == 'idle_stage':
                    self._log('info', '已返回挂机关卡')
                    return
                continue
            if phase != 'result':
                time.sleep(1)
        # 兜底：点击空白处关闭
        self._log('info', 'ESC未生效，尝试点击空白处')
        self._input_controller.click_abs(960, 540)

    # ── 网络断开检测 ──────────────────────────────────────────

    def _check_network_disconnect(self) -> bool:
        """检测并处理网络断开弹窗。

        Returns:
            bool: True 表示已处理（需重新检测 phase）
        """
        try:
            items: List[Tuple] = self._screen_capture.ocr_screen()
            texts: List[str] = [
                t for _, _, t, c, *_ in items
            ]
            if any(
                '连接已断开' in t
                or '重新连接' in t
                or '是否要重新连接' in t
                or '断开' in t
                for t in texts
            ):
                self._log('warn', '检测到网络断开弹窗，点击重连')
                self._input_controller.click_ratio(
                    0.625, 0.688, label='重连确认'
                )
                self._stop_event.wait(3)
                return True
        except Exception as e:
            self._log('warn', f'网络断开检测异常: {e}')
        return False

    # ── 导航恢复 ──────────────────────────────────────────────

    def _startup_probe(self) -> str:
        """启动探测（v1.8.7 对齐 BaseTask.startup_probe 契约三态）。

        推图任务的执行域 = 挂机推进可用的任意界面（2级挂机关卡页 /
        备战 / 战斗中 / 结算中等）——启动时若已在此类界面，主循环的
        各 phase 分支自会续跑（备战就开战、战斗中则等待结束、结算则
        关闭并统计），无需退回 0 层重导航。仅当在 0层/玩法目录/未知
        时才需要导航到挂机关卡页。

        Returns:
            str: 'ready'（已在执行域，直接进主循环）| 'clear'（需导航）
        """
        phase: str = self._phase_detector.detect_phase()
        # v1.8.44：改**正向白名单**——执行域仅限挂机关卡页/备战/战斗/结算。
        # 旧反向排除（town/playbook/unknown）漏了 guild/activity/world/home，
        # 公会界面启动曾被误判"已在推图执行域"（真机 9/7 日志误导）
        # v1.8.46：result 移出白名单——其它活动的结算界面启动 ≠ 推图
        # 执行域（真机 9/7），走导航收敛（navigate 自带结算关闭）
        if phase in ('idle_stage', 'formation', 'battle'):
            return 'ready'
        return 'clear'

    def _recover_to_idle_stage(self) -> bool:
        """恢复到挂机关卡流程（不要求回 0 层，检测到任意可用状态即继续）。

        可用状态优先级：
        1. 挂机关卡页（2级：幻灵挑战/挑战可见）
        2. 备战界面（通关阵容/自动挑战可见）→ 继续推进
        3. 玩法目录界面 → 继续导航
        4. 其余界面 → 赴战全流程

        Returns:
            bool: True 表示恢复成功
        """
        self._stats_tracker.increment('recovery_count')
        self._stats_tracker.push()
        self._log('info', '尝试恢复')

        phase: str = self._phase_detector.detect_phase()
        if phase == 'idle_stage':
            self._log('info', '[成功] 已在挂机关卡')
            return True
        if phase == 'formation':
            self._log('info', '已在备战界面（继续推进）')
            return True
        if phase == 'playbook':
            self._log('info', '在玩法目录界面，继续导航')
            return self._navigator.navigate_to_idle_stage()
        if phase in ('battle', 'result', 'town'):
            self._log('info', f'当前状态={self._phase_zh(phase)}，退出键回退')
            self._input_controller.send_esc()
            self._stop_event.wait(1.5)

        # ESC后再次检测
        phase2: str = self._phase_detector.detect_phase()
        if phase2 == 'idle_stage':
            self._log('info', '[成功] 退出键后回到挂机关卡')
            return True
        if phase2 == 'formation':
            self._log('info', '[成功] 退出键后回到备战')
            return True
        if phase2 == 'playbook':
            self._log('info', '继续导航')
            return self._navigator.navigate_to_idle_stage()
        if phase2 == 'battle':
            self._log('info', '战斗中')
            return True

        # 兜底：完整导航流程
        if not self._navigator.navigate_to_idle_stage():
            self._log('error', '恢复失败')
            return False
        self._log('info', '[成功] 恢复成功')
        return True

    # ── 推送到 GUI ────────────────────────────────────────────

    def _push_stats(self) -> None:
        """推送统计数据到 GUI。"""
        self._stats_tracker.push()

    # ── 赛季关卡检测 ──────────────────────────────────────────

    def _detect_season_stages(self) -> Tuple[int, int]:
        """检测备战界面的赛季关卡数。

        Returns:
            (huanling, normal) 幻灵关卡数和挂机关卡数，(0, 0) 表示检测失败
        """
        items: List[Tuple] = self._screen_capture.ocr_screen()
        return self._phase_detector._detect_season_stages(items)

    def _handle_pause_menu(self) -> bool:
        """检测并处理战斗暂停界面。

        暂停界面特征：同时出现"暂停中" + 至少一个操作按钮
        （"继续"/"再次挑战"/"退出战斗"）。
        处理方式：点击"继续"退出暂停。

        Returns:
            True  — 已检测到暂停界面并已处理
            False — 未检测到暂停界面
        """
        items: List[Tuple] = self._screen_capture.ocr_screen()
        texts: List[str] = [
            t for _, _, t, c, *_ in items if c >= 0.3
        ]

        # 关键特征：必须包含"暂停中"
        has_pause: bool = any('暂停中' in t for t in texts)

        # 辅助特征：至少包含一个操作按钮
        _action_kws: List[str] = ['继续', '再次挑战', '退出战斗']
        has_action: bool = any(
            any(kw in t for kw in _action_kws) for t in texts
        )

        if not (has_pause and has_action):
            return False

        self._log('info', '[暂停] 检测到战斗暂停界面')

        # 优先点击"继续"
        pos = self._recognizer.find_text(items, '继续')
        if pos:
            self._input_controller.click_at(pos[0], pos[1], '暂停_继续')
            self._log('info', '[暂停] 已点击"继续"')
            self._stop_event.wait(2)
            return True

        # 兜底：点击"再次挑战"
        pos = self._recognizer.find_text(items, '再次挑战')
        if pos:
            self._input_controller.click_at(pos[0], pos[1], '暂停_再次挑战')
            self._log('info', '[暂停] 已点击"再次挑战"')
            self._stop_event.wait(2)
            return True

        # 兜底：点击"退出战斗"
        pos = self._recognizer.find_text(items, '退出战斗')
        if pos:
            self._input_controller.click_at(pos[0], pos[1], '暂停_退出战斗')
            self._log('info', '[暂停] 已点击"退出战斗"')
            self._stop_event.wait(2)
            return True

        self._log('warn', '[暂停] 检测到暂停但未找到操作按钮')
        return False

    # ── run() 主入口 ──────────────────────────────────────────

    def run(self) -> None:
        """统一入口：初始化并调用 _run_main_loop()。"""
        try:
            self._run_main_loop()
        except Exception as e:
            self._log('error', f'致命错误: {e}')
            import traceback
            traceback.print_exc()
        finally:
            self.set_status(running=False)

    # ── 主循环 ────────────────────────────────────────────────

    def _run_main_loop(self) -> None:
        """统一的主循环逻辑（v1.5.0：仅交替模式 + 逐次战斗）。

        使用实例变量：
            self.battle_mode: 'manual'（逐次战斗，固定）
            self.run_mode: 'alternate'（交替模式，固定）
        """
        # ── 初始化 ──────────────────────────────────────────
        self._stop = False
        self._stop_event.clear()
        self._pause.set()
        # 首次循环标志：启动时若在结算界面（result），_handle_common_phase
        # 处理后会误判 _pending_defeat（把胜利当失败计数+换阵），首次循环排除
        self._first_loop: bool = True
        # 推荐阵容用完标志：进入 formation 阶段时检测 > 按钮是否存在；
        # defeat 分支据此立即切换挑战模式（v1.5.0：唯一切换依据）
        self._formation_exhausted: bool = False
        self.set_status(
            running=True,
            round=0, mode='', phase='init',
            script_start=time.time()
        )

        self._log(
            'info',
            f'AFKJ自动化工具 {VERSION} 启动'
        )
        self._log(
            'info',
            f'模式: 交替（幻灵+挑战） | 逐次战斗'
            f' | 轮数: 无限'
        )

        # v2.2.0：**移除本处的 F12 注册**。
        # ① 冗余：GUI 已在 `afk_gui.App._bind_global_hotkeys()` 全局接管 F12，
        #    其回调最终调 `facade.stop()` → `core.stop()` → 本类的 `stop()`，
        #    效果完全一致（已核对调用链）。
        # ② 有害：本行在 `run()` 内，**每次运行都会 add_hotkey 一次** →
        #    回调累积，跑 N 次后一次 F12 会触发 N 个陈旧 stop。
        # 若将来需要在无 GUI 环境用 F12：需重新 `import keyboard`，并加
        # "只注册一次"的守卫 + 退出时 remove_hotkey，不要恢复裸注册。
        # keyboard.add_hotkey('f12', self.stop)

        self._hwnd = self.find_and_focus()
        if not self._hwnd:
            self._log('error', '未找到游戏窗口')
            return

        cl_left, cl_top, cl_right, cl_bottom = win32gui.GetClientRect(
            self._hwnd
        )
        cl_w: int = cl_right - cl_left
        cl_h: int = cl_bottom - cl_top
        self._log('info', f'窗口就绪 | {cl_w}x{cl_h}')

        # 交替模式：从幻灵挑战开始
        current_mode: str = 'huanling'

        formation_idx: int = 0

        mode_prev_stage: Dict[str, int] = {
            'huanling': self.stats.get('huanling_stage', 0),
            'normal': self.stats.get('normal_stage', 0),
        }

        unknown_count: int = 0
        need_switch: bool = False

        self.manual_no_progress_count = 0

        # ── 启动定位（v1.8.7：任意界面启动 → 识别 → 收敛主循环）──
        # 已在推图执行域（挂机关卡页/备战/战斗中/结算）→ 'ready' 直接续跑，
        # 不做无谓回退；仅在 0层/玩法目录/未知才导航（framework
        # BaseTask.startup_probe 契约三态，此处为 core 层实现）
        if self._startup_probe() == 'clear':
            self._log('info', '导航中（启动不在挂机执行域）')
            if not self._navigator.navigate_to_idle_stage():
                self._screen_capture.debug_screenshot('nav_failed_init')
                self._log('error', '导航失败')
                return
            phase = 'idle_stage'
            self._log('info', '[成功] 导航完成')
        else:
            self._log('info', '[启动定位] 已在推图执行域，直接续跑')

        # ── 主循环 ────────────────────────────────────────
        while not self._stop:
            self._pause.wait()
            self._check_network_disconnect()

            # 任务结束指标检查（时间/轮次，OR 逻辑；任一满足即结束）
            if self._task_completion_reached():
                self._log(
                    'info',
                    f'[自动推图] 任务指标达成: '
                    f'{self._task_completion_reason()}，结束任务'
                )
                break

            # 1. 检测当前 phase
            phase = self._phase_detector.detect_phase()
            prev_phase: str = phase  # 保存原始 phase，用于追踪通用处理器的状态变化
            self._log('info', f'[阶段] {self._phase_zh(phase)}')

            # 处理通用阶段（可能改变 phase，如 result→formation/idle_stage）
            phase = self._navigator._handle_common_phase(phase)

            # 追踪通用处理器是否消耗了 result 阶段
            # 当 result 被通用处理器关闭后，核心的战斗处理逻辑会被跳过，
            # formation_idx/need_switch 等需要在后续 formation 阶段补齐
            # 注意：仅在非首次循环且经历过 battle 分支后才有意义，
            # 启动时若画面在结算界面（result），不能把胜利误判为失败
            # 注意：_handle_common_phase 处理 result 时，BattleMonitor
            # 会记录 last_result（victory/defeat）；仅真实失败才按失败处理，
            # 胜利结算被抢先时不能误判为失败换阵
            self._pending_defeat: bool = (
                not self._first_loop
                and prev_phase == 'result' and phase != 'result'
                and getattr(
                    self._battle_monitor, 'last_result', 'defeat'
                ) == 'defeat'
            )
            self._first_loop = False

            # ── result 循环检测 ────────────────────────────
            if phase == 'result':
                self._result_loop_count += 1
                self._log(
                    'warn',
                    f'result循环 | 第{self._result_loop_count}次'
                )
                if self._result_loop_count >= 3:
                    self._log(
                        'warn',
                        'result循环超过3次，强制导航恢复'
                    )
                    self._navigator.navigate_to_idle_stage()
                    self._result_loop_count = 0
                    phase = self._phase_detector.detect_phase()
            elif phase not in ('unknown',):
                # 非 result 阶段重置计数器
                self._result_loop_count = 0

            # 停止检查：phase 处理后立即检查
            if self._stop:
                break

            if phase == 'battle':
                self._pending_defeat = False
                unknown_count = 0
                self._result_loop_count = 0
                self._log('info', '战斗中，等待结束...')

                result: str = (
                    self._battle_monitor.wait_for_manual_battle_end()
                )

                prev_mode: str = current_mode

                # 停止检查：战斗结束后立即检查
                if self._stop:
                    break

                stats: Dict[str, Any] = self.stats
                stats['total_battles'] = stats.get('total_battles', 0) + 1
                self.set_status(round=stats['total_battles'])

                if result == 'victory':
                    # 胜利后重置推荐阵容用完标志（新关卡推荐阵容可能变化）
                    self._formation_exhausted = False
                    if prev_mode == 'huanling':
                        stats['huanling_wins'] = (
                            stats.get('huanling_wins', 0) + 1
                        )
                    else:
                        stats['normal_wins'] = (
                            stats.get('normal_wins', 0) + 1
                        )
                    self._push_stats()

                    cb_result = self._formation_manager.click_challenge_btn(
                        'victory', current_mode
                    )
                    if cb_result == 'all_completed':
                        self._log(
                            'info',
                            '[成功] 当前挑战已全部完成，停止脚本'
                        )
                        self.stop()
                        break
                    elif cb_result == 'mode_completed':
                        self._log(
                            'info',
                            '[成功] 当前挑战已通关，'
                            'ESC返回挂机关卡页切换挑战类型'
                        )
                        self._close_result_and_go_idle_stage()
                    need_switch = False
                    formation_idx = 0
                    self.manual_no_progress_count = 0

                    if current_mode != prev_mode:
                        self._log(
                            'info',
                            f'切换 | '
                            f'{"幻灵" if prev_mode == "huanling" else "挂机"}'
                            f'→{"幻灵" if current_mode == "huanling" else "挂机"}'
                        )

                elif result == 'stopped':
                    # 用户主动停止，立即跳出
                    break

                else:
                    # 记录挑战次数（不论胜负）
                    if prev_mode == 'huanling':
                        stats['huanling_wins'] = stats.get('huanling_wins', 0) + 1
                    else:
                        stats['normal_wins'] = stats.get('normal_wins', 0) + 1
                    self._push_stats()

                    cb_result = self._formation_manager.click_challenge_btn(
                        'defeat', current_mode
                    )
                    if cb_result == 'all_completed':
                        self._log(
                            'info',
                            '[成功] 当前挑战已全部完成，停止脚本'
                        )
                        self.stop()
                        break
                    elif cb_result == 'mode_completed':
                        self._log(
                            'warn',
                            '未找到挑战按钮，'
                            'ESC返回挂机关卡页重新检测'
                        )
                        self._close_result_and_go_idle_stage()
                        continue
                    self.manual_no_progress_count += 1
                    cnt: int = self.manual_no_progress_count
                    lbl: str = (
                        '幻灵挑战' if prev_mode == 'huanling'
                        else '挑战'
                    )
                    self._log(
                        'warn',
                        f'[{lbl}] 未推进 | 失败{cnt}次'
                    )

                    # v1.5.0：切换挑战模式的唯一依据 = 推荐阵容用完（> 按钮消失）
                    should_switch_challenge = self._formation_exhausted

                    if not should_switch_challenge:
                        # > 按钮还在：换下一套阵容
                        formation_idx += 1
                        if formation_idx >= self.max_formation_index:
                            # v1.5.1 兜底：> 检测未触发但阵容数已达上限
                            # （保护：检测失效时也不会死循环）
                            self._log(
                                'info',
                                f'所有阵容试完（formation_idx={formation_idx}），'
                                f'强制切换挑战类型'
                            )
                            should_switch_challenge = True
                            formation_idx = 0

                    if should_switch_challenge:
                        # > 按钮消失 / 阵容用尽：切换挑战类型
                        self._log(
                            'info',
                            '推荐阵容用完，切换挑战类型'
                        )
                        self._formation_manager.navigate_to_other_challenge(
                            current_mode
                        )
                        current_mode = (
                            'normal' if current_mode == 'huanling'
                            else 'huanling'
                        )
                        self.manual_no_progress_count = 0
                        formation_idx = 0
                        need_switch = False
                    else:
                        need_switch = True

                # ── 判断本轮是否推进了关卡 ─────────────────
                if result == 'victory':
                    stats['stage_progress'] = (
                        stats.get('stage_progress', 0) + 1
                    )
                    stats['本轮进度'] = stats['stage_progress']
                    self._log('info', '[成功] 推进+1关')
                    self._push_stats()
                    self._log(
                        'info',
                        '逐次战斗模式：胜利，等待进入备战界面'
                    )

            elif phase == 'formation':
                stats = self.stats
                self._log('info', f'备战界面（{current_mode}）')

                # 注意：不能在面板未打开时检测 has_switch_button（> 在面板内）。
                # _formation_exhausted 由 switch_formation 切换时检测（> 按钮消失）。
                # 这里不重置：_pending_defeat 块需要读取上一轮 prepare 后的值，
                # 统一在 _pending_defeat 块处理完后、prepare 前重置。

                hl, nm = self._detect_season_stages()
                if hl > 0:
                    stats['huanling_stage'] = hl
                if nm > 0:
                    stats['normal_stage'] = nm
                if hl > 0 or nm > 0:
                    stats['current_stage'] = max(hl or 0, nm or 0)
                    self._log(
                        'info',
                        f'[数据] 当前关卡: {stats["current_stage"]}'
                        f'（幻灵:{hl}, 挂机:{nm}）'
                    )

                self._push_stats()

                # ── 处理通用处理器消耗 result 后的遗留逻辑 ──
                # 当 navigator 的 _handle_common_phase 处理了 result 阶段
                # （点击"再次挑战"→进入备战），核心的战斗处理被跳过，
                # 需要在这里补齐 formation_idx 递增和 need_switch 设置。
                if self._pending_defeat:
                    # ── 补齐被跳过的战斗统计 ──────────────
                    stats = self.stats
                    stats['total_battles'] = stats.get('total_battles', 0) + 1
                    self.set_status(round=stats['total_battles'])
                    if current_mode == 'huanling':
                        stats['huanling_wins'] = stats.get('huanling_wins', 0) + 1
                    else:
                        stats['normal_wins'] = stats.get('normal_wins', 0) + 1
                    self._push_stats()

                    # ── 阵容切换逻辑 ────────────────────────
                    self.manual_no_progress_count += 1
                    cnt: int = self.manual_no_progress_count
                    lbl: str = (
                        '幻灵挑战' if current_mode == 'huanling'
                        else '挑战'
                    )
                    self._log(
                        'warn',
                        f'[{lbl}] 未推进 | 失败{cnt}次 | '
                        f'通用处理器处理'
                    )

                    # v1.5.0：切换挑战模式的唯一依据 = 推荐阵容用完（> 按钮消失）
                    should_switch_challenge: bool = self._formation_exhausted

                    if not should_switch_challenge:
                        # > 按钮还在：换下一套阵容
                        formation_idx += 1
                        if formation_idx >= self.max_formation_index:
                            # v1.5.1 兜底：> 检测未触发但阵容数已达上限
                            self._log(
                                'info',
                                f'所有阵容试完（formation_idx={formation_idx}），'
                                f'强制切换挑战类型'
                            )
                            should_switch_challenge = True
                            formation_idx = 0

                    if should_switch_challenge:
                        # > 按钮消失 / 阵容用尽：切换挑战类型
                        self._log(
                            'info',
                            '推荐阵容用完，切换挑战类型'
                        )
                        self._formation_manager.navigate_to_other_challenge(
                            current_mode
                        )
                        current_mode = (
                            'normal' if current_mode == 'huanling'
                            else 'huanling'
                        )
                        self.manual_no_progress_count = 0
                        formation_idx = 0
                        need_switch = False
                    else:
                        need_switch = True
                self._pending_defeat = False
                # 消费完上一轮"推荐阵容用完"标志后重置，
                # 供本次 prepare 合并 switch_formation 的新检测结果
                self._formation_exhausted = False

                if need_switch or formation_idx > 0:
                    switch_count: int = formation_idx
                    self._formation_manager.prepare_and_start_battle(
                        switch_count=switch_count,
                        battle_mode=self.battle_mode,
                    )
                    need_switch = False
                else:
                    self._formation_manager.prepare_and_start_battle(
                        switch_count=0,
                        battle_mode=self.battle_mode,
                    )
                # 合并 switch_formation 内的检测结果：
                # 切换阵容时若 > 按钮消失（已到最后一套）→ 标记推荐阵容用完
                self._formation_exhausted = (
                    self._formation_exhausted
                    or getattr(
                        self._formation_manager, '_switch_exhausted', False
                    )
                )
                # 记录战斗开始时间（用于unknown防护，避免战斗刚启动时误按ESC）
                self._round_start_time = time.time()
                self._stop_event.wait(4)  # 等战斗真正开始，避免 transition 误判

            elif phase == 'idle_stage':
                self._pending_defeat = False
                # 检测挂机关卡页有哪些挑战按钮
                available: Optional[str] = (
                    self._formation_manager.detect_available_challenge_mode()
                )
                if available is None:
                    # 两种按钮都不在 → 检查是否全部通关
                    _items = self._screen_capture.ocr_screen()
                    # v1.8.35：修正解包——items=(cy,cx,文本,置信度,x0,x1)，
                    # 旧式 *_,t,_c 取到的是坐标（int）→ 'in' 报类型错误
                    _texts = [
                        _t for _cy, _cx, _t, _c, *_ in _items
                        if isinstance(_c, (int, float)) and _c >= 0.5
                        and isinstance(_t, str)
                    ]
                    if any('已完成全部挑战' in t for t in _texts):
                        self._log(
                            'info',
                            '[挂机关卡] 检测到"已完成全部挑战"，'
                            '全部通关，停止脚本'
                        )
                        self._task_completed = True   # 自然完成（非用户停止）
                        # v1.8.40：只结束本任务，**不再调用 self.stop()**——
                        # stop() 会置位 bot._stop_event → 桥接的调度器停止
                        # 信号 → 后续任务被中止 + 全局导航瞬间失败（真机 9/6：
                        # 全通关后女神试炼被跳过）。自然完成 ≠ 用户停止
                        break
                    self._log(
                        'error',
                        '[挂机关卡] 未找到任何挑战按钮，等待重试'
                    )
                    self._stop_event.wait(2)
                    continue

                # 如果当前模式按钮缺失但另一模式存在，判定当前模式已通关
                if available != 'both' and available != current_mode:
                    mode_label_map: Dict[str, str] = {
                        'huanling': '幻灵挑战',
                        'normal': '普通挑战',
                    }
                    self._log(
                        'warn',
                        f'[挂机关卡] {mode_label_map.get(current_mode, current_mode)} '
                        f'按钮缺失，只有 {mode_label_map.get(available, available)} 可用，'
                        f'判定已通关，切换模式'
                    )
                    current_mode = available
                    formation_idx = 0
                    need_switch = False

                self._log(
                    'info',
                    f'挂机关卡页，点击挑战按钮（{current_mode}）'
                )
                self._formation_manager.ocr_click_challenge(current_mode)

                formation_timeout: int = 10
                formation_start: float = time.time()
                while time.time() - formation_start < formation_timeout:
                    self._pause.wait()
                    if self._stop:
                        break
                    phase = self._phase_detector.detect_phase()
                    if phase == 'formation':
                        self._log('info', '成功进入备战界面')
                        break
                    elif phase in ('playbook', 'town', 'world',
                                   'home', 'activity', 'guild'):
                        self._log(
                            'warn',
                            f'点击挑战后进入{phase}界面，'
                            f'导航回挂机关卡页'
                        )
                        self._navigator.navigate_to_idle_stage()
                        self._stop_event.wait(2)
                        self._formation_manager.ocr_click_challenge(
                            current_mode
                        )
                        formation_start = time.time()
                    elif phase == 'idle_stage':
                        self._log(
                            'warn',
                            '仍在挂机关卡，重新点击挑战按钮'
                        )
                        self._formation_manager.ocr_click_challenge(
                            current_mode
                        )
                    self._stop_event.wait(1)

                if phase != 'formation':
                    self._log(
                        'error',
                        '点击挑战按钮后未进入备战界面（超时）'
                    )

                continue

            elif phase in ('town', 'world', 'home', 'playbook',
                           'activity', 'guild'):
                self._log('info', f'在{self._phase_zh(phase)}界面，导航到挂机关卡页')
                self._navigator.navigate_to_idle_stage()
                phase = self._phase_detector.detect_phase()

            elif phase == 'pause':
                # 战斗暂停界面（_handle_common_phase已处理，这里兜底）
                self._log('info', '[阶段] 战斗暂停界面')
                if self._handle_pause_menu():
                    continue
                # 兜底：当作unknown处理
                self._stop_event.wait(2)

            elif phase == 'unknown':
                self._pending_defeat = False
                # ── 暂停界面检测（优先于所有其他逻辑） ──────
                if self._handle_pause_menu():
                    continue

                # ── 结算界面复查（秒杀场景兜底） ────────────
                # 极限推图秒杀：战斗几秒内失败，detect 的周期性截图
                # 可能恰好早于结算界面完全显示（截到战斗末尾帧/结算
                # 渲染前帧）→ OCR 无 result 关键词 → 误判 unknown →
                # 空转数秒。这里用无缓存 OCR 复查当前画面，发现结算
                # 关键词立即按结算处理，跳过空转。
                # 注意：必须用 snap()+_ocr_img()（无缓存）而非
                # ocr_screen()，避免缓存碰撞再次返回旧 OCR 结果。
                # v1.8.12：此类弹窗文字 1 秒周期闪烁（用户实测），单帧 OCR
                # 可能落在暗相 → 未命中时隔 1s 再扫一帧（对齐闪烁周期）。
                _result_kws: Tuple[str, ...] = (
                    '再次挑战', '战斗失败', '战败',
                    '胜利', '挂机收益提升',
                    '点击空白处关闭', '点击空白处继续', '点击关闭',
                )
                _rs_texts: List[str] = []
                _is_result_ui: bool = False
                for _blink_i in range(2):
                    try:
                        _rs_img, _rs_l, _rs_t, _rs_w, _rs_h = (
                            self._screen_capture.snap()
                        )
                        _rs_items = self._screen_capture._ocr_img(_rs_img)
                        _rs_texts = [
                            s for _, _, s, _c, *_ in _rs_items
                            if isinstance(_c, (int, float)) and _c >= 0.3
                        ]
                    except Exception as _e:
                        self.log(f"[调试] _run_main_loop() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
                        _rs_texts = []
                    _is_result_ui = any(
                        any(kw in s for kw in _result_kws) for s in _rs_texts
                    )
                    if _is_result_ui:
                        break
                    self._stop_event.wait(1)
                if _is_result_ui:
                    self._log(
                        'info',
                        '[信息] 结算复查命中，直接处理结算'
                        f' | 识别到: {_rs_texts[:8]}'
                    )
                    self._battle_monitor.step_close_result()
                    self._stop_event.wait(1)
                    continue

                # ── 战斗中角色全屏动画检测 ──────────────────
                # 战斗刚启动时，角色全屏大招动画会遮挡HUD，
                # OCR返回极少文字→误判为unknown。
                # 检测条件：刚进入战斗（_round_start_time < 30s）
                # + 全屏OCR文本数极少（<3项）→ 正常动画，忽略。
                if self._round_start_time and (time.time() - self._round_start_time) < 30:
                    try:
                        _items = self._screen_capture.ocr_screen()
                        # 格式: (cy, cx, text, score, x_min, x_max)
                        _text_count: int = sum(
                            1 for _i in _items
                            if len(_i) >= 4 and isinstance(_i[3], (int, float)) and _i[3] >= 0.3
                        )
                    except Exception as _e:
                        self.log(f"[调试] _run_main_loop() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
                        _text_count = 999  # 出错时保守处理
                    if _text_count < 3:
                        self._log(
                            'info',
                            '战斗中（角色全屏动画），'
                            f'忽略 unknown | OCR文本数={_text_count}'
                        )
                    else:
                        self._log(
                            'info',
                            '战斗刚启动，跳过 unknown 判定'
                        )
                    self._stop_event.wait(2)
                    continue

                unknown_count += 1
                self._log('warn', f'未知阶段（{unknown_count}/3）')

                if unknown_count >= 3:
                    self._log(
                        'warn',
                        '连续未知阶段3次，'
                        '尝试导航到挂机关卡页'
                    )
                    # 战斗中不导航
                    if self._round_start_time and (time.time() - self._round_start_time) < 120:
                        self._log('info', '[信息] 战斗进行中，等待...')
                        self._stop_event.wait(5)
                        unknown_count = 0
                        continue
                    self._navigator.navigate_to_idle_stage()
                    unknown_count = 0
                else:
                    # 战斗中不按 ESC（会触发游戏内暂停菜单！）
                    if self._round_start_time and (time.time() - self._round_start_time) < 30:
                        self._log('info', '[信息] 战斗中，不按 ESC')
                        self._stop_event.wait(3)
                    else:
                        self._input_controller.send_esc()
                        self._stop_event.wait(5)
                    phase = self._phase_detector.detect_phase()

            # 其他 phase 已由 _handle_common_phase() 处理

        # ── 循环结束 ──────────────────────────────────────
        self._log('info', '主循环结束')
        self.set_status(running=False)

    def run_auto_farm(self, max_time: Optional[int] = None,
                      max_turns: Optional[int] = None,
                      start_turns: int = 0) -> str:
        """自动推图任务入口（供 tasks 层 / TaskScheduler 调用）。

        设计原则：复用现有 _run_main_loop()，不复制、不修改任何推图逻辑。
        推图的所有边界处理（卡死恢复、掉线重连、结算误判、首次循环标志、
        连败切换等）仍集中在 _run_main_loop 内，此处只做「结果翻译」。

        Args:
            max_time: 任务最大执行秒数（None=无限），达标自动结束返回 done
            max_turns: 任务最大战斗轮次（None=无限），达标自动结束返回 done
            start_turns: 实例启动时的轮次基线（用于计算相对轮次增量）

        Returns:
            'done'    — 正常结束（指标达成 / 全部关卡完成）
            'failed'  — 执行中抛出异常
            'stopped' — 被 stop() 主动中止（用户手动停止）

        Note:
            v1.5.0 起运行模式与战斗模式已固定（交替模式 + 逐次战斗），
            本方法**不接受模式参数**。
        """
        # 注入任务结束指标（时间/轮次 OR 逻辑 + 全部通关兜底）
        self._task_max_time = max_time
        self._task_max_turns = max_turns
        self._task_start_time = time.time()
        self._task_start_turns = start_turns
        self._task_completed = False

        _metric_txt = ''
        if max_time is not None:
            _metric_txt += f'时间≤{max_time}s '
        if max_turns is not None:
            _metric_txt += f'轮次≤{max_turns} '
        self._log(
            'info',
            '[自动推图] 任务启动'
            + (f'（指标: {_metric_txt.strip()}）' if _metric_txt else '')
        )

        try:
            self._run_main_loop()
        except Exception as e:
            self._log('error', f'[自动推图] 异常终止: {e}')
            import traceback
            traceback.print_exc()
            return 'failed'
        finally:
            self.set_status(running=False)

        if self._stop and not self._task_completed:
            self._log('info', '[自动推图] 已停止')
            return 'stopped'

        self._log('info', '[自动推图] 任务完成')
        return 'done'

    # ── 任务结束指标辅助 ───────────────────────────────────

    def _task_completion_reached(self) -> bool:
        """检查任务结束指标（时间/轮次，OR 逻辑）；达标返回 True"""
        if self._task_max_time is not None:
            if (time.time() - self._task_start_time) >= self._task_max_time:
                return True
        if self._task_max_turns is not None:
            if (self.stats.get('total_battles', 0)
                    - self._task_start_turns) >= self._task_max_turns:
                return True
        return False

    def _task_completion_reason(self) -> str:
        """任务指标达成的具体原因（用于日志）"""
        reasons = []
        if self._task_max_time is not None \
                and (time.time() - self._task_start_time) >= self._task_max_time:
            reasons.append(f'时间 {self._task_max_time}s')
        if self._task_max_turns is not None \
                and (self.stats.get('total_battles', 0)
                     - self._task_start_turns) >= self._task_max_turns:
            reasons.append(f'轮次 {self._task_max_turns} 轮')
        return '、'.join(reasons) or '无'

    # ═══════════════════════════════════════════════════════════════
    #  兼容 API — 供 BaseTask 通过 ctx['bot'] 访问
    # ═══════════════════════════════════════════════════════════════

    def detect_phase(self) -> str:
        """检测当前游戏阶段。"""
        return self._phase_detector.detect_phase()

    def _win_dims(self) -> Tuple[int, int, int, int]:
        """获取窗口位置和尺寸，返回 (l, t, w, h)。"""
        return self._screen_capture.win_dims()

    def _snap(self) -> Tuple[Any, int, int, int, int]:
        """截图，返回 (img, l, t, w, h)。"""
        return self._screen_capture.snap()

    def _ocr_screen(
        self, img: Any = None, no_cache: bool = False
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """全屏 OCR 识别（no_cache=True 强制重识别）。"""
        return self._screen_capture.ocr_screen(img=img, no_cache=no_cache)

    def _ocr_img(
        self, img: Any, roi: Any = None
    ) -> List[Tuple[int, int, str, float, int, int]]:
        """对 PIL Image 执行 OCR。"""
        return self._screen_capture._ocr_img(img, roi=roi)

    def click_at(self, cx: int, cy: int, label: str = '') -> None:
        """图像坐标点击。"""
        self._input_controller.click_at(cx, cy, label=label)

    def click_ratio(
        self, rx: float, ry: float, label: str = ''
    ) -> bool:
        """比例坐标点击。"""
        return self._input_controller.click_ratio(rx, ry, label=label)

    def click_ratio_dual(
        self,
        landscape: Tuple[float, float],
        portrait: Tuple[float, float],
        label: str = '',
    ) -> bool:
        """横竖屏自适应比例坐标点击。"""
        return self._input_controller.click_ratio_dual(
            landscape, portrait, label=label
        )

    def _click_abs(self, cx: int, cy: int) -> None:
        """屏幕绝对坐标点击。"""
        self._input_controller.click_abs(cx, cy)

    def _send_esc(self) -> None:
        """发送 ESC 按键。"""
        self._input_controller.send_esc()

    def find_text(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """模糊查找：keyword 包含在 OCR 文字中即匹配。"""
        return self._text_recognizer.find_text(
            items, keyword, min_conf=min_conf
        )

    def find_text_exact(
        self,
        items: List[Tuple],
        keyword: str,
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配。"""
        return self._text_recognizer.find_text_exact(
            items, keyword, min_conf=min_conf
        )

    def find_text_exclude(
        self,
        items: List[Tuple],
        keyword: str,
        exclude_keywords: List[str],
        min_conf: float = 0.5,
    ) -> Optional[Tuple[int, int]]:
        """精确匹配 keyword，排除包含 exclude_keywords 中任意项的结果。"""
        return self._text_recognizer.find_text_exclude(
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
        return self._text_recognizer._find_text_impl(
            items, keyword, exact=exact, min_conf=min_conf
        )

    def _screen_offset(self) -> Tuple[int, int]:
        """返回图像坐标原点的屏幕偏移 (l, t)（图像→屏幕平移量）。

        v1.8.56：优先用客户区偏移（图像原点=客户区左上角）——窗口含
        边框装饰时 win_dims 左上角不等于客户区原点；Unity 无边框窗口
        两者等价（既有真机行为不变）。
        """
        coord = getattr(self._screen_capture, '_coord_system', None)
        if coord is not None:
            try:
                return coord.client_offset
            except Exception as _e:
                self.log(f"[调试] _screen_offset() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
                pass
        l, t, _, _ = self._screen_capture.win_dims()
        return l, t

    def template_match(
        self,
        tpl_path: str,
        threshold: float = 0.8,
        scale_range: Tuple[float, float, float] = (0.5, 2.0, 0.1),
        silent: bool = False,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int]]:
        """多尺度模板匹配。"""
        return self._input_controller.template_match(
            tpl_path, threshold=threshold, scale_range=scale_range,
            silent=silent, region=region,
        )

    # ═══════════════════════════════════════════════════════════════
    #  分辨率切换
    # ═══════════════════════════════════════════════════════════════

    def switch_resolution(self, target_mode: str) -> bool:
        """切换游戏分辨率到目标模式。

        流程：
        1. 0 层（世界/家园）→ 点 ≡（选项菜单）
        2. 选项菜单 → 点 ⚙（齿轮/设置）
        3. 设置 → 点"图像"标签
        4. 图像 → 点分辨率下拉 → 选择目标模式
        5. 确认弹窗 → 点 √（绿色确认按钮）

        Args:
            target_mode: 目标分辨率模式文字，如 '720p'、'1080p'

        Returns:
            bool: 是否成功切换
        """
        self._log('info', f'[切换分辨率] 开始 | 目标={target_mode}')
        try:
            # Step 1: 点 ≡
            self._log('info', '[切换分辨率] Step 1: 点击 ≡ 选项菜单')
            self._input_controller.click_ratio_dual(
                _BTN_POS['options'], _BTN_POS_PORTRAIT['options'],
                label='≡选项',
            )
            if not self._wait_for_ocr_text('设置', timeout=10):
                self._log('info', '[切换分辨率] 失败：选项菜单未出现')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_options_menu'
                )
                return False

            # Step 2: 点 ⚙
            self._log('info', '[切换分辨率] Step 2: 点击 ⚙ 齿轮设置')
            self._input_controller.click_ratio_dual(
                _BTN_POS['gear'], _BTN_POS_PORTRAIT['gear'],
                label='⚙齿轮',
            )
            if not self._wait_for_ocr_text('图像', timeout=10):
                self._log('info', '[切换分辨率] 失败：设置界面未出现')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_settings'
                )
                return False

            # Step 3: 点"图像"标签
            self._log('info', '[切换分辨率] Step 3: 点击"图像"标签')
            if not self._click_ocr_text(
                '图像', label='图像标签'
            ):
                self._log('info', '[切换分辨率] 失败：无法点击"图像"标签')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_image_tab'
                )
                return False
            if not self._wait_for_ocr_text('分辨率', timeout=10):
                self._log('info', '[切换分辨率] 失败：图像标签页面未出现')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_image_page'
                )
                return False

            # Step 4: 点分辨率下拉
            self._log('info', '[切换分辨率] Step 4: 点击分辨率下拉')
            if not self._click_ocr_text_below(
                '分辨率', offset_y=30, label='分辨率下拉'
            ):
                self._log('info', '[切换分辨率] 失败：无法点击分辨率下拉')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_dropdown'
                )
                return False
            if not self._wait_for_ocr_text(target_mode, timeout=10):
                self._log(
                    'info',
                    f'[切换分辨率] 失败：分辨率选项未出现'
                    f' ({target_mode})'
                )
                self._screen_capture.debug_screenshot(
                    f'switch_res_no_{target_mode}'
                )
                return False

            # Step 5: 选择目标分辨率
            self._log(
                'info',
                f'[切换分辨率] Step 5: 选择目标分辨率 ({target_mode})'
            )
            if not self._click_ocr_text(
                target_mode, label=f'分辨率{target_mode}'
            ):
                self._log(
                    'info',
                    f'[切换分辨率] 失败：无法选择 {target_mode}'
                )
                self._screen_capture.debug_screenshot(
                    f'switch_res_cannot_select_{target_mode}'
                )
                return False

            # 等待确认弹窗
            self._log('info', '[切换分辨率] 等待确认弹窗...')
            if not self._wait_for_green_button(timeout=10):
                self._log('info', '[切换分辨率] 失败：确认弹窗未出现')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_confirm'
                )
                return False

            # Step 6: 点 √ 确认
            self._log('info', '[切换分辨率] Step 6: 点击 √ 确认')
            if not self._click_green_button(label='√确认'):
                self._log('info', '[切换分辨率] 失败：无法点击 √ 确认')
                self._screen_capture.debug_screenshot(
                    'switch_res_no_confirm_btn'
                )
                return False

            self._log('info', '[切换分辨率] 等待切换完成...')
            self._stop_event.wait(10)

            self._log('info', f'[切换分辨率] 完成 | 目标={target_mode}')
            return True

        except Exception as e:
            self._log('info', f'[切换分辨率] 异常 | {e}')
            self._screen_capture.debug_screenshot('switch_res_exception')
            import traceback
            self._log('error', traceback.format_exc())
            return False

    # ── 分辨率切换辅助方法 ────────────────────────────────────

    def _wait_for_ocr_text(
        self, keyword: str, timeout: int = 15
    ) -> bool:
        """等待指定文字出现（OCR 轮询）。

        Args:
            keyword: 要等待的关键词
            timeout: 超时时间（秒）

        Returns:
            bool: 找到返回 True，超时返回 False
        """
        deadline: float = time.time() + timeout
        while time.time() < deadline:
            if self._stop or not self._pause.is_set():
                return False
            items: List[Tuple] = self._screen_capture.ocr_screen()
            if any(
                keyword in t
                for _, _, t, c, *_ in items if c >= 0.5
            ):
                return True
            self._stop_event.wait(0.8)
        return False

    def _click_ocr_text(
        self,
        keyword: str,
        label: str = '',
        exact: bool = False,
        min_conf: float = 0.5,
    ) -> bool:
        """OCR 找文字并点击。

        Args:
            keyword: 要查找的文字
            label: 日志标签
            exact: 是否精确匹配
            min_conf: 最低置信度

        Returns:
            bool: 找到并点击成功返回 True
        """
        img, l, t, w, h = self._screen_capture.snap()
        items: List[Tuple] = self._screen_capture._ocr_img(img)
        found: Optional[Tuple[int, int, float]] = None
        for cy, cx, text, score, x_min, x_max in items:
            if score < min_conf:
                continue
            match: bool = (
                (exact and text == keyword)
                or (not exact and keyword in text)
            )
            if match and (not found or score > found[2]):
                found = (cx, cy, score)
        if not found:
            self._log(
                'info',
                f'[切换分辨率] OCR未找到 | {label} keyword={keyword}'
            )
            return False
        cx_abs: int = l + found[0]
        cy_abs: int = t + found[1]
        self._log(
            'info',
            f'[切换分辨率] OCR点击 | {label} '
            f'({cx_abs},{cy_abs}) score={found[2]:.2f}'
        )
        self._input_controller.click_abs(cx_abs, cy_abs)
        self._stop_event.wait(0.5)
        return True

    def _click_ocr_text_below(
        self,
        keyword: str,
        offset_y: int = 30,
        label: str = '',
        min_conf: float = 0.5,
    ) -> bool:
        """OCR 找文字，点击其下方 offset_y 像素处。

        Args:
            keyword: 要查找的文字
            offset_y: 向下偏移像素数
            label: 日志标签
            min_conf: 最低置信度

        Returns:
            bool: 找到并点击成功返回 True
        """
        img, l, t, w, h = self._screen_capture.snap()
        items = self._screen_capture._ocr_img(img)
        found = None
        for cy, cx, text, score, x_min, x_max in items:
            if score < min_conf:
                continue
            if keyword in text and (not found or score > found[2]):
                click_y: int = cy + offset_y
                found = (cx, click_y, score)
        if not found:
            self._log(
                'info',
                f'[切换分辨率] OCR未找到 | {label} keyword={keyword}'
            )
            return False
        cx_abs = l + found[0]
        cy_abs = t + found[1]
        self._log(
            'info',
            f'[切换分辨率] OCR点击下方 | {label} '
            f'({cx_abs},{cy_abs}) score={found[2]:.2f}'
        )
        self._input_controller.click_abs(cx_abs, cy_abs)
        self._stop_event.wait(0.5)
        return True

    def _find_green_button_in_img(
        self,
        img: Any,
        hsv_lower: Tuple[int, int, int] = (35, 80, 80),
        hsv_upper: Tuple[int, int, int] = (85, 255, 255),
    ) -> Optional[Tuple[int, int]]:
        """在图像中找绿色按钮区域（HSV 颜色检测）。

        Args:
            img: PIL Image
            hsv_lower: HSV 下限
            hsv_upper: HSV 上限

        Returns:
            (cx, cy) 或 None
        """
        import cv2
        import numpy as np
        arr: np.ndarray = np.array(img.convert('RGB'))
        hsv: np.ndarray = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV)
        mask: np.ndarray = cv2.inRange(hsv, hsv_lower, hsv_upper)
        contours, _ = cv2.findContours(
            mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours:
            return None
        largest = max(contours, key=cv2.contourArea)
        M: dict = cv2.moments(largest)
        if M['m00'] == 0:
            return None
        cx: int = int(M['m10'] / M['m00'])
        cy: int = int(M['m01'] / M['m00'])
        return (cx, cy)

    def _wait_for_green_button(self, timeout: int = 10) -> bool:
        """等待绿色按钮出现（颜色检测）。

        Args:
            timeout: 超时时间（秒）

        Returns:
            bool: 找到返回 True
        """
        deadline: float = time.time() + timeout
        while time.time() < deadline:
            if self._stop or not self._pause.is_set():
                return False
            img, l, t, w, h = self._screen_capture.snap()
            pos: Optional[Tuple[int, int]] = (
                self._find_green_button_in_img(img)
            )
            if pos:
                return True
            self._stop_event.wait(0.5)
        return False

    def _click_green_button(self, label: str = '') -> bool:
        """找绿色按钮并点击。

        Args:
            label: 日志标签

        Returns:
            bool: 找到并点击成功返回 True
        """
        img, l, t, w, h = self._screen_capture.snap()
        pos: Optional[Tuple[int, int]] = (
            self._find_green_button_in_img(img)
        )
        if not pos:
            self._log('info', f'[切换分辨率] 未找到绿色按钮 | {label}')
            return False
        cx_screen: int = l + pos[0]
        cy_screen: int = t + pos[1]
        self._log(
            'info',
            f'[切换分辨率] 点击绿色按钮 | {label} '
            f'({cx_screen},{cy_screen})'
        )
        self._input_controller.click_abs(cx_screen, cy_screen)
        self._stop_event.wait(0.5)
        return True

    # ── 推进检测辅助 ──────────────────────────────────────────

    def _record_pre_battle_stage(
        self, current_mode: str, mode_prev_stage: Dict[str, int]
    ) -> None:
        """记录进入战斗前的赛季关卡。

        Args:
            current_mode: 当前模式 — 'huanling' 或 'normal'
            mode_prev_stage: 用于战后计算推进数的字典
        """
        stats: Dict[str, Any] = self.stats
        hl, nm = self._detect_season_stages()
        if hl > 0:
            stats['huanling_stage'] = hl
        if nm > 0:
            stats['normal_stage'] = nm
        if hl > 0 or nm > 0:
            display_hl: int = hl if hl > 0 else stats.get('huanling_stage', 0)
            display_nm: int = nm if nm > 0 else stats.get('normal_stage', 0)
            stats['current_stage'] = max(hl, nm, display_hl, display_nm)
            self._log(
                'info',
                f'[数据] 当前关卡: {stats["current_stage"]}'
                f'（幻灵:{display_hl}, 挂机:{display_nm}）'
            )

        if current_mode == 'huanling':
            mode_prev_stage['huanling'] = stats.get('huanling_stage', 0)
        else:
            mode_prev_stage['normal'] = stats.get('normal_stage', 0)
