"""
任务：女神试炼（强攻秘藏）

v1.8.17 实装（用户 2026-09-04 需求 + 5 张真机截图 OCR 判据实测）。

────────────────────────────────────────────
【入口路径】
  0 层（世界/家园）→ 点「玩法目录」→ 玩法目录
  → OCR 找「女神试炼」卡片（0.98 @ 卡片标题）→ 点卡片 → 选关页

【页面判据】（MaaOCR 实测置信度，素材见 用户 2026-09-04 截图）
  选关页   ：「今日可挑战层数上限：60」0.99（主判据）；
             兜底「攻秘藏」标题 0.99 + 「详情/魔饰总览」
  备战页   ：「自动挑战」0.99 +「战斗」0.99 +「秘藏」（顶栏"强攻秘藏-第56层"）
             —— 必须含「秘藏」：推图备战页同样有「自动挑战」+「战斗」
             （v1.8.8 同款撞词教训），「秘藏」为女神副本独有
  扫荡弹窗 ：「扫荡次数」0.98 +「奖励预览」0.99 双词

【状态机核心 = 大按钮文字】（用户确认）
  选关页大按钮：「挑战」→ 有可挑战层，走挑战流程；
               「扫荡」→ 无可挑战层（含全部通关），直接扫荡
  —— 全部通关时大按钮必为「扫荡」，"跳过挑战直接扫荡"天然满足。

【按钮点击规则】（用户 2026-09-04 明确 + 真机 20:55 实测修正）
  「挑战」/「扫荡」：均**全等匹配**（norm_text == 词）——
    ① 「挑战」：判据文字「今日可挑战层数上限：60」自身含"挑战"子串，
       且 OCR 输出顺序先于大按钮 → 子串匹配点到上限文字位置无效死循环
       （v1.8.17 首跑日志 20:55 实测）；
    ② 「扫荡」：界面有 6 处「扫荡掉落」奖品标签；
    ③ 且 ocr_engine.find_text(exact=True) 实际是
       "全等 or startswith or 包含"（"扫荡掉落".startswith("扫荡") 命中），
       exact=True 不可用，故自实现 _find_exact_word。
    说明文字「本次挑战不消耗女神徽记」全等不命中，但本来就在按钮内。

【流程】
  挑战：点「挑战」→ 备战页点「自动挑战」→ 自动连打到当前可挑战最后一层
        （用户确认：等待自动结束即可，不可能打不过 → 无失败分支）
        → 回选关页 → 大按钮变「扫荡」
  扫荡：点「扫荡」→ 弹窗点 ⏭ 拉满（btn_sweep_max.png RGBA 模板，maskedNCC
        自截 1.000 @ 滑条行右端；OCR「扫荡次数」行定位参照）→ 点「扫荡」确认
        → 回选关页 → done

【架构原则】
  女神试炼内部状态全部自 OCR 判定，不动 detector（v1.7.9 起的隔离原则，
 迷宫多次验证 detector 对玩法内部态有盲区）。
────────────────────────────────────────────
"""

import os
import time
import re
import unicodedata
from typing import List, Optional, Tuple

from tasks.base import BaseTask
from tasks.labyrinth import (  # noqa: E402  v1.8.69 补参
    _BACK_BUTTON_TPLS, _BACK_THRESHOLD)
from task_registry import TaskRegistry, TaskMeta

task_id = "goddess_trial"
label = "女神试炼"
description = "进入女神试炼自动挑战至当前上限层，随后扫荡拉满奖励后结束"

# ── 界面判据（MaaOCR 实测 2026-09-04）──────────────────────
# ── 界面判据（v1.8.25 上收全局注册表 tasks/screen_registry.py，单一真相源）
from tasks.screen_registry import (  # noqa: E402
    GODDESS_AUTOCHECK_MARK as _AUTOCHECK_MARK,
    GODDESS_SECRET_MARK as _SECRET_MARK,
    GODDESS_SELECT_MARK as _SELECT_MARK,
    GODDESS_SELECT_TITLE_MARKS as _SELECT_FALLBACK,
    GODDESS_SWEEP_DIALOG_MARKS as _SWEEP_DIALOG_MARKS,
    GLOBAL_SCREEN_REGISTRY,
    zh_screen,
)
# v1.8.23：点「扫荡」大按钮后等待次数弹窗出现的秒数（转场+动画余量）；
# 连续 _SWEEP_MISS_MAX 次弹不出 = 次数用尽（扫荡完成）→ done
_SWEEP_DIALOG_WAIT: float = 6.0
_SWEEP_MISS_MAX: int = 2

