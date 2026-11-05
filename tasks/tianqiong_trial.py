"""天穹试炼（赛季四塔轮换）自动挑战。

【入口】玩法目录 → 常规玩法 →（**向下滚动**才能看到）「天穹试炼」卡片
【主界面】赛季天穹试炼 = 四座塔（光辉 / 自然 / 不朽 / 意志），每日轮换开启，
        **周六周日全部开放**；卡片显示「出发！」= 当日可进，
        显示「不在开放时间内」= 不可进。
【流程】塔列表「出发！」→ 塔内楼层页「挑战！」→ 备战（通关阵容 →
        一键采用 → 按 F）→ 结算 → 塔内下一层「挑战！」→ …；
        某塔已无「挑战」→ 返回塔列表 → 换下一座可进的塔；
        塔列表里没有可点的「出发！」→ 今日无开放塔 → done。
【判据】全部走 **OCR 文本**（不依赖任何新模板），实测置信度见下：

  玩法目录（1284×1061）：「天穹试炼」(779,458) conf=0.989
        ⚠️ 同屏还有「女神试炼」(490,460) 0.983 → 必须用完整四字匹配 ✗
  塔列表页（1282×1027）：
        「选择目的地」(261,639) conf=0.994  ← 主判据（最稳）
        「季天穹试炼」(75,346) 0.980（标题被拆成「赛」+「季天穹试炼」）
        「出发！」(701,853) conf=0.957     ← 可进入（全角感叹号）
        「不在开放时间内」0.985~0.991（cy=419/572/867, cx≈508~518）
        「赛季光辉之塔」(324,490) / 「赛季自然之塔」(472,482)
        「赛季不朽之塔」(618,510) / 「赛季意志之场」(774,496) 0.885
        ⚠️ 末者为误读，实为「意志之**塔**」→ 塔名匹配须兼容「之场」
  塔内楼层页（1280×1026）：
        「赛季不朽之塔」(50,347) 0.975 /「还差2层」(106,358) 0.993
        「挑战！」(673,747) conf=0.988     ← 全角感叹号
        「已通关」(864,472) 0.960 /「未解锁」0.989（更高层）
"""
from __future__ import annotations

import re
import time
from typing import List, Optional, Tuple

from tasks.base import BACK_BUTTON_TPLS, BACK_THRESHOLD, BaseTask
from task_registry import TaskMeta, TaskRegistry

task_id = "tianqiong_trial"
label = "天穹试炼"
description = "四塔轮换：塔列表「出发！」→ 塔内「挑战！」→ 通关阵容/一键采用/F"

# ── 判据常量（全部来自真机截图 OCR）───────────────────────────
_CARD_TEXT: str = '天穹试炼'          # 玩法目录卡片（完整四字，避开「女神试炼」）
_CARD_MIN_CONF: float = 0.6
_LIST_WORD: str = '选择目的地'        # 塔列表页主判据（conf 0.994）
_TOWER_KWS: Tuple[str, ...] = ('之塔', '之场')   # 「之场」= 「之塔」的已知误读
_GO_WORD: str = '出发'                # 「出发！」去全角标点后全等
_CHALLENGE_WORD: str = '挑战'         # 「挑战！」去全角标点后全等（排除「挑战奖励」）
_CLOSED_WORD: str = '不在开放时间内'   # 该塔今日不可进
_LOCKED_WORD: str = '未解锁'          # 塔内更高层
_CLEARED_WORD: str = '已通关'         # 当前层已通关
_PREP_WORDS: Tuple[str, ...] = ('通关阵容', '一键采用')
_RESULT_WORDS: Tuple[str, ...] = (
    # 「下一关」= 天穹胜利结算页的主按钮（真机 2026-11-05 19:44 截图）；
    # 大标题「战斗胜利」是艺术字，OCR 常读不出，靠按钮反推更稳。
    '下一关', '战斗胜利', '战斗失败', '继续挑战', '再次挑战', '推荐提升',
)

