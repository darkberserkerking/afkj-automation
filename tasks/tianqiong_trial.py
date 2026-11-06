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
from typing import Dict, List, Optional, Tuple

from tasks.base import BACK_BUTTON_TPLS, BACK_THRESHOLD, BaseTask
from task_registry import TaskMeta, TaskRegistry

task_id = "tianqiong_trial"
label = "天穹试炼"
# 任务栏文案（GUI `_instance_summary` 直接取 meta.description）：
# 只说「做什么」，不写操作步骤/实现细节；统一 ≤20 字，实测任务栏
# 描述行可用约 360px（12 号字），留足余量避免字体/DPI 变化后溢出。
description = "挑战当日开放塔，失败换阵容，全部完成即止"

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
# v2.3.26：塔卡片高度（实测四塔 cy=324/472/618/774，间距 148~156）。
# 用来把右侧「出发！」按钮归属到左侧最近的塔名。
_TOWER_CARD_H: float = 150.0

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
        # ── v2.3.25 失败换阵容（对齐自动推图 formation_idx 语义）──
        # 同一层失败一次就换下一套推荐阵容重试；`>` 消失或达上限 → 换塔。
        self._fail_idx: int = 0            # 当前层已失败次数 = 已切换阵容次数
        self._formation_exhausted: bool = False   # `>` 消失 = 推荐阵容已用完
        try:
            _cfg = getattr(ctx.get('bot'), 'config', {}) or {}
            self._max_formations: int = int(
                _cfg.get('max_formation_index', 10) or 10)
        except Exception:                              # noqa: BLE001
            self._max_formations = 10
        # ── v2.3.26 今日开放塔快照 ──
        # 塔列表上「已通关」与「可挑战」外观完全一致（都显示「出发！」），
        # 只能进塔确认（塔内无「挑战！」）后才能标记完成。故进塔列表时
        # 快照一次「今日开放哪几座塔」，之后按塔名/索引去重，不再重复进入。
        self._today_towers: Optional[List[dict]] = None
        self._cur_tower: Optional[dict] = None
        # 塔名 → 进入失败次数。连续 2 次点「出发」都进不去就跳过该塔，
        # 否则会死循环点同一个按钮（干跑 13e 实测连点 5 次）。
        self._tower_enter_fails: Dict[str, int] = {}
        self._no_arrow: int = 0     # 连续找不到「>」的次数（2 次才判阵容用完）

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
        # v2.3.31 用户规则（22:05 细化）：按元素类型分流
        #   有明确文字 → OCR（首选）；只有符号/图案 → 模板匹配
        self.log(
            "[信息] UI 定位分流（用户规则）：文字按钮「出发！/挑战！/下一关/"
            "再次挑战/通关阵容/一键采用/天穹试炼卡片」→ OCR；"
            "符号图案「>」用 btn_formation_next.png、返回用 btn_back_* → 模板")
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

        **定位方式（v2.3.31）**：「出发！」是**有明确文字**的按钮 → 按用户
        规则直接走 OCR（首选方案，不是降级），无需在模板库找文字模板。

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

    def _scan_open_towers(self, items: List[Tuple]) -> List[dict]:
        """快照塔列表上「今日开放」的塔（即显示「出发！」的那些）。

        归属规则：`出发` 的 cy 落在 `[塔名 cy, 塔名 cy + _TOWER_CARD_H)`
        区间内，即属于该塔。实测（截图 1282×1027）：
          塔名 cy = 324/472/618/774，出发 cy = 701 → 属于 618 的「不朽之塔」；
          701-472=229 > 150 → 不会误归给自然之塔。

        塔名用「之塔/之场」模糊匹配：「意志之塔」会被 OCR 读成「意志之场」。

        Returns:
            [{'name': '赛季不朽之塔', 'go': (cx, cy), 'done': False}, ...]
            按屏幕从上到下排序；扫描不到「出发」时返回空列表。
        """
        names: List[Tuple[float, str]] = []
        gos: List[Tuple[float, float]] = []
        for it in items:
            if len(it) < 4:
                continue
            try:
                cy, cx, cf = float(it[0]), float(it[1]), float(it[3])
            except (TypeError, ValueError):
                continue
            t = str(it[2]).strip()
            if cf >= _TEXT_MIN_CONF and (
                    any(k in t for k in _TOWER_KWS)
                    or ('赛季' in t and '塔' in t)):
                names.append((cy, t))
            elif cf >= _GO_MIN_CONF and _norm(t) == _GO_WORD:
                gos.append((cy, cx))
        names.sort()
        out: List[dict] = []
        for gy, gx in sorted(gos):
            owner = ''
            for ny, nt in names:
                if ny - 10 <= gy < ny + _TOWER_CARD_H:
                    owner = nt
                    break
            out.append({'name': owner or f'塔@{int(gy)}',
                        'go': (int(gx), int(gy)), 'done': False})
        return out

    def _find_challenge(self, items: List[Tuple]) -> Optional[Tuple[int, int]]:
        """找塔内「挑战！」坐标 (cx, cy)。

        **定位方式（v2.3.31）**：「挑战！」是**有明确文字**的按钮 → 按用户
        规则直接走 OCR（首选方案，不是降级）。

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

    def _setup_lineup(self, switch_count: int = 0) -> None:
        """备战界面：通关阵容 →（切换 N 套）→ 一键采用。

        Args:
            switch_count: 需要点 `>` 切换的阵容数（= 当前层已失败次数）。
                自动推图侧等价语义见 `afk_bot/auto_farm/formation.py:140`
                `switch_formation(times)`。
        """
        pos = self._find_exact(self.bot._ocr_screen(), '通关阵容', _TEXT_MIN_CONF)
        if pos:
            self.log(f"[点击] 「通关阵容」 | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '备战_通关阵容')
            self._sleep(1.5)
        if switch_count > 0:
            self._switch_formation(switch_count)
        pos2 = self._find_exact(self.bot._ocr_screen(), '一键采用', _TEXT_MIN_CONF)
        if pos2:
            self.log(f"[点击] 「一键采用」 | 图像={pos2}")
            self.bot.click_at(pos2[0], pos2[1], '备战_一键采用')
            self._sleep(1.5)

    # ── UI 定位分流（v2.3.31，用户 22:05 细化规则）──
    #   · 有明确文字的元素 → OCR（首选，不是降级）：
    #     「出发！」「挑战！」「下一关」「再次挑战」「通关阵容」「一键采用」
    #     「天穹试炼」卡片
    #   · 只有符号/图案的元素 → 模板匹配（templates/ 共 28 个，已核对）：
    #     `>` 用 btn_formation_next.png；返回用
    #     btn_back_arrow / btn_back_dark / btn_back_purple（已用于 _back）
    #   · 符号类模板未命中禁止改用 OCR 猜同一目标；仅此时才降级并说明原因。
    _ARROW_TPLS: Tuple[str, ...] = ('btn_formation_next.png',)
    # (x, y, w, h) 相对窗口比例，照抄自动推图 formation.py:132-138
    _ARROW_REGION_RATIO: Tuple[float, float, float, float] = (
        0.60, 0.25, 0.28, 0.50)

    def _arrow_region(self) -> Optional[Tuple[int, int, int, int]]:
        """`>` 模板搜索区域（**屏幕绝对坐标**）；拿不到窗口尺寸 → None（全屏）。

        比例来自自动推图：`>` 位于窗口 x≈0.69 / y≈0.54，限定
        x 0.60~0.88 / y 0.25~0.75，约 **14% 全屏面积**，且避开底部
        「一键采用」（y≈0.94 在区域外）。
        `facade.window_rect()` 文档明确该接口就是「模板 region 限界用」，
        取不到时返回 (0,0,0,0)，此处退化为全屏，保证功能不退化。
        """
        try:
            fn = getattr(self.bot, 'window_rect', None)
            if fn is None:
                return None
            l, t, w, h = fn()
            if w <= 0 or h <= 0:
                return None
            rx, ry, rw, rh = self._ARROW_REGION_RATIO
            return (int(l + w * rx), int(t + h * ry),
                    int(w * rw), int(h * rh))
        except Exception as exc:                       # noqa: BLE001
            self.log(f"[调试] 窗口尺寸获取失败，模板改全屏搜索: {exc}")
            return None

    def _find_btn_template(self, names: Tuple[str, ...],
                           threshold: float = 0.6
                           ) -> Optional[Tuple[int, int]]:
        """按顺序在模板库中匹配按钮：**局部 region 优先 + 全屏兜底 + 计时埋点**。

        v2.3.32：此前不传 `region`，走全屏 16 尺度 `matchTemplate`，真机
        22:30 日志反推单次约 7.5~8.5s（占备战耗时 67%）。改为先按窗口
        比例圈 14% 面积搜索；未命中再全屏重试一次，**保证不漏检**。
        每次匹配都打印耗时与 region，便于真机核对优化收益。

        Returns:
            Optional[Tuple[int, int]]: 匹配中心的**屏幕绝对坐标**
            （消费方必须用 `_click_abs`）。
        """
        region = self._arrow_region()
        scopes: List[Tuple[Optional[Tuple[int, int, int, int]], str]] = (
            [(region, '局部')] if region is not None else [])
        scopes.append((None, '全屏'))

        for nm in names:
            for reg, tag in scopes:
                hit: Optional[Tuple[int, int]] = None
                t0 = time.monotonic()
                try:
                    hit = self.bot.template_match(
                        nm, threshold=threshold, silent=True, region=reg)
                except Exception as exc:               # noqa: BLE001
                    self.log(f"[调试] 模板 {nm} 匹配异常已忽略: {exc}")
                finally:
                    self.log(
                        f"[耗时] 模板匹配「{nm}」"
                        f" | {time.monotonic() - t0:.2f}s"
                        f" | {tag} region={reg if reg is not None else '全屏'}"
                        f" | 命中={hit is not None}")
                if hit:
                    self.log(f"[信息] 模板命中「{nm}」"
                             f" | 屏幕=({int(hit[0])},{int(hit[1])}) | {tag}")
                    return (int(hit[0]), int(hit[1]))
        return None

    def _find_arrow(self) -> Optional[Tuple[int, int]]:
        """找阵容切换 `>` 按钮 —— **仅模板匹配，不做 OCR 兜底**。

        模板库检索范围：`templates/` 全部 28 个模板，与本按钮匹配的只有
        `btn_formation_next.png`（与自动推图 `formation.py:156` 同款）。

        Returns:
            Optional[Tuple[int, int]]: **屏幕绝对坐标**（供 `_click_abs`）；
            未命中返回 None，并打印检索范围与可能原因（规则要求）。
        """
        pos = self._find_btn_template(self._ARROW_TPLS, threshold=0.6)
        if pos is None:
            self.log(
                "[警告] 模板库未匹配到「>」切换按钮"
                f"（已检索 templates/ 全部 28 个，候选={list(self._ARROW_TPLS)}）"
                "｜可能原因：阵容面板未打开 / 被弹窗或鼠标遮挡 / 分辨率与模板不符"
                "｜降级：本次不切换阵容")
        return pos

    def _has_arrow(self) -> bool:
        """当前是否还有 `>` 切换按钮（**仅模板**，与 `_find_arrow` 同源）。"""
        return self._find_arrow() is not None

    def _switch_formation(self, times: int) -> bool:
        """在「通关阵容」面板内点 `>` 切换 N 套推荐阵容。

        v2.3.31 用户规则：**符号/图案类元素用模板**。`>` 是纯符号，
        用模板库 `btn_formation_next.png`；未命中时只记录检索范围与原因，
        不做 OCR 猜测（符号 OCR 曾导致误点工具按钮 + 误判阵容用完）。

        ⚠️ 坐标语义：`template_match` 返回**屏幕绝对坐标** → 必须配 `_click_abs`；
        用 `click_at` 会被再转一次 image→screen，双重偏移（真机 20:39 实测
        `> ` 落到 (2040,719)，点到工具自己的「定时」按钮）。

        Returns:
            bool: True = 还有阵容可切；False = 已到最后一套 / 本次找不到按钮
        """
        arrow = self._find_arrow()

        if arrow is None:
            self._no_arrow += 1
            self.log(f"[警告] 未找到「>」按钮（第 {self._no_arrow} 次）"
                     " → 本次不切换阵容")
            # 单次找不到多半是面板未开/遮挡，**不立即判"用完"**
            # （否则会过早换塔，真机 20:40 就因此每 2 分钟换一次塔）
            if self._no_arrow >= 2:
                self.log("[警告] 连续 2 次未找到「>」→ 判定推荐阵容已用完")
                self._formation_exhausted = True
            return False
        self._no_arrow = 0

        self.log(f"[信息] 切换阵容 {times} 套（点 > {times} 次）| 屏幕={arrow}")
        for i in range(times):
            if self.check_stop():
                return False
            self.bot._click_abs(arrow[0], arrow[1])      # 屏幕坐标
            self._sleep(0.6)
            if i < times - 1:
                self._sleep(0.2)

        # 验证 `>` 是否还在：**连续 2 次都读不到**才算消失
        # （单帧抖动/动画遮挡会误判，误判即过早换塔）
        still = False
        for _ in range(2):
            if self._has_arrow():
                still = True
                break
            self._sleep(1.0)
        if not still:
            self.log("[警告] 连续 2 次读不到「>」→ 推荐阵容已全部试过")
            self._formation_exhausted = True
            return False
        self.log(f"[成功] 已切换到第 {times + 1} 套阵容")
        return True

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

    def _back_to_tower_list(self, attempts: int = 3) -> bool:
        """返回塔列表并**确认已到达**（换塔 / 本塔打完时用）。

        真机 2026-11-05 20:40：`_back()` 后只 sleep 1.5s 就回主循环，塔内
        转场还没结束 → 又读到「挑战！」→ 反复进同一座塔（日志里每 2 分钟
        一次「切换另一座塔」，实际一直在同一座塔内打）。

        Returns:
            bool: True = 已确认在塔列表；False = 多次尝试仍未回到
        """
        for i in range(attempts):
            self._back()
            for _ in range(4):
                self._sleep(1.0)
                if self._is_tower_list(self.bot._ocr_screen()):
                    self.log("[成功] 已返回塔列表")
                    return True
            self.log(f"[警告] 第 {i + 1} 次返回后仍未到塔列表 → 重试")
        return False

    def _close_result(self, items: List[Tuple]) -> Optional[bool]:
        """结算页处理，返回本场胜负。

        Returns:
            True  = 胜利（点「下一关」，进入下一层）
            False = 失败（点「再次挑战」回同一层备战，由调用方决定换阵容/换塔）
            None  = 无按钮，走了返回兜底

        按钮文案实测（用户截图）：
          胜利结算 → 绿色「下一关」
          失败结算 → 绿色「再次挑战」（上方有「战斗失败」艺术字 + 推荐提升）
        """
        pos = self._find_exact(items, '下一关', _TEXT_MIN_CONF)
        if pos:
            self.log(f"[点击] 结算页「下一关」（胜利） | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '天穹试炼_下一关')
            self._sleep(2.5)
            return True
        pos = self._find_exact(items, '再次挑战', _TEXT_MIN_CONF)
        if pos:
            self.log(f"[点击] 结算页「再次挑战」（失败） | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '天穹试炼_再次挑战')
            self._sleep(2.5)
            return False
        pos = self._find_exact(items, '继续挑战', _TEXT_MIN_CONF)
        if pos:
            self.log(f"[点击] 结算页「继续挑战」 | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '天穹试炼_继续挑战')
            self._sleep(2.5)
            return True
        self.log("[信息] 结算页无可点按钮 → 关闭（返回 / ESC）")
        self._back()
        return None

    def _battle_once(self) -> bool:
        """一局：通关阵容 →（按失败次数切阵容）→ 一键采用 → F → 等结算。

        Returns:
            bool: True = 已确认结算（此时才计数）；False = 超时未结算
        """
        if self._fail_idx > 0:
            self.log(f"[信息] 复用第 {self._fail_idx + 1} 套阵容重试本层")
        self._setup_lineup(self._fail_idx)
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
                _tname = (self._cur_tower or {}).get('name') or '本塔'
                self.log(f"[信息] 「{_tname}」今日已无可挑战层"
                         f"（已连续 {_NO_CHALLENGE_CONFIRM} 次确认）"
                         "→ 标记为已通关，本次运行不再进入")
                # v2.3.26：记入今日塔快照，返回塔列表后自动跳过
                if self._cur_tower is not None:
                    self._cur_tower['done'] = True
                if self._cur_go:
                    self._exhausted_go.add(self._cur_go)
                # v2.3.28：必须**确认已回到塔列表**，否则转场期又读到
                # 「挑战！」会重复进同一座塔（真机 20:40 实测）
                if not self._back_to_tower_list():
                    self.log("[错误] 多次尝试仍无法返回塔列表 → 终止")
                    return 'failed'
                continue

            # ③ 结算页 → 胜利进下一层 / 失败换阵容（v2.3.25）
            if self._is_result_page(items):
                stall = 0
                _won = self._close_result(items)
                if _won is True:
                    # 进入新的一层 → 阵容从第 1 套重新开始
                    self._fail_idx = 0
                    self._formation_exhausted = False
                elif _won is False:
                    self._fail_idx += 1
                    self.log(f"[信息] 本层失败第 {self._fail_idx} 次"
                             f"（阵容上限 {self._max_formations}）"
                             f"→ 下一步换第 {self._fail_idx + 1} 套阵容")
                    if (self._fail_idx >= self._max_formations
                            or self._formation_exhausted):
                        self.log(f"[信息] 本层已试 {self._fail_idx} 套阵容仍无法通过"
                                 " → 切换另一座塔")
                        # 该塔本次运行不再进入（否则会回来重试同一层死循环）
                        if self._cur_tower is not None:
                            self._cur_tower['done'] = True
                        if self._cur_go:
                            self._exhausted_go.add(self._cur_go)
                        self._fail_idx = 0
                        self._formation_exhausted = False
                        # v2.3.28：确认回到塔列表再选下一座
                        if not self._back_to_tower_list():
                            self.log("[错误] 多次尝试仍无法返回塔列表 → 终止")
                            return 'failed'
                        continue
                continue

            # ④ 塔列表 → 点「出发！」；今日开放的塔都进过则结束
            if self._is_tower_list(items):
                stall = 0
                # v2.3.26：首次进塔列表时快照「今日开放塔」（按塔名去重）。
                # 塔列表上已通关/可挑战外观一致，无法从卡片区分，所以每座
                # 塔只进一次，进塔后无「挑战！」即标记完成、不再重复进入。
                if self._today_towers is None:
                    _snap = self._scan_open_towers(items)
                    if _snap:
                        self._today_towers = _snap
                        self.log(
                            f"[信息] 今日开放 {len(_snap)} 座塔："
                            + '、'.join(t['name'] for t in _snap))
                _next_tower: Optional[dict] = None
                if self._today_towers:
                    _next_tower = next(
                        (t for t in self._today_towers if not t['done']), None)
                    if _next_tower is None:
                        self.log(
                            "[成功] 今日开放的塔已全部尝试（"
                            + '、'.join(t['name'] for t in self._today_towers)
                            + "）→ 结束")
                        return 'done'
                    pos = _next_tower['go']          # type: ignore[assignment]
                    _go_name = _next_tower['name']
                else:
                    # 快照为空（OCR 全漏）→ 回退旧的单点查找
                    pos = self._find_go(items)       # type: ignore[assignment]
                    _go_name = ''
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
                self.log(f"[点击] 「出发！」"
                         f"{f'（{_go_name}）' if _go_name else ''} | 图像={pos}")
                self.bot.click_at(pos[0], pos[1], '天穹试炼_出发')
                self._cur_go = pos
                self._cur_tower = _next_tower
                # 进入新塔 → 该塔当前层的阵容尝试从第 1 套重新开始
                self._fail_idx = 0
                self._formation_exhausted = False
                no_challenge = 0
                # v2.3.22：等塔内页稳定再交给主循环判断，避免转场期误判
                if not self._wait_enter_tower():
                    _nm = (self._cur_tower or {}).get('name') or '本塔'
                    _n = self._tower_enter_fails.get(_nm, 0) + 1
                    self._tower_enter_fails[_nm] = _n
                    self.log(f"[警告] 点「出发！」后 "
                             f"{int(_ENTER_TOWER_TIMEOUT)}s 未确认进入「{_nm}」"
                             f"（第 {_n} 次）")
                    # 连续进不去 → 本次运行跳过该塔，避免死循环点同一个按钮
                    if _n >= 2 and self._cur_tower is not None:
                        self.log(f"[警告] 「{_nm}」连续 {_n} 次无法进入"
                                 " → 本次跳过该塔")
                        self._cur_tower['done'] = True
                        if self._cur_go:
                            self._exhausted_go.add(self._cur_go)
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