_SWEEP_WORD: str = '扫荡'        # 大按钮/确认按钮（全等匹配）
_CHALLENGE_WORD: str = '挑战'    # 大按钮（子串匹配，说明文字在按钮内）
_MIN_CONF: float = 0.6

# ⏭ 拉满按钮模板（RGBA 透明，maskedNCC 自截 1.000；扫荡弹窗滑条行右端）
_PROJECT_ROOT: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE_DIR: str = os.path.join(_PROJECT_ROOT, 'templates')
_SWEEP_MAX_TPL: str = os.path.join(_TEMPLATE_DIR, 'btn_sweep_max.png')
_SWEEP_TPL_THRESHOLD: float = 0.72

# ── 等待参数 ───────────────────────────────────────────────
_NAV_MAX_ITER: int = 30          # 导航主循环最大迭代
_ENTER_TIMEOUT: float = 10.0     # 点卡片后等待进入选关页
_TEXT_TIMEOUT: float = 8.0       # OCR 找按钮重试秒数
_STATE_POLL: float = 0.8         # 状态轮询间隔
_TRANSITION_WAIT: float = 2.0    # 点击后转场等待
# 自动挑战连打多层（用户确认：一直打到当前可挑战最后一层）→ 等待上限放宽
_AUTO_BATTLE_WAIT: float = 900.0
_AUTO_BATTLE_POLL: float = 1.5
_SWEEP_RESULT_WAIT: float = 30.0  # 扫荡确认后等回选关页（含结果弹窗）

# 玩法目录滚动（与 labyrinth 同一页面，参数照搬 v1.7.4 提速版）
_SCROLL_REGION: Tuple[float, float, float, float] = (0.435, 0.124, 0.79, 0.605)
_SCROLL_CLICKS: int = -30
# v1.8.88（用户定案）：移除 `_SCROLL_MAX`（最多滚动次数上限）。终止改由
# 画面状态决定：找到卡片即进入；滚动后画面文本不再变化（已滚到底）仍未
# 找到 → 返回 False 交由上层兜底，保证正常流程不会无限滚动。
_SCROLL_SETTLE: float = 0.3
_CARD_TEXT: str = '女神试炼'
_CARD_MIN_CONF: float = 0.6

# 导航 phase 中文标签（日志用，同 labyrinth）
_PHASE_LABELS: dict = {
    'world': '世界', 'home': '家园', 'guild': '公会',
    'playbook': '玩法目录', 'idle_stage': '挂机关卡态', 'activity': '活动页',
    'formation': '备战态', 'battle': '战斗态', 'result': '结算态',
    'unknown': '未知态',
}

_STATE_LABELS: dict = {
    'select_page':   '选关页',
    'battle_prep':   '备战页',
    'sweep_dialog':  '扫荡弹窗',
    'victory_result': '战斗胜利结算',
    'auto_battle':   '自动挑战中',
    'unknown':       '未知/转场',
}

_UNKNOWN_ESC_AFTER: int = 3
_UNKNOWN_GIVE_UP: int = 10