_TEXT_MIN_CONF: float = 0.7      # 文本判据阈值（实测都 ≥0.88）
_BTN_MIN_CONF: float = 0.85      # 按钮判据阈值（挑战 0.988）
# v2.3.23：「出发」只有两个字，短文本 OCR 置信度波动大（实测截图 0.957，
# 但真机实时识别可能偏低）→ 单独放宽到 0.6，避免漏检后误判「今日无开放塔」。
_GO_MIN_CONF: float = 0.6
_NO_GO_CONFIRM: int = 3          # 连续 N 帧找不到「出发」才判定今日无开放塔

_SCROLL_CLICKS: int = -30        # 负值 = 向下滚
_SCROLL_SETTLE: float = 0.3

# ── 防死循环护栏（v2.3.21 QA 复核：此前点击无效会无限重试）──────
# oneshot=True 会把 max_time/max_turns 强制为 None，max_battles 默认 0，
# 若「挑战/出发/F」任一失效且没有上限，任务永不结束（实测连点 120 次）。
_MAX_ROUNDS: int = 400           # 单次任务硬上限（四塔满层正常约 100+ 轮）
_NO_PROGRESS_BACK: int = 3       # 画面连续无变化 N 帧 → 返回上一层重试
_NO_PROGRESS_FAIL: int = 6       # 画面连续无变化 N 帧 → 终止
_F_TIMEOUT_FAIL: int = 2         # 连续 N 次按 F 后未结算 → 终止
# v2.3.22（真机 19:35）：点「出发！」后有转场动画，首帧常读不到「挑战！」
# → 旧逻辑 4 秒就判「本塔已无可挑战层」直接返回。改为多帧确认。
_NO_CHALLENGE_CONFIRM: int = 3   # 连续 N 帧（间隔 2s）无「挑战」才算本塔结束
_ENTER_TOWER_TIMEOUT: float = 15.0   # 点「出发」后等塔内页稳定的上限
_SCROLL_MAX: int = 14
_ENTER_TIMEOUT: float = 12.0     # 点卡片后等塔列表
_RESULT_TIMEOUT: float = 90.0    # 战斗结算最长等待
_STALL_ESC: int = 3              # 未知画面 → ESC/返回交替
_STALL_BACK: int = 6             # 未知画面 → 返回塔列表
_STALL_MAX: int = 10             # 未知画面 → 判定失败

# 全角/半角标点与空白一律剔除（\W 在 Unicode 下保留中文与数字）
_PUNCT_RE = re.compile(r'[\W_]+', re.UNICODE)


def _norm(text: str) -> str:
    """去掉标点与空白后的归一化文本（「出发！」→「出发」）。"""
    return _PUNCT_RE.sub('', str(text))


