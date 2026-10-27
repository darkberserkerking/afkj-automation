"""
任务：每日任务（v1.9.49 实装，用户 12:55 需求 + 双截图 OCR 实测）。

────────────────────────────────────────────
【入口路径】
  主菜单按钮（0 层 btn_menu_l0 / 1 层 btn_menu_l1，按当前所在层选择）
  → 侧滑菜单 → 「任务」按钮（btn_quest.png 模板 / OCR 全等兜底）
  → 每日任务界面

【页面判据】（MaaOCR 实测 2026-09-10 每日任务截图）
  每日任务界面：「每日任务」标题 0.97 / 页签 0.99 + 「一键领取」0.94
  活跃度进度条（顶部）：0→20→40→60→80→100，100 档 = 250 钻石
  奖励弹窗（如 250 钻石）：无「点击空白处」字样 → 点中央数字关闭

【两级标签】（用户 2026-09-11 18:21 需求 + 18:18 截图实测）
  一级：日常任务 / 翻翻乐 / 成长挑战 / 赛季任务 / 家园目标
  日常任务 下的二级：每日任务 / 公会任务
  进入任务界面后**必须确认在「日常任务 → 每日任务」**，不在则依次点
  一级「日常任务」→ 二级「每日任务」（见 `_ensure_daily_tab`）。
  选中态判据 = 二级标签**上边框橙色**（取色，实测 R−B 187 vs 未选中 17）。

【流程】
  startup_probe：奖励弹窗优先关闭 → 已在每日任务界面 → 直通；
  导航：菜单入口统一走 tasks/menu_nav.open_from_main_menu（v1.9.69
        与 friend_gift 共用，按当前所在层取按钮模板，2 层+ 先导航）；
  领取：「一键领取」OCR 循环 ≤3 次 → 弹层关闭（点空白/中央数字）；
        任务奖励给活跃度 → 满 100 点最高档（250 钻石）领活跃度奖励。

【架构】preflight/_stop_result/checklist 齐全（v1.9.39 铁律）。
────────────────────────────────────────────
"""

from typing import List, Optional, Tuple

import numpy as np

from task_registry import TaskRegistry, TaskMeta
from tasks.base import BaseTask

task_id = "daily_quest"
label = "每日任务"
description = "一键领取每日任务奖励（含活跃度 100 档活跃奖励）"

# 「任务」按钮模板（用户裁剪 RGBA；菜单面板内）
_QUEST_BTN_TPL: str = 'btn_quest.png'
_QUEST_BTN_THRESHOLD: float = 0.70
_CLAIM_MAX: int = 3             # 一键领取循环上限

# ── 活跃度奖励（100 档 = 250 钻石）读取参数（v2.2.4，用户 11:54 真机）──
# 真机 OCR 实测 2026-09-13：「250」@ 图像 (1082, 185.5) conf 0.99
# → rx 0.845 / ry 0.181；活跃度数值「160」@ (169, 136.5) conf 0.99 rx 0.132。
_ACT_MIN_CONF: float = 0.60      # 顶部「250」置信度下限（真机 0.78~0.99 波动）
_ACT_MAX_TRIES: int = 3          # 顶部读数重试次数（等进度条/宝箱动画结束）
_ACT_TRIES_WAIT: float = 1.2     # v2.2.18（用户 09-17：「截图中没领，工具没点或点错位置」+「那是动画遮挡」）：
# **改为以可读的「100」标签为锚** —— 实测该标签在"250 可见"与"250 被动画遮挡"
# 两种状态下都稳定可读（conf 0.98 ✓✓），而宝箱本身在遮挡态读不到 ✗。
# 偏移（同一张图内实测，与窗口装饰/缩放无关）：
#   09-13 截图：宝箱(250) @(1082,185.5)、100标签 @(1059,220.5)
#   → 宝箱 = 标签 + (dx +23, dy −35)
# ⚠️ 同时删掉旧的 `_ACT_RATIO_Y=0.181`：它取自**含窗口装饰的截图原始 y**，
#   比真实客户区低约 28px ✗ → 真机点在宝箱**下方**（进度条背景）上 → 点了
#   但没生效（这正是"日志有点击、奖励却没领"的原因）✗
_ACT_CHEST_FROM_100_DX: int = 23      # 宝箱 x = 「100」标签 x + 23
_ACT_CHEST_FROM_100_DY: int = -35     # 宝箱 y = 「100」标签 y − 35（在其上方）
_ACT_TAG_RY_MAX: float = 0.30         # 「100」标签需在顶部区域（防误取正文里的 100）
_ACT_RETRY_MAX: int = 3               # 未弹领取弹窗（= 没生效）时的补点上限
_ACT_MIN_VALUE: int = 100        # 活跃度达到该值才认为 100 档可领