class GoddessTrialTask(BaseTask):
    task_id = task_id

    # v2.1.9（用户 22:35 规范）循环语义声明——选关页起即为循环内
    loop_screens = frozenset({
        'goddess.select_page', 'goddess.battle_prep',
        'goddess.auto_battle', 'goddess.victory_result',
        'goddess.sweep_dialog',
    })
    label = label
    description = description

    # 本任务无可调参数（扫荡拉满为固定行为）
    options_schema = {}

    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self._time_up: bool = False
        self._time_up_logged: bool = False
        self._swept: bool = False   # 扫荡已完成 → 回选关页即 done

    # ── 时间上限（v1.8.4 labyrinth 同款语义）────────────────

    def check_stop(self) -> bool:
        if super().check_stop():
            return True
        return self._time_limit_reached()

    def _time_limit_reached(self) -> bool:
        if not self.max_time or self.max_time <= 0:
            return False
        if self.elapsed < self.max_time:
            return False
        self._time_up = True
        if not self._time_up_logged:
            self._time_up_logged = True
            self.log(f"任务指标达成: 时间 {self.max_time}s，结束任务")
        return True

    def _stop_result(self) -> str:
        return "done" if self._time_up else "stopped"

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    # ── OCR 工具 ───────────────────────────────────────────

    @staticmethod
    def _norm(text: str) -> str:
        return unicodedata.normalize('NFKC', text).strip()

    def _texts(self, items: List[Tuple], min_conf: float = 0.5) -> List[str]:
        """文本列表（v2.1.27 P1-5：过滤规则委托基类，本类只叠加归一化）。

        与 base 的差异仅在**归一化**（`self._norm`），故改为
        「基类过滤 → 本类归一化」，避免过滤规则出现第二份实现。
        """
        return [self._norm(t) for t in super()._texts(items, min_conf)]

    def _find_exact_word(
        self, items: List[Tuple], word: str, min_conf: float = _MIN_CONF
    ) -> Optional[Tuple[int, int]]:
        """全等匹配找按钮词（find_text exact=True 会 startswith/包含误命中
        「扫荡掉落」，不可用——用户 2026-09-04 明确要求全等）。

        Returns:
            (cx, cy) 图像坐标或 None
        """
        for it in items:
            if len(it) < 4 or it[3] < min_conf:
                continue
            if self._norm(it[2]) == word:
                return int(it[1]), int(it[0])   # items 结构 (cy, cx, ...)
        return None

    def _click_exact_word(
        self, word: str, label: str = '', min_conf: float = _MIN_CONF
    ) -> bool:
        """OCR 全等匹配按钮词并点击，带重试。"""
        deadline: float = time.time() + _TEXT_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            pos = self._find_exact_word(items, word, min_conf)
            if pos:
                self.log(f"[点击] 「{word}」(全等) | 图像=({pos[0]},{pos[1]})")
                self.bot.click_at(pos[0], pos[1], label or word)
                self._sleep(1.2)
                return True
            self._sleep(0.6)
        self.log(f"[警告] 未找到按钮词 | {word}")
        return False

    def _click_text_kw(self, keyword: str, min_conf: float = 0.6) -> bool:
        """OCR 子串匹配找文字并点击（用户规则：挑战类直接子串即可）。"""
        deadline: float = time.time() + _TEXT_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            pos = self.bot.find_text(items, keyword, min_conf=min_conf)
            if pos:
                self.log(
                    f"[点击] 「{keyword}」(子串) | 图像=({pos[0]},{pos[1]})"
                )
                self.bot.click_at(pos[0], pos[1], keyword)
                self._sleep(1.2)
                return True
            self._sleep(0.6)
        self.log(f"[警告] 未找到文字 | {keyword}")
        return False

    # ── 状态判定 ───────────────────────────────────────────

    def _detect_state(self, items: List[Tuple]) -> str:
        """女神域状态判定（v1.8.25 起委托全局界面注册表）。

        判据全部上收至 tasks/screen_registry.py（GLOBAL_SCREEN_REGISTRY，
        单一真相源，依据来源见 docs/界面判据清单.md）。本方法只做域映射：
          goddess.*          → 去前缀的本域状态名（原动作分流不变）
          他域 id / unknown  → 'unknown'（调用方走脱离链；
                              self._last_screen 保留全局指名，日志可见）
        评估顺序与历史行为逐条对齐（sweep_dialog → victory_result →
        battle_prep → select_page → auto_battle），勿在注册表中随意调换。
        """
        screen: str = GLOBAL_SCREEN_REGISTRY.detect(items)
        self._last_screen: str = screen
        if screen.startswith('goddess.'):
            return screen.split('.', 1)[1]
        return 'unknown'

    # ── 启动探测（v1.8.7 契约，labyrinth 样板）──────────────

    def startup_probe(self) -> str:
        """OCR 一帧判定是否已在任务执行域（选关页/备战页/扫荡弹窗）。"""
        items = self.bot._ocr_screen()
        state: str = self._detect_state(items)
        if state != 'unknown':
            lbl: str = _STATE_LABELS.get(state, state)
            self.log(f"[信息] 启动即在女神试炼界面（{lbl}），直接续跑")
            return 'ready'
        # 无特定残留弹窗；闪烁弹窗顺手清一帧（零额外 OCR 之外的一次尝试）
        self.dismiss_blink_popup(items)
        return 'clear'

    # ── 任务主体 ───────────────────────────────────────────

    def run(self) -> str:
        """女神试炼任务主体。

        阶段 0：startup_probe 三态（'ready' 直通主循环）
        阶段 1：导航到选关页（0层→玩法目录→点「女神试炼」卡片）
        阶段 2：玩法主循环（挑战/自动挑战/扫荡 → done）
        """
        self.log("启动（导航 + 女神试炼玩法循环）")
        self._swept = False
        self._sweep_miss = 0   # v1.8.23：点扫荡未弹窗计数（≥2=次数用尽）

        probe: str = self.startup_probe()
        if probe == 'ready':
            return self._play_loop()
        if probe == 'failed':
            if self.check_stop():
                return self._stop_result()
            self.log("[错误] 启动探测失败")
            return "failed"

        ok: bool = self._navigate_to_trial()
        if self.check_stop():
            return self._stop_result()
        if not ok:
            self.log_nav_failed("女神试炼选关页")
            return "failed"
        self.log("导航完成，已在女神试炼选关页")

        return self._play_loop()

    def summary(self) -> str:
        return "选关挑战并扫荡拉满次数"

    # ── 导航 ───────────────────────────────────────────────

    def _navigate_to_trial(self) -> bool:
        """导航到女神试炼选关页（状态机同 labyrinth 模式）。"""
        # v1.8.32：启动导航整体接入**全局导航系统**（判定统一走全局界面
        # 注册表 + 最短路径）；已在女神试炼任意执行域界面即算到达
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(
            self.bot, logger=self.log, stop_check=self.check_stop,
        )
        ok: bool = nav.navigate_to('goddess_trial',
                                   arrived_cb=self._nav_arrived)
        if not ok:
            self._nav_last_screen = getattr(nav, 'last_screen', '')   # v2.1.27：留现场（诊断用，取值失败不得中断）
        return ok

    def _nav_arrived(self, screen_id: str, items: List[Tuple]) -> bool:
        """全局导航「已到达」回调：女神选关页/备战页/自动挑战中/结算页/
        扫荡弹窗——任意执行域界面均算到达（交由玩法主循环分流）。"""
        return screen_id.startswith('goddess.')

    def _is_on_playbook(self) -> bool:
        """是否在玩法目录（判据同 labyrinth v1.7.9：常规玩法+活动 同现）。"""
        items = self.bot._ocr_screen()
        texts: List[str] = self._texts(items)
        return (
            any('常规玩法' in t for t in texts)
            and any('活动' in t for t in texts)
        )

    def _find_and_enter_card(self) -> bool:
        """玩法目录内滚动找「女神试炼」卡片并点击（labyrinth 同款自愈）。"""
        prev_texts = None
        while True:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            pos = self.bot.find_text(
                items, _CARD_TEXT, min_conf=_CARD_MIN_CONF
            )
            if pos:
                self.log(f"找到「{_CARD_TEXT}」卡片，点击 | 图像=({pos[0]},{pos[1]})")
                self.bot.click_at(pos[0], pos[1], _CARD_TEXT)
                if self._wait_enter_select():
                    return True
                self.log("[警告] 点击后未进入，继续滚动")
            # v1.8.88：滚动后画面不再变化 = 已到底 → 放弃（非次数上限）
            cur_texts = tuple(sorted(self._texts(items)))
            if prev_texts is not None and cur_texts == prev_texts:
                self.log("[警告] 玩法目录已滚到底，未找到「"
                         f"{_CARD_TEXT}」卡片")
                return False
            prev_texts = cur_texts
            # 滚轮向下（内部含激活窗口 + 鼠标移到可滚动区中心）
            x0, y0, x1, y1 = _SCROLL_REGION
            self.bot.scroll_wheel(
                _SCROLL_CLICKS,
                rx=(x0 + x1) / 2, ry=(y0 + y1) / 2,
                label='玩法目录_向下',
            )
            self._sleep(_SCROLL_SETTLE)
        return False

    def _wait_enter_select(self) -> bool:
        """点卡片后等待进入选关页（自 OCR 判据）。"""
        deadline: float = time.time() + _ENTER_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            if self._detect_state(items) in (
                'select_page', 'battle_prep', 'sweep_dialog',
                'victory_result', 'auto_battle',
            ):
                return True
            self._sleep(0.8)
        return False

    # ── 玩法主循环 ─────────────────────────────────────────

    def _play_loop(self) -> str:
        """女神试炼主循环：挑战/自动挑战/扫荡 → done。

        状态分流（大按钮文字驱动，用户定案）：
          select_page  大按钮「扫荡」(全等) → 点扫荡 → 弹窗流程
                       大按钮「挑战」(子串) → 点挑战 → 备战页
                       _swept=True 时回到选关页 → done
          battle_prep  点「自动挑战」→ 等自动连打结束回选关页
          sweep_dialog ⏭ 拉满 → 点「扫荡」确认 → 等回选关页 → _swept=True
          unknown      闪烁弹窗 → 转场等待 → ESC → give_up（labyrinth 降级链）
        """
        unknown_streak: int = 0
        while True:
            if self.check_stop():
                return self._stop_result()
            # v2.1.27（P0-1 配套）：显式迭代计数（不再以日志条数代替）
            self.count_iter()

            items = self.bot._ocr_screen()
            # v1.9.2（用户定案全局规则）：点击空白处弹层先点掉
            if self.dismiss_blink_popup(items=items):
                continue
            state: str = self._detect_state(items)
            if state != 'unknown':
                unknown_streak = 0
            if state == 'unknown' and getattr(
                self, '_last_screen', 'unknown'
            ) != 'unknown':
                # v1.8.25：全局注册表精准指名他域画面（日志可见，便于补判据）
                # v2.1.27（P0-1）：改 log_state——状态变化才打印
                self.log_state(
                    'unknown:other',
                    f"玩法循环 | 未识别画面(他域："
                    f"{zh_screen(self._last_screen)}，脱离)"
                )
            else:
                # v2.1.27（P0-1）：状态变化才打印（原每帧一条）
                self.log_state(
                    state,
                    f"玩法循环 | {state}({_STATE_LABELS.get(state, state)})"
                )

            if state == 'select_page':
                if self._swept:
                    self.log("[成功] 扫荡完成，任务结束")
                    return "done"
                # 大按钮「扫荡」(全等) 优先——无可挑战层（含全部通关）
                pos = self._find_exact_word(items, _SWEEP_WORD)
                if pos:
                    # v1.8.23（真机 9/6 用户反馈"无法识别扫荡完成画面"）：
                    # 扫荡完成后的选关页与扫荡前**视觉完全一致**（大按钮仍是
                    # 「扫荡」+「已达今日可挑战层数上限」横幅），唯一区别是
                    # 次数用尽后点扫荡不再弹窗 → 无限重点扫荡按钮到不了 done。
                    # 修法：点「扫荡」后限时等弹窗；连续 2 次弹不出 = 次数
                    # 已用尽（扫荡完成）→ done
                    # v1.8.28（用户提供 扫荡前/扫荡后.png）：扫荡徽记
                    # **6/1（白字 OCR 0.99 可读）→ 0/1（灰字 OCR 读不出）**
                    # 是扫荡前后唯一区别。判别规则反转：按钮附近能读出
                    # X/1 = 有次数；读不出 = 已用尽（0 灰字不可读）
                    remaining = self._read_sweep_remaining(items, pos)
                    if remaining is not None:
                        self.log(f"[数据] 扫荡剩余次数：{remaining}/1")
                        if remaining <= 0:
                            self.log(
                                "[成功] 当日扫荡已完成（次数 0），任务结束"
                            )
                            return "done"
                    if self._sweep_miss >= _SWEEP_MISS_MAX:
                        self.log(
                            "[成功] 连续多次点「扫荡」均未弹出次数弹窗，"
                            "判定扫荡已完成（次数用尽），任务结束"
                        )
                        return "done"
                    self._sweep_miss += 1
                    self.log(
                        "[信息] 大按钮=扫荡（无可挑战层），进入扫荡流程"
                        f"（第 {self._sweep_miss} 次尝试）"
                    )
                    self.bot.click_at(pos[0], pos[1], '扫荡_大按钮')
                    # 限时等扫荡弹窗（转场+动画余量）；弹出即清零计数；
                    # v1.8.28：期间捕捉「今日已无扫荡次数」toast → 立即结束
                    deadline: float = time.time() + _SWEEP_DIALOG_WAIT
                    while time.time() < deadline:
                        if self.check_stop():
                            return self._stop_result()
                        self._sleep(_STATE_POLL)
                        frame: List[Tuple] = self.bot._ocr_screen()
                        if self._detect_state(frame) == 'sweep_dialog':
                            self._sweep_miss = 0
                            break
                        if self._sweep_exhausted_toast(frame):
                            self.log(
                                "[成功] 「今日已无扫荡次数」——当日已完成"
                                "扫荡，任务结束"
                            )
                            return "done"
                    continue
                # 大按钮「挑战」(全等——真机 v1.8.17 实测 20:55：判据文字
                # 「今日可挑战层数上限：60」自身含"挑战"子串，且 OCR 输出
                # 顺序先于大按钮 → 子串匹配点到上限文字位置无效死循环)
                pos = self._find_exact_word(items, _CHALLENGE_WORD)
                if pos:
                    self.log("[信息] 大按钮=挑战，进入挑战流程")
                    self.bot.click_at(pos[0], pos[1], '挑战_大按钮')
                    self._sleep(_TRANSITION_WAIT)
                    continue
                self.log("[警告] 选关页未找到挑战/扫荡按钮，重试")
                self._sleep(_STATE_POLL)
                continue

            if state == 'victory_result':
                # 最后一关通关结算页（真机 2026-09-04：需 ESC/返回键退出，
                # 不会自动消失；用户定案用 ESC）
                self.log("[信息] 战斗胜利结算页，ESC 退出")
                self.bot.press_esc(label='结算页_ESC')
                self._sleep(1.5)
                continue

            if state == 'battle_prep':
                self._auto_challenge_and_wait()
                continue

            if state == 'sweep_dialog':
                self._handle_sweep_dialog()
                continue

            # ── unknown 降级链（v1.8.12 全局规则，labyrinth 同款）──
            unknown_streak += 1
            # v1.8.27：全局注册表指名导航域画面（玩法目录/世界等）=
            # 已被踢出女神试炼执行域（oneshot 无续局）→ 如实判 failed
            _last: str = getattr(self, '_last_screen', 'unknown')
            if _last.startswith('nav.'):
                self.log(
                    f"[警告] 已离开女神试炼执行域（识别为：{zh_screen(_last)}）"
                )
                return "failed"
            if self.dismiss_blink_popup(items):
                unknown_streak = 0
                continue
            if unknown_streak >= _UNKNOWN_GIVE_UP:
                self.log("[警告] 连续未知画面超限，尝试模板返回键兜底")
                if self.bot.click_back_button(
                        _BACK_BUTTON_TPLS, threshold=_BACK_THRESHOLD):
                    self.log("[信息] 返回键点击成功，重置未知计数继续")
                    unknown_streak = 0
                    self._sleep(1.5)
                    continue
                self.log("[错误] 连续未知画面超限且返回键无效，任务终止")
                return "failed"
            if unknown_streak >= _UNKNOWN_ESC_AFTER:
                # v1.8.24：ESC 与返回按钮模板交替（用户定案：ESC 不万能）
                self.escape_alternating(unknown_streak, label='女神试炼未知')
            else:
                self._sleep(_STATE_POLL)

    # ── 挑战流程 ───────────────────────────────────────────

    def _auto_challenge_and_wait(self) -> None:
        """备战页点「自动挑战」→ 等自动连打结束回选关页。

        用户确认（2026-09-04）：自动挑战会一直打到当前可挑战最后一层，
        等待自动结束即可，不可能打不过 → 无失败分支，只等选关页特征词。
        """
        if not self._click_text_kw(_AUTOCHECK_MARK, min_conf=0.6):
            self.log("[警告] 未找到「自动挑战」按钮，返回主循环重试")
            return
        self.log("[信息] 已点「自动挑战」，等待自动连打结束（上限 900s）")
        deadline: float = time.time() + _AUTO_BATTLE_WAIT
        while time.time() < deadline:
            if self.check_stop():
                return
            self._sleep(_AUTO_BATTLE_POLL)
            items = self.bot._ocr_screen()
            state: str = self._detect_state(items)
            if state in ('select_page', 'sweep_dialog'):
                self.log("[信息] 自动挑战结束，已回到选关页")
                return
            if state == 'victory_result':
                # 最后一关通关结算页 → ESC 退出（真机 2026-09-04 用户反馈：
                # 结束后需按 ESC 或返回键；连打中间层不弹，仅最终弹一次）
                self.log("[信息] 最后一关通关，结算页 ESC 退出")
                self.bot.press_esc(label='自动挑战_结算页_ESC')
                self._sleep(1.5)
                continue
            # 战斗中画面（「自动挑战中...」HUD 或无特征）属预期，不打断
        self.log("[警告] 自动挑战等待超时（900s），交由主循环兜底")

    # ── 扫荡流程 ───────────────────────────────────────────

    # ── 扫荡次数徽记判别（v1.8.28）──────────────────────────
    # 扫荡前 6/1（白字 OCR 0.99）↔ 扫荡后 0/1（灰字 OCR 读不出，真机实测
    # 只出 '③1'/'8/1' 噪声）→ 判别规则反转：按钮附近读出 X/1 = 有次数
    _SWEEP_COUNT_RE = re.compile(r'(\d+)\s*/\s*1\b')
    _SWEEP_EXHAUSTED_MARK: str = '已无扫荡次数'

    def _read_sweep_remaining(
        self, items: List[Tuple], btn_pos: Tuple[int, int]
    ) -> Optional[int]:
        """从扫荡按钮下方徽记读剩余次数 X/1（真机：徽记在按钮下方 ~31px）。

        Args:
            items: 已有 OCR 帧
            btn_pos: 「扫荡」大按钮 (cx, cy) 图像坐标

        Returns:
            int|None: 剩余次数；徽记文本不存在或 OCR 读不出（0/1 灰字态）→ None
        """
        bx, by = btn_pos
        best: Optional[int] = None
        for it in items:
            if len(it) < 4 or it[3] < 0.4:
                continue
            m = self._SWEEP_COUNT_RE.search(it[2])
            if not m:
                continue
            cx, cy = it[1], it[0]
            if abs(cx - bx) <= 90 and 10 <= cy - by <= 70:
                best = int(m.group(1))
        return best

    def _sweep_exhausted_toast(self, items: List[Tuple]) -> bool:
        """「今日已无扫荡次数」toast 检测（点扫荡后的瞬时报条，加分判据——
        特写小图 OCR 不稳定，全屏帧读出即用，读不出不影响主流程）。"""
        return any(
            self._SWEEP_EXHAUSTED_MARK in it[2]
            for it in items if len(it) >= 4 and it[3] >= 0.5
        )

    def _handle_sweep_dialog(self) -> None:
        """扫荡弹窗：⏭ 拉满次数 → 点「扫荡」确认 → 等回选关页。

        ⏭ 拉满按钮无文字（OCR 不可及）→ RGBA 模板 maskedNCC（自截
        1.000 @ 滑条行右端）；模板未命中时降级直接扫荡当前次数
        （扫 1 次比卡死强，日志说明）。
        """
        # 1. ⏭ 拉满
        pos = self.bot.template_match(
            _SWEEP_MAX_TPL, threshold=_SWEEP_TPL_THRESHOLD, silent=True
        )
        if pos:
            self.log(f"[点击] ⏭ 拉满 | 屏幕=({pos[0]},{pos[1]})")
            self.bot._click_abs(pos[0], pos[1])
            self._sleep(1.0)
        else:
            self.log("[警告] ⏭ 模板未命中，按当前次数扫荡（降级）")

        # 2. 点「扫荡」确认（全等匹配——弹窗内也有「扫荡次数」标签）
        if not self._click_exact_word(_SWEEP_WORD, label='扫荡_确认'):
            self.log("[警告] 未点中「扫荡」确认，返回主循环重试")
            return
        self._swept = True

        # 3. 等回选关页（可能弹扫荡结果奖励弹窗——无素材，靠闪烁弹窗
        #    全局规则 + 点空白兜底；真机首跑校准）
        deadline: float = time.time() + _SWEEP_RESULT_WAIT
        while time.time() < deadline:
            if self.check_stop():
                return
            self._sleep(_STATE_POLL)
            items = self.bot._ocr_screen()
            if self._detect_state(items) == 'select_page':
                self.log("[信息] 扫荡完成，已回选关页")
                return
            self.dismiss_blink_popup(items)   # 结果弹窗「点击空白处」兜底
        self.log("[警告] 扫荡后等待回选关页超时，交由主循环兜底")


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🌟",
    group="游戏任务",
    status="ready",   # v1.8.17 实装：导航+玩法循环，待真机验证
    options_schema=GoddessTrialTask.options_schema,
    oneshot=True,     # v1.8.22：单次任务（追平上限+扫荡即完成），GUI 隐藏时间/轮次
    nav_target='goddess_trial',   # v1.8.31：入口卡片在玩法目录 → 只回到玩法目录
))