class TianqiongTrialTask(BaseTask):
    """天穹试炼：当天开放的塔逐个打，塔内循环「挑战！」直到没有可挑战层。"""

    task_id = task_id
    label = label
    description = description

    # v2.3.21（QA 复核）：原 `loop_screens` 声明了 tianqiong.* 三个 id，但
    # 它们从未在 screen_registry 注册 → 永远不为真，是**死声明**（调度器照常
    # 走完整导航）。已移除，避免误导；若将来要做「就地续跑」，必须先注册判据。

    options_schema = {
        'max_battles': {
            'type': 'int',
            'label': '最多次数',
            'default': 0,
            'desc': '0 = 不限（打完当天开放的塔为止）',
        },
    }

    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self.battles: int = 0                 # 实际开打局数
        # 已确认「无挑战层」的「出发！」按钮坐标 → 避免同一座塔反复进出
        self._exhausted_go: set = set()
        self._cur_go: Optional[Tuple[int, int]] = None

    # ── 基类契约 ────────────────────────────────────────────

    def preflight(self) -> Tuple[bool, str]:
        """校验游戏窗口存在并置前（与其他玩法一致）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def startup_probe(self) -> str:
        """一帧 OCR 自判当前是否已在执行域（避免无谓导航）。

        Returns:
            'ready'  — 塔列表 / 塔内楼层 / 备战（可直接续跑）
            'clear'  — 不在执行域（走导航 + 滚动找卡片）
            'failed' — OCR 异常
        """
        try:
            items = self.bot._ocr_screen()
        except Exception as exc:                       # noqa: BLE001
            self.log(f"[错误] 启动探测 OCR 异常：{exc}")
            return 'failed'
        # 按**特异性**排序：备战 ⊂ 塔内 ⊂ 塔列表
        if self._is_battle_prep(items):
            self.log("[信息] 启动即在备战界面 → 直接续跑")
            return 'ready'
        if self._is_floor_page(items):
            self.log("[信息] 启动即在塔内楼层页 → 直接续跑")
            return 'ready'
        if self._is_tower_list(items):
            self.log("[信息] 启动即在塔列表 → 直接续跑")
            return 'ready'
        self.dismiss_blink_popup(items)
        return 'clear'

    def _stop_result(self) -> str:
        """手动停止 → 'stopped'；达到 max_battles → 'done'。"""
        mb = int(self.options.get('max_battles') or 0)
        if mb > 0 and self.battles >= mb:
            self.log(f"[成功] 已达最多次数上限 {mb} → 结束天穹试炼")
            return 'done'
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def summary(self) -> str:
        """GUI 折叠态摘要（≤14 字）。"""
        return "自动打当天开放的天穹塔"

    def run(self) -> str:
        """probe →（必要时导航并滚动找卡片）→ 塔内循环。"""
        self.log("启动（玩法目录 → 天穹试炼 → 塔内循环挑战）")
        probe = self.startup_probe()
        if probe == 'failed':
            if self.check_stop():
                return 'stopped'
            self.log("[错误] 启动探测失败")
            return 'failed'
        if probe != 'ready':
            if not self._enter_tianqiong():
                self.log_nav_failed("天穹试炼")
                return 'failed'
        if self.check_stop():
            return 'stopped'
        return self._play_loop()

    # ── 判据 ────────────────────────────────────────────────

    def _find_exact(self, items: List[Tuple], word: str,
                    min_conf: float = 0.6
                    ) -> Optional[Tuple[int, int]]:
        """**去标点后全等**匹配，返回 (cx, cy)。

        为什么不用 `bot.find_text(exact=True)`：其语义在子串/全等之间反复，
        且不处理全角标点（「出发！」「挑战！」都会失配）→ 本任务自备实现。
        """
        target = _norm(word)
        for it in items:
            if len(it) < 4 or it[3] < min_conf:
                continue
            if _norm(it[2]) == target:
                return (int(it[1]), int(it[0]))
        return None

    def _is_tower_list(self, items: List[Tuple]) -> bool:
        """塔列表页：「选择目的地」为主判据；或 ≥2 个「之塔 / 之场」塔名。"""
        texts = self._texts(items, _TEXT_MIN_CONF)
        if any(_LIST_WORD in t for t in texts):
            return True
        towers = sum(1 for t in texts if any(k in t for k in _TOWER_KWS))
        return towers >= 2

    def _is_floor_page(self, items: List[Tuple]) -> bool:
        """塔内楼层页。

        主判据：「未解锁」（更高层）且（「挑战！」或「已通关」）。
        兜底（v2.3.21）：某塔全部通关后可能不再显示「未解锁」，此时用
        「塔名 + 已通关/还差N层」判定，避免误落 unknown 后按 ESC 脱离。
        """
        texts = self._texts(items, _TEXT_MIN_CONF)
        has_locked = any(_LOCKED_WORD in t for t in texts)
        has_action = (any(_norm(t) == _CHALLENGE_WORD for t in texts)
                      or any(_CLEARED_WORD in t for t in texts))
        if has_locked and has_action:
            return True
        has_tower = any(k in t for t in texts for k in _TOWER_KWS)
        has_progress = (any(_CLEARED_WORD in t for t in texts)
                        or any('还差' in t for t in texts))
        return has_tower and has_progress

    def _is_battle_prep(self, items: List[Tuple]) -> bool:
        """备战界面：「通关阵容」或「一键采用」（同挂机/结社战线）。"""
        texts = self._texts(items, _TEXT_MIN_CONF)
        return any(w in t for t in texts for w in _PREP_WORDS)

    def _is_result_page(self, items: List[Tuple]) -> bool:
        """战斗结算页：胜利/失败标题或「继续挑战/再次挑战」按钮。"""
        texts = self._texts(items, _TEXT_MIN_CONF)
        return any(w in t for t in texts for w in _RESULT_WORDS)

    def _find_go(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """找当日可进入的「出发！」按钮坐标 (cx, cy)；跳过已确认无层的塔。

        阈值用 `_GO_MIN_CONF`(0.6) 而非按钮通用的 0.85：「出发」仅两字，
        短文本 OCR 置信度波动大，卡 0.85 会漏检（真机 2026-11-05 19:38
        即因此误判「今日无开放中的天穹塔」直接结束）。
        """
        for it in items:
            if len(it) < 4 or it[3] < _GO_MIN_CONF:
                continue
            if _norm(it[2]) != _GO_WORD:
                continue
            pos = (int(it[1]), int(it[0]))
            if pos in self._exhausted_go:
                continue
            return pos
        return None

    def _find_challenge(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """找塔内「挑战！」坐标 (cx, cy)。

        去标点后**全等** → 天然排除「挑战奖励：」（归一见「挑战奖励」）
        与「继续挑战 / 再次挑战」（归一见完整四字）。
        """
        return self._find_exact(items, _CHALLENGE_WORD, _BTN_MIN_CONF)

    def _cands(self, items: List[Tuple]) -> str:
        """诊断：找不到按钮时打印候选（便于真机按日志微调）。"""
        out = [f'{it[2]}({int(it[1])},{int(it[0])},{it[3]:.2f})'
               for it in items if len(it) >= 4 and it[3] >= 0.5]
        return '; '.join(out[:18]) if out else '无'

    # ── 导航 ────────────────────────────────────────────────

    def _goto_playbook(self) -> bool:
        """回玩法目录（复用全局导航，**不新增导航目标**）。"""
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(self.bot, logger=self.log,
                              stop_check=self.check_stop)
        if not nav.navigate_to('playbook'):
            self._nav_last_screen = getattr(nav, 'last_screen', '')
            return False
        return True

    def _enter_tianqiong(self) -> bool:
        """玩法目录 →（向下滚动）→ 点「天穹试炼」卡片 → 等塔列表。"""
        if not self._goto_playbook():
            return False
        return self._find_and_enter_card()

    def _find_and_enter_card(self) -> bool:
        """滚动查找「天穹试炼」卡片（照女神试炼：画面不再变化 = 已到底）。"""
        prev: Optional[Tuple[str, ...]] = None
        for _ in range(_SCROLL_MAX):
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            pos = self._find_exact(items, _CARD_TEXT, _CARD_MIN_CONF)
            if pos:
                self.log(f"[点击] 找到「{_CARD_TEXT}」卡片 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '玩法目录_天穹试炼')
                if self._wait_tower_list():
                    self.log("[成功] 已进入赛季天穹试炼（塔列表）")
                    return True
                self.log("[警告] 点击「天穹试炼」后未进入塔列表，继续滚动")
            cur = tuple(sorted(self._texts(items, 0.5)))
            if prev is not None and cur == prev:
                self.log(f"[警告] 玩法目录已滚到底，未找到「{_CARD_TEXT}」"
                         f"｜候选={self._cands(items)}")
                return False
            prev = cur
            self.bot.scroll_wheel(_SCROLL_CLICKS, rx=0.5, ry=0.5,
                                  label='玩法目录_向下')
            self._sleep(_SCROLL_SETTLE)
        self.log(f"[警告] 滚动 {_SCROLL_MAX} 次仍未找到「{_CARD_TEXT}」")
        return False

    def _wait_tower_list(self) -> bool:
        """点卡片后轮询等待塔列表出现。"""
        deadline = time.time() + _ENTER_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            if self._is_tower_list(items):
                return True
            self._sleep(0.8)
        return False

    # ── 备战（同挂机 / 结社战线）─────────────────────────────

    def _setup_lineup(self) -> None:
        """备战界面：通关阵容 → 一键采用。"""
        pos = self._find_exact(self.bot._ocr_screen(), '通关阵容', _TEXT_MIN_CONF)
        if pos:
            self.log(f"[点击] 「通关阵容」 | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '备战_通关阵容')
            self._sleep(1.5)
        pos2 = self._find_exact(self.bot._ocr_screen(), '一键采用', _TEXT_MIN_CONF)
        if pos2:
            self.log(f"[点击] 「一键采用」 | 图像={pos2}")
            self.bot.click_at(pos2[0], pos2[1], '备战_一键采用')
            self._sleep(1.5)

    def _press_f(self) -> None:
        """按 F 开战（**不在这里计数**：F 失效时会虚增，见 v2.3.21）。"""
        self.log("[信息] 按 F 开始战斗")
        self.bot.press_f('天穹试炼_按F')
        self._sleep(2.0)

    def _credit_battle(self) -> None:
        """确认结算后才计入统计（`turns` 只读，只能递增 stats）。"""
        try:
            self.stats['total_battles'] = int(
                self.stats.get('total_battles', 0)) + 1
        except Exception:                              # noqa: BLE001
            pass
        self.battles += 1

    def _wait_result(self) -> bool:
        """轮询等结算（≤90s）：回到塔内楼层页或结算页即返回。"""
        deadline = time.time() + _RESULT_TIMEOUT
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            if self.dismiss_blink_popup(items=items):
                continue
            if self._is_floor_page(items) or self._is_result_page(items):
                return True
            self._sleep(1.5)
        self.log(f"[警告] 战斗超过 {int(_RESULT_TIMEOUT)}s 未结算 → 继续主循环")
        return False

    def _wait_enter_tower(self,
                          timeout: float = _ENTER_TOWER_TIMEOUT) -> bool:
        """点「出发！」后轮询等塔内页稳定，返回 True 表示已进入。

        真机 2026-11-05 19:35：点出发后有**转场动画**，首帧 OCR 读不到
        「挑战！」→ 旧逻辑 4 秒就判「本塔今日已无可挑战层」直接返回塔列表，
        实际塔内有可挑战层。这里以「挑战！」或「已通关/未解锁」出现为准。
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            if self.dismiss_blink_popup(items=items):
                self._sleep(1.0)
                continue
            if self._find_challenge(items):
                return True
            texts = self._texts(items, _TEXT_MIN_CONF)
            if any(_CLEARED_WORD in t or _LOCKED_WORD in t for t in texts):
                return True
            self._sleep(1.5)
        return False

    def _back(self) -> None:
        """返回上一层（模板返回按钮优先，ESC 兜底）。"""
        try:
            if self.bot.click_back_button(BACK_BUTTON_TPLS,
                                          threshold=BACK_THRESHOLD):
                self.log("[点击] 返回按钮（模板）")
                self._sleep(1.5)
                return
        except Exception as exc:                       # noqa: BLE001
            self.log(f"[调试] 返回按钮异常已忽略: {exc}")
        self.log("[警告] 返回按钮模板未命中 → ESC 兜底")
        self.bot.press_esc(label='天穹试炼_返回ESC')
        self._sleep(1.5)

    def _close_result(self, items: List[Tuple]) -> None:
        """结算页：优先点「下一关」，其次「继续挑战/再次挑战」，否则返回。

        真机 2026-11-05 19:44（用户截图）：天穹胜利结算页的主按钮是
        **「下一关」**（绿色），旧逻辑只找「继续挑战/再次挑战」→ 找不到就
        按返回，直接退出了塔内，一局打完就结束。必须点「下一关」继续下一层。
        """
        for word in ('下一关', '继续挑战', '再次挑战'):
            pos = self._find_exact(items, word, _TEXT_MIN_CONF)
            if pos:
                self.log(f"[点击] 结算页「{word}」 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], f'天穹试炼_{word}')
                self._sleep(2.5)
                return
        self.log("[信息] 结算页无可点按钮 → 关闭（返回 / ESC）")
        self._back()

    def _battle_once(self) -> bool:
        """一局：通关阵容 → 一键采用 → F → 等结算。

        Returns:
            bool: True = 已确认结算（此时才计数）；False = 超时未结算
        """
        self._setup_lineup()
        self._press_f()
        ok = self._wait_result()
        if ok:
            self._credit_battle()
        return ok

    # ── 主循环（状态机）────────────────────────────────────

    def _play_loop(self) -> str:
        """塔列表 → 塔内 → 备战 → 结算 的状态机。

        分支优先级按**特异性**：备战 ⊂ 塔内 ⊂ 塔列表
        （塔内楼层页也可能出现多个「之塔」文案，先判塔内更安全）。
        """
        stall: int = 0
        # v2.3.21 防死循环三件套（QA 实测：点击无效会无限重试）
        rounds: int = 0
        no_progress: int = 0
        f_timeout: int = 0
        no_challenge: int = 0        # v2.3.22：塔内连续未见「挑战」的确认计数
        no_go: int = 0               # v2.3.23：塔列表连续未找到「出发」的确认计数
        prev_fp: Tuple[str, ...] = ()
        while True:
            if self.check_stop():
                return self._stop_result()
            mb = int(self.options.get('max_battles') or 0)
            if mb > 0 and self.battles >= mb:
                return self._stop_result()
            rounds += 1
            if rounds > _MAX_ROUNDS:
                self.log(f"[错误] 超过 {_MAX_ROUNDS} 轮仍未结束 → 终止（防死循环）")
                return 'failed'
            self.count_iter()

            items = self.bot._ocr_screen()
            if self.dismiss_blink_popup(items=items):
                continue

            # ── 画面无进展检测：点击后画面文本集合完全不变 → 该动作无效 ──
            fp: Tuple[str, ...] = tuple(sorted(
                str(it[2]) for it in items if len(it) >= 4))
            if fp != prev_fp:
                prev_fp = fp
                no_progress = 0
            else:
                no_progress += 1
                if no_progress >= _NO_PROGRESS_FAIL:
                    self.log(f"[错误] 画面连续 {no_progress} 帧无变化 → 终止"
                             f"｜候选={self._cands(items)}")
                    return 'failed'
                if no_progress == _NO_PROGRESS_BACK:
                    # 注意：这里**不清零** —— 清零会导致「3→back→3→back」永远
                    # 到不了 FAIL 阈值（自身踩过的坑）。只有画面真变了才清零。
                    self.log(f"[警告] 画面连续 {no_progress} 帧无变化"
                             " → 返回上一层重试")
                    self._back()
                    continue

            # ① 备战 → 打一局
            if self._is_battle_prep(items):
                stall = 0
                self.log("[信息] 备战界面 → 通关阵容 → 一键采用 → F")
                if not self._battle_once():
                    f_timeout += 1
                    if f_timeout >= _F_TIMEOUT_FAIL:
                        self.log(f"[错误] 连续 {f_timeout} 次按 F 后战斗未结算"
                                 " → 终止（窗口失焦/F 未生效？）")
                        return 'failed'
                else:
                    f_timeout = 0
                continue

            # ② 塔内楼层 → 点「挑战！」
            if self._is_floor_page(items):
                pos = self._find_challenge(items)
                if pos:
                    stall = 0
                    no_challenge = 0
                    self.log(f"[点击] 「挑战！」 | 图像={pos}")
                    self.bot.click_at(pos[0], pos[1], '天穹试炼_挑战')
                    self._sleep(2.5)
                    continue
                # v2.3.22：无「挑战」必须先连续多帧确认 —— 刚进塔的转场动画
                # 会遮住按钮，单帧读不到就返回会**误判「本塔已打完」**
                # （真机 19:35：进塔 4 秒即返回，实际有可挑战层）。
                no_challenge += 1
                if no_challenge < _NO_CHALLENGE_CONFIRM:
                    self.log(f"[信息] 塔内暂未读到「挑战！」"
                             f"（{no_challenge}/{_NO_CHALLENGE_CONFIRM}）"
                             f"，等待转场结束｜候选={self._cands(items)}")
                    self._sleep(2.0)
                    continue
                no_challenge = 0
                # 多次确认仍无「挑战」→ 本塔已打完；用 stall 防「返回无效」死循环
                stall += 1
                if stall >= _STALL_MAX:
                    self.log(f"[错误] 塔内无「挑战」且返回无效（连续 {stall} 帧）"
                             f"→ 终止｜候选={self._cands(items)}")
                    return 'failed'
                self.log(f"[信息] 本塔今日已无可挑战层"
                         f"（已连续 {_NO_CHALLENGE_CONFIRM} 次确认）→ 返回塔列表")
                if self._cur_go:
                    self._exhausted_go.add(self._cur_go)
                self._back()
                continue

            # ③ 结算页残留 → 关掉
            if self._is_result_page(items):
                stall = 0
                self._close_result(items)
                continue

            # ④ 塔列表 → 点「出发！」；没有则今日结束
            if self._is_tower_list(items):
                stall = 0
                pos = self._find_go(items)
                if not pos:
                    # v2.3.23：单帧读不到「出发！」不得直接判结束 —— 真机
                    # 19:38 就因此 2 秒误退（实际当天有开放塔）。改为连续
                    # 多帧确认，并打印当帧候选便于定位是 OCR 漏检还是确实无塔。
                    no_go += 1
                    if no_go < _NO_GO_CONFIRM:
                        self.log(f"[信息] 塔列表暂未找到「出发！」"
                                 f"（{no_go}/{_NO_GO_CONFIRM}），等待确认"
                                 f"｜候选={self._cands(items)}")
                        self._sleep(1.5)
                        continue
                    self.log(f"[成功] 连续 {_NO_GO_CONFIRM} 次未找到「出发！」"
                             "→ 今日无开放中的天穹塔 → 结束")
                    return 'done'
                no_go = 0
                self.log(f"[点击] 「出发！」 | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '天穹试炼_出发')
                self._cur_go = pos
                no_challenge = 0
                # v2.3.22：等塔内页稳定再交给主循环判断，避免转场期误判
                if not self._wait_enter_tower():
                    self.log(f"[警告] 点「出发！」后 {int(_ENTER_TOWER_TIMEOUT)}s "
                             "未确认进入塔内 → 重新识别画面")
                continue

            # ⑤ 未知画面
            stall += 1
            self.log(f"[信息] 天穹试炼：未知画面（等待 {stall}/{_STALL_MAX}）")
            if stall >= _STALL_MAX:
                self.log(f"[错误] 连续 {stall} 帧未知画面，终止"
                         f"｜候选={self._cands(items)}")
                return 'failed'
            if stall == _STALL_ESC:
                self.escape_alternating(stall, '天穹试炼未知画面')
            elif stall == _STALL_BACK:
                self.log("[警告] 连续未知 → 返回塔列表重试")
                self._back()
            else:
                self._sleep(1.2)


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🗼",
    group="游戏任务",
    status="ready",
    options_schema=TianqiongTrialTask.options_schema,
    nav_target='playbook',   # 入口在玩法目录（不新增导航目标）
    oneshot=True,            # 自带走次选项 → 隐藏全局时间/轮次
    show_metrics=False,
))