# ── 任务界面两级标签（用户 2026-09-11 18:18 截图实测 1280×1020）────────
# 一级：日常任务 / 翻翻乐 / 成长挑战 / 赛季任务 / 家园目标
# 日常任务 下的二级：每日任务 / 公会任务
#   二级「每日任务」文字 @ 图像 (413.5, 881.5)；「公会任务」@ (924.0, 886.5)
#   选中态体现在**标签上边框的橙色高亮**：实测 选中 峰值 R−B≈187 /
#   未选中 全程 R−B≈17（干净分离）。二级标签行只在「日常任务」下出现，
#   故「二级 每日任务 呈选中态」⟺ 处于 日常任务→每日任务。
_TAB2_DAILY_X: float = 0.323    # 二级「每日任务」标签中心 rx
_TAB2_GUILD_X: float = 0.722    # 二级「公会任务」标签中心 rx
_TAB2_BORDER_Y: float = 0.838   # 二级标签「上边框」ry（取色判选中态）
_TAB1_DAILY: Tuple[float, float] = (0.391, 0.965)   # 一级「日常任务」点击点
_TAB2_DAILY: Tuple[float, float] = (0.324, 0.864)   # 二级「每日任务」点击点
_SELECT_RB_MIN: float = 80.0    # 选中态判据：边框 R−B 阈值（187 vs 17）
_TAB_SAMPLE_HALF: Tuple[int, int] = (18, 5)         # 取色窗口半宽/半高（px）

# 左上角「当前标签标题」区域与目标标题词（用户 18:24：界面左上角有当前标签标题）
# 真机实测（用户 8 张标签截图全量核对）：
#   标题在左侧最上方；其下方还有一行**副标题**（「刷新时间：…」/「…后结算」），
#   位置随标签而变（公会任务/翻翻乐在左侧，每日任务在右侧）→ 必须排除。
#   注意：翻翻乐/成长挑战/家园目标 的标题**首字是美术字图标**，OCR 常只读到
#   「翻乐」「成」「家」—— 但**判据仍安全**：在「每日任务」时标题是干净的
#   四字「每日任务」（两处真机样本均 0 误读），别的标签则必不等于它 →
#   不存在假阳性，最多"多切一次标签"（点已选中的标签无害）。
_TITLE_REGION_RY: float = 0.12      # 标题带（0.08 太紧：成长挑战/家园目标更低）
_TITLE_REGION_RX: float = 0.35      # 且靠左（右侧是「刷新时间」等）
_TITLE_SKIP_WORDS: Tuple[str, ...] = ('刷新时间', '结算', '小时')  # 副标题行
_TAB_TITLE_DAILY: str = '每日任务'

# 任务界面**一级标签行**（底部常驻，与当前选中哪个标签无关）——
# 这是「是否已进入任务界面」的稳定判据（v2.1.26）。
_TASK_PAGE_TABS: Tuple[str, ...] = ('日常任务', '翻翻乐', '成长挑战',
                                    '赛季任务', '家园目标')
_TASK_PAGE_MIN_TABS: int = 3        # 5 个里命中几个即认定「已在任务界面」


class DailyQuestTask(BaseTask):
    """每日任务一键领取 + 活跃度奖励。"""

    task_id = task_id
    label = label

    options_schema = {}

    def preflight(self) -> Tuple[bool, str]:
        """窗口就绪检查（v1.9.39 铁律：新任务必写）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    # ── 页面判据 ────────────────────────────────────────────

    def _text_set(self, items: List[Tuple]) -> set:
        """OCR 文本集合（**strip 后**）。

        v2.1.26：旧判据走 `_texts`（不 strip）+ 整条全等匹配，OCR 返回
        「每日任务 」带尾空格就判否 —— 是识别不稳的来源之一。
        """
        return {it[2].strip() for it in items
                if len(it) >= 4 and it[3] >= 0.5 and it[2].strip()}

    def _on_task_page(self, items: List[Tuple]) -> bool:
        """是否在「任务」界面（**任意一级标签**）。

        判据 = 底部一级标签行（日常任务/翻翻乐/成长挑战/赛季任务/家园目标）
        命中 ≥ `_TASK_PAGE_MIN_TABS` —— 这一行在任务界面**始终常驻**，
        与当前选中的是哪个标签无关。
        """
        texts = self._text_set(items)
        return (sum(1 for t in _TASK_PAGE_TABS if t in texts)
                >= _TASK_PAGE_MIN_TABS)

    def _on_daily_page(self, items: List[Tuple]) -> bool:
        """是否已进入「任务」界面（任意标签）——菜单导航的到达判据。

        v2.1.26（用户 18:57 真机·关键修正）：**判据从「每日任务页」放宽为
        「任务界面」**。旧判据要求标题是「每日任务」→ 若上次停在别的一级标签
        （翻翻乐/成长挑战/赛季任务/家园目标），进入任务界面后判据为否，
        而该界面又**未注册** → 全局导航判 unknown → 反复
        「未识别超限→脱离→回玩法目录」两轮后直接导航失败
        （真机 18:58:29~18:59:20：点开「任务」→ 判否 → 绕圈 → failed）。

        **页面识别不该管标签** —— 标签对不对由 `_ensure_daily_tab()` 负责切换。
        """
        return self._on_task_page(items)

    # ── 标签选中态（用户 2026-09-11 18:21 需求：两级标签检测）────────

    def _tab_selected(self, rx: float) -> Optional[bool]:
        """采样二级标签**上边框**的颜色，判断该标签是否处于选中态。

        选中态 = 橙色高亮边框（真机截图实测 R−B 峰值 187 / 未选中 19）。
        取每行 R−B 的**最大值**（边框只有 ~6px 高，取均值会被上下白底稀释）。

        Returns:
            True/False；采不到样（无截图能力/异常）→ None
        """
        try:
            img, _cl, _ct, cw, chh = self.bot.screen_image()
        except Exception as _e:
            self.log(f"[调试] _tab_selected() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return None
        if img is None or cw <= 0 or chh <= 0:
            return None
        arr = np.asarray(img)[:, :, ::-1]        # PIL RGB → BGR
        cx = int(rx * cw)
        cy = int(_TAB2_BORDER_Y * chh)
        hw, hh = _TAB_SAMPLE_HALF
        sub = arr[max(0, cy - hh):cy + hh, max(0, cx - hw):cx + hw]
        if sub.size == 0:
            return None
        b = sub[:, :, 0].astype(int)
        r = sub[:, :, 2].astype(int)
        return bool((r - b).mean(axis=1).max() >= _SELECT_RB_MIN)

    def _daily_tab_title(self, items: List[Tuple]) -> Optional[str]:
        """读「任务界面左上角标题」= 当前所在标签名（用户 18:24 指出的判据）。

        真机 8 张标签截图全量实测结论：
          · 标题在左侧**最上方**（取 min y，不是 min x）；
          · 其下方还有一行副标题「刷新时间：…」/「…后结算」，位置随标签而变
            → 按 `_TITLE_SKIP_WORDS` 排除；
          · 标题带用 ry≤0.12（0.08 会漏掉 成长挑战/家园目标 的标题）。
        标题词形：翻翻乐/成长挑战/家园目标 首字为美术字，OCR 可能只读到
        「翻乐」「成」「家」—— 只影响"显示"，不影响 == 每日任务 的判定。
        """
        best: Optional[Tuple[int, str]] = None
        for it in items:
            if len(it) < 4 or it[3] < 0.5:
                continue
            if it[0] / 1024.0 > _TITLE_REGION_RY or \
                    it[1] / 1280.0 > _TITLE_REGION_RX:
                continue
            txt: str = it[2].strip()
            if not txt or any(w in txt for w in _TITLE_SKIP_WORDS):
                continue
            if best is None or it[0] < best[0]:
                best = (it[0], txt)
        return best[1] if best else None

    def _on_daily_quest_tab(self, items: Optional[List[Tuple]] = None) -> bool:
        """是否在「日常任务 → 每日任务」标签（两级都满足）。

        主判据 = **左上角标题是「每日任务」**（用户 18:24），用 `startswith`
        容忍 OCR 尾巴（真机 8 张里「公会任务」曾被读成「公会任务0」）。

        兜底 = 取色，但**必须先确认二级标签行存在**（见下）。
        真机 8 张全量实测：本判据 8/8 正确、零误判 —— 含用户特别关心的
        「公会任务页会不会被误判成每日任务」→ ①公会任务页判 False ✓
        （该页底部同样有「每日任务」「公会任务」两行文字，旧判据必误判）。
        """
        if items is None:
            items = self.bot._ocr_screen()
        title = self._daily_tab_title(items)
        if title is not None:
            return title.startswith(_TAB_TITLE_DAILY)
        # 标题读不到 → 取色兜底。**先要求二级标签行存在**：赛季任务页在
        # 相近高度也有自己的子标签行（阶段目标/赛季成长任务/赛季阶段），
        # 且选中的那个同样是橙色边框 → 位置重叠，不确认就会把赛季页
        # 误判成"在每日任务"（真机实测 ⑤ 赛季-阶段目标：该点取色=True）。
        texts = self._text_set(items)
        if not ('每日任务' in texts and '公会任务' in texts):
            return False
        return self._tab_selected(_TAB2_DAILY_X) is True

    def _ensure_daily_tab(self) -> bool:
        """确保停在「日常任务 → 每日任务」；不在则依次点一级、二级标签。

        用户 2026-09-11 18:21/18:24 需求：
          「进入任务界面后需要检测是否在日常任务_每日任务标签，
           不在的话需要点击日常任务、再点击每日任务。」
          「界面左上角有当前所在标签标题。」
        二级标签行只在「日常任务」下出现 → 不在二级行时先点一级必然正确。
        已在目标标签 → **不做任何点击**（检测的意义所在）。

        Returns:
            bool: 操作后是否确认已在目标标签
        """
        items = self.bot._ocr_screen()
        _title = self._daily_tab_title(items)
        if self._on_daily_quest_tab(items):
            self.log(f"[信息] 已在「日常任务 → 每日任务」标签"
                     f"（左上角标题={_title}）")
            return True
        self.log(f"[信息] 当前不在「每日任务」标签"
                 f"（左上角标题={_title if _title else '未读到'}）"
                 f"→ 依次点「日常任务」→「每日任务」")
        self.bot.click_ratio(_TAB1_DAILY[0], _TAB1_DAILY[1],
                             label='每日任务_一级_日常任务')
        self._sleep(1.2)
        self.bot.click_ratio(_TAB2_DAILY[0], _TAB2_DAILY[1],
                             label='每日任务_二级_每日任务')
        self._sleep(1.2)
        _after = self._daily_tab_title(self.bot._ocr_screen())
        if _after is not None and _after.startswith(_TAB_TITLE_DAILY):
            self.log("[成功] 已切换到「日常任务 → 每日任务」标签")
            return True
        self.log(f"[警告] 切换后左上角标题仍为「{_after or '未读到'}」，"
                 "继续按每日任务流程尝试")
        return False

    # ── 启动探查 ────────────────────────────────────────────

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        # v1.9.60：先处理奖励弹窗（弹窗下标题仍可读 → 误判 ready）
        if self._dismiss_reward_popup(items, '每日任务_奖励弹窗'):
            items = self.bot._ocr_screen()
            if self._on_daily_page(items):
                self.log("[信息] 关闭奖励弹窗后在每日任务界面，直接领取")
                return 'ready'
            return 'clear'
        if self._on_daily_page(items):
            self.log("[信息] 启动即在每日任务界面，直接领取")
            return 'ready'
        return 'clear'

    # ── 主流程 ──────────────────────────────────────────────

    def run(self) -> str:
        self.log("启动（导航 + 主菜单 + 一键领取）")
        probe = self.startup_probe()
        if probe != 'ready':
            ok = self._navigate_to_daily()
            if self.check_stop():
                return self._stop_result()
            if not ok:
                self.log_nav_failed("每日任务界面")
                return 'failed'
        # v2.1.25（用户 18:21）：进入任务界面后**先确认两级标签**——
        # 任务界面有 5 个一级标签（日常任务/翻翻乐/成长挑战/赛季任务/家园目标），
        # 日常任务 下再分二级（每日任务/公会任务）；不在「日常任务→每日任务」
        # 就依次点这两级标签，否则会去领错页面的奖励。
        if self.check_stop():
            return self._stop_result()
        self._ensure_daily_tab()
        return self._claim_flow()

    def summary(self) -> str:
        return "一键领取每日任务奖励"

    # ── 导航（公共逻辑：tasks/menu_nav）──────────────────────

    def _navigate_to_daily(self) -> bool:
        """按当前所在层打开主菜单 → 「任务」（公共逻辑见 menu_nav）。

        v1.9.69：入口导航抽到 tasks/menu_nav.open_from_main_menu
        （与 friend_gift 共用），本方法只提供判据与菜单项参数。
        """
        from tasks.menu_nav import open_from_main_menu
        return open_from_main_menu(
            self,
            on_page=self._on_daily_page,
            item_tpl=_QUEST_BTN_TPL,
            item_threshold=_QUEST_BTN_THRESHOLD,
            item_kw='任务',
            label='每日任务',
        )

    # ── 活跃度奖励 ──────────────────────────────────────────

    def _claim_activity_rewards(self, items=None) -> None:
        """活跃度奖励领取（v1.9.61 建；v2.2.4 修「领不到」）。

        机制：每日任务界面**顶部进度条** 0→20→40→60→80→100，**100 档 =
        250 钻石**（最高档）。「一键领取」领的是**任务奖励**（任务给活跃度），
        活跃度满 100 后点最高档 = 领取全部活跃度奖励。

        ⚠️ v2.2.4（用户 11:54 真机）：一键领取完成后**没领到活跃度奖励**，
        而且日志里连一行提示都没有 —— 旧实现读不到「250」就**静默 return**，
        无法定位。真机截图 OCR 实测「250」@(1082,185.5) conf 0.99 / ry 0.181，
        判据本身没问题 → 失败只可能出在**取数时机或遮挡**：
          ① 一键领取的奖励弹层仍遮着顶部
          ② 进度条 / 宝箱动画未结束
        故三处加固（并全程留痕）：
          ① 先用**调用方已有的帧**查弹层并关掉（不额外拍照，保住测试帧预算）
          ② 「250」重试 `_ACT_MAX_TRIES` 次（间隔 `_ACT_TRIES_WAIT`，
             阈值 0.8 → `_ACT_MIN_CONF`）
          ③ 仍读不到时，用**活跃度数值 ≥100** 作为"该档可领"的证据，
             按真机实测比例位置补点（已领过再点无副作用）

        Args:
            items: 调用方手里已有的 OCR 帧（避免多拍一帧）；None 则现拍
        """
        for _round in range(_ACT_RETRY_MAX):   # v2.2.18：未生效则补点（上限见常量）
            if self.check_stop():
                return
            # ① 弹层可能仍遮着顶部（一键领取的奖励弹层）→ 先关掉再读数
            if self._dismiss_reward_popup(
                    items if items is not None else self.bot._ocr_screen(),
                    '每日任务_奖励弹窗'):
                self._sleep(1.2)
                items = None
                continue
            # ② 顶部找「250」：重试等动画 + 放宽阈值
            pos = None
            for _try in range(_ACT_MAX_TRIES):
                items = self.bot._ocr_screen()
                pos = next(
                    ((it[1], it[0]) for it in items
                     if len(it) >= 4 and it[3] >= _ACT_MIN_CONF
                     and it[2].strip() == '250'
                     and it[0] / 1024.0 < 0.25), None)
                if pos or self.check_stop():
                    break
                if _try < _ACT_MAX_TRIES - 1:
                    self._sleep(_ACT_TRIES_WAIT)
            if self.check_stop():
                return
            if pos is None:
                # ③ 兜底（v2.2.18，用户 09-17 两次更正）：**以可读的「100」标签
                #   为锚**推算宝箱位置 —— 该标签在"250 可见"与"250 被动画遮挡"
                #   两种状态下都稳定可读（实测 conf 0.98 ✓），而宝箱本身在遮挡态
                #   读不到 ✗。**不再用固定比例坐标**（旧 `_ACT_RATIO_Y` 取自含
                #   窗口装饰的截图，比真实客户区低约 28px ✗ → 真机点在宝箱下方
                #   的背景上，点了但没生效 ✗）。
                _tag = next(
                    ((it[1], it[0]) for it in items
                     if len(it) >= 4 and it[3] >= 0.90
                     and it[2].strip() == '100'
                     and it[0] / 1024.0 < _ACT_TAG_RY_MAX), None)
                if _tag is None:
                    self.log("[信息] 顶部「250」与「100」标签均未读到"
                             "（动画遮挡期？）→ 本轮跳过活跃度奖励")
                    return
                pos = (int(_tag[0]) + _ACT_CHEST_FROM_100_DX,
                       int(_tag[1]) + _ACT_CHEST_FROM_100_DY)
                self.log(f"[信息] 顶部未读到「250」（可能被动画遮挡）→ "
                         f"以「100」标签 {_tag} 为锚推算宝箱位置 {pos}")
            self.log(f"[点击] 活跃度奖励 250 钻石（100 档）"
                     f"（第 {_round + 1}/{_ACT_RETRY_MAX} 次） | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '每日任务_活跃度250')
            self._sleep(2.0)
            items = None
            it_r = self.bot._ocr_screen()
            if self._dismiss_reward_popup(it_r, '每日任务_奖励弹窗'):
                self._sleep(1.2)
                return   # **出现领取弹窗 = 生效** ✓（点最高档即领全部）
            # v2.2.18：**没弹窗 = 这一次没生效**（点在动画/背景上）→ 补点
            self.log(f"[警告] 点击后未出现领取弹窗（疑似未生效）→ 补点"
                     f"（{_round + 1}/{_ACT_RETRY_MAX}）")
            self._sleep(1.2)
        self.log("[警告] 活跃度奖励多次补点仍未出现领取弹窗 → 放弃本轮")
        return

    def _read_activity(self, items) -> Optional[int]:
        """读顶部活跃度数值（如 160）；读不到返回 None。

        位置：左上（rx<0.30 / ry<0.25），值须 ≥1（真机实测 (169,136.5)
        conf 0.99，rx 0.132 / ry 0.133）。仅用于「100 档是否可领」的兜底判断。
        """
        best: Optional[int] = None
        for it in items or []:
            if len(it) < 4 or it[3] < 0.80:
                continue
            if it[1] / 1280.0 >= 0.30 or it[0] / 1024.0 >= 0.25:
                continue
            t = it[2].strip()
            if t.isdigit() and 1 <= len(t) <= 4:
                v = int(t)
                if best is None or v > best:
                    best = v
        return best

    # ── 领取循环 ────────────────────────────────────────────

    def _claim_flow(self) -> str:
        """每日任务界面：「一键领取」循环至按钮消失。"""
        claimed = 0
        popup_tries = 0   # v1.9.60：弹窗关闭尝试次数
        while True:
            if self.check_stop():
                return self._stop_result()
            items = self.bot._ocr_screen()
            pos = self.bot.find_text(items, '一键领取', min_conf=0.8)
            if not pos:
                # v1.9.60：无按钮可能因奖励弹窗遮挡（启动即在弹窗）
                # → 尝试关弹窗后重扫；连续关不掉才判完成
                if claimed == 0 and popup_tries < 3:
                    if self._dismiss_reward_popup(items, '每日任务_奖励弹窗'):
                        popup_tries += 1
                        self._sleep(1.2)
                        continue
                self.log(f"[成功] 每日任务领取完毕（一键领取 {claimed} 次）")
                # v1.9.61：任务奖励已给活跃度 → 领活跃度奖励（满 100
                # 时点进度条最高档 250 钻石）
                # v2.2.4：把**当前这一帧**传进去（内含是否被弹层遮挡的信息），
                # 避免多拍一帧打乱测试帧预算
                self._claim_activity_rewards(items)
                return 'done'
            self.log(f"[点击] 「一键领取」（第 {claimed + 1} 次） | 图像={pos}")
            self.bot.click_at(pos[0], pos[1], '每日任务_一键领取')
            claimed += 1
            self._sleep(2.0)
            # 领取动画/奖励弹层 → 关闭（v1.9.57 增强：奖励弹窗
            # （如 250 钻石）无「点击空白处」字样 → OCR 弹窗中央
            # 的奖励数字 → 点击该位置关闭）
            it_r = None          # v2.2.4：留给活跃度奖励读取复用（省一帧）
            for _ in range(4):
                it_r = self.bot._ocr_screen()
                if self._dismiss_reward_popup(it_r, '每日任务_奖励弹窗'):
                    self._sleep(1.2)
                    break
                self._sleep(0.8)
            if claimed >= _CLAIM_MAX:
                self.log("[信息] 一键领取达 3 次上限，任务完成")
                self._claim_activity_rewards(it_r)
                return 'done'


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="📋",
    group="游戏任务",
    status="ready",
    options_schema=DailyQuestTask.options_schema,
    nav_target='',   # 主菜单在 0/1 层均有按钮
    oneshot=True,    # 领完即止
))
