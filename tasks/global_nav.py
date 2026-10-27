# -*- coding: utf-8 -*-
"""全局导航系统（v1.8.31，用户命名并定案）

组成：
  ┌────────────────────────────────────────────────────────┐
  │ ① 全局界面识别：tasks/screen_registry.py（已录入全部界面）│
  │ ② 最短路径导航：本模块 GlobalNavigator.navigate_to()     │
  └────────────────────────────────────────────────────────┘
          ↓ 应用范围：**所有状态**（任务启动/玩法内部/任务间切换/
            bot core 导航）——判定统一走注册表，动作按"目标层 - 当前层"
            差值选择：差值 >0 进层（点玩法目录/点卡片），<0 脱离
            （ESC/返回按钮交替），=0 同层切换。

层级模型（数字越小越靠外）：
  0 层：世界/家园/公会        （底栏：神秘屋/玩法目录/英雄厅堂/公会）
  1 层：玩法目录/活动页       （含各玩法卡片）
  2 层：各玩法入口页          （挂机关卡页 / 异界迷宫主页 / 女神选关页）
  3 层：玩法内部              （迷宫门卡/战斗/女神备战、扫荡弹窗等）

用户定案的最短路径规则：
  - 任务完成后，回到**下一个任务入口所在层**即可，不再一律回到挂机关卡；
    下一个任务入口在玩法目录（异界迷宫/女神试炼卡片）→ 只回到玩法目录
  - 最后一个任务结束后不做任何导航
"""

from typing import Callable, Dict, List, Optional, Tuple

from tasks.base import BACK_BUTTON_TPLS, BACK_THRESHOLD
from tasks.nav_planner import plan_path  # v1.9.58 最短路径规划
from tasks.screen_registry import (
    GLOBAL_SCREEN_REGISTRY,
    zh_screen,
)

# ── 界面 → 层级（未列出的界面按 unknown 处理：靠脱离链收敛）────────
# v1.8.48 用户纠正层级模型：公会 = 1 层（主城点「公会」按钮进入，与玩法
# 目录同级）；0 层与 1 层页面都有常驻底部 UI，点按钮可直达任意 1 层页面
_SCREEN_LAYER: Dict[str, int] = {
    # -1 层（v1.8.61：登录界面，比主城更靠外；动作=点「点击开始游戏」）
    'nav.login': -1,
    # v2.1.16（用户 12:57 规范）：好友界面是「主菜单」的子页 →
    # **2 层**（各菜单的主界面）；此前误登记为 -1 层（系统弹层语义）
    'nav.friend': 2,
    # v2.1.16：1 层补充（用户规范：神秘屋/玩法目录/英雄厅堂/公会/主菜单）
    'nav.mystery_house': 1,
    'nav.hero_hall': 1,
    'nav.main_menu': 1,
    # v2.1.26：任务界面（主菜单·任务 进入的子页，2 层）
    'nav.task_page': 2,
    # v2.1.15：家园工坊内部（3 层）/ 竞技场（2 层）
    'home.workshop': 3,
    'arena.main': 2,
    'arena.select': 2,   # v2.1.3 好友界面（主菜单子页）
    # v2.0.4（用户 18:38）：加载界面（登录前/切场景）与退出确认弹窗
    'nav.loading': -1,
    'nav.quit_confirm': -3,   # 系统级弹窗：最外层，最高优先级处理

    'nav.monthly_reward': -2,  # v1.8.70：月刊弹窗（登录后随机弹出）
    # 0 层（主城）
    'nav.world_home': 0,
    'nav.world_scene': 0, 'nav.home_scene': 0,
    # 1 层（主城底部 UI 直达的页面）
    'nav.guild': 1,
    'nav.playbook': 1,
    'nav.activity': 1,
    'nav.playbook_activity': 1,   # v1.8.83：玩法目录·活动页签
    # 2 层（各玩法入口页）
    'nav.idle_stage': 2,
    'nav.labyrinth_main': 2,
    'goddess.select_page': 2,
    'labyrinth.push_map': 2,   # v1.9.11：推图副本（异域）与挂机关卡同级
    # 3 层（玩法内部）
    'labyrinth.door': 3, 'labyrinth.battle_prep': 3,
    'labyrinth.victory': 3, 'labyrinth.failure': 3,
    'labyrinth.imprint': 3, 'labyrinth.shop': 3,
    'labyrinth.boss_cleared': 3, 'labyrinth.final_settle': 3,
    # v1.9.11：补齐已注册但此前未纳入层映射的画面（判层为 None → 走
    # unknown/ESC 链）。全部属于迷宫内部（3 层）：荣誉结算、终止确认
    # 弹窗、烙印/小店/门卡三类 fallback 画面
    'labyrinth.leave_settle': 3,
    'labyrinth.end_confirm': 3,
    'labyrinth.imprint_fallback': 3,
    'labyrinth.shop_fallback': 3,
    'labyrinth.door_fallback': 3,
    'home.wishlist': 3,   # v1.9.12：一键家园·愿望单界面
    'home.res_short': 3,  # v1.9.18：资源不足弹窗（材料耗尽获取途径）
    'goddess.battle_prep': 3, 'goddess.victory_result': 3,
    'goddess.sweep_dialog': 3, 'goddess.auto_battle': 3,
}

# ── 导航目标 → (目标层, 玩法目录内入口卡片文字) ──────────────────
# 卡片文字为空 = 无需点卡片（目标层即终点）
NAV_TARGETS: Dict[str, Tuple[int, str]] = {
    'world_home': (0, ''),
    'home_scene': (0, ''),   # v1.9.48：家园场景（0 层细分）
    'world_scene': (0, ''),  # v1.9.48：世界场景（0 层细分）
    'playbook': (1, ''),
    'guild': (1, ''),
    'idle_stage': (2, '挂机关卡'),
    'labyrinth': (2, '异界迷宫'),
    'goddess_trial': (2, '女神试炼'),
}

# v1.8.48 用户定案：0 层与 1 层页面都有**常驻底部 UI**，点按钮可直达
# 任意 1 层页面（玩法目录/公会）——不需要 ESC 绕道
BOTTOM_BUTTONS: Dict[str, str] = {
    'playbook': '玩法目录',
    'guild': '公会',
}

# 脱离键：ESC 与返回按钮模板交替（v1.8.24 用户定案）
_ESC_AFTER: int = 2
_UNKNOWN_WAIT_MAX: int = 4   # 未识别画面按转场假设等待的最大帧数（容忍 ~8s 过场）


class GlobalNavigator:
    """全局最短路径导航（判定统一走全局界面注册表）。"""

    def __init__(self, bot, logger: Optional[Callable[[str], None]] = None,
                 stop_check: Optional[Callable[[], bool]] = None,
                 max_iter: int = 14, poll: float = 1.2):
        self.bot = bot
        self._log: Callable[[str], None] = logger or (lambda m: None)
        self._stop = stop_check or (lambda: False)
        self.max_iter = max_iter
        self.poll = poll
        self._escape_count: int = 0   # 脱离动作计数（奇 ESC 偶返回按钮）
        self._card: str = ''
        self._give_up: bool = False
        self._unknown_streak: int = 0   # 连续未识别计数（转场等待用）
        self.last_screen: str = 'unknown'   # 最近一次识别结果（供调用方复用）
        self._planned_from: str = ''        # v1.9.58 规划起点（偏离即重规划）
        self._plan_steps: Optional[int] = None   # 规划步数
        self.max_scrolls: int = 4           # 玩法目录滚动找卡片上限
        self._scrolls: int = 0   # 当前导航目标在玩法目录内的入口卡片文字

    # ── 对外主入口 ──────────────────────────────────────────
    # ── v2.0.4 退出确认弹窗（ESC 陷阱）最高优先级处理 ──────────
    _QUIT_DLG_KW: str = '确定要退出游戏'
    _QUIT_CANCEL_TPL: str = 'btn_confirm_cancel.png'

    def _dismiss_quit_dialog(self, items) -> bool:
        """点 X 取消退出确认弹窗（模板优先，锚点兜底）。"""
        try:
            pos = self.bot.template_match(
                self._QUIT_CANCEL_TPL, threshold=0.75, silent=True,
                scale_range=(0.8, 1.3, 0.1))
        except Exception as _e:
            self.log(f"[调试] _dismiss_quit_dialog() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            pos = None
        if pos:
            self._log(f"[点击] 退出确认弹窗「X」取消按钮 | 屏幕={pos}")
            self.bot._click_abs(*pos)
            return True
        anchor = next(((it[1], it[0]) for it in items
                       if len(it) >= 4 and it[3] >= 0.8
                       and self._QUIT_DLG_KW in it[2]), None)
        if anchor:
            x = int(anchor[0] - 0.264 * 1280)
            y = int(anchor[1] + 171)
            self._log(f"[点击] 退出确认弹窗「X」（锚点兜底） | 图像={(x, y)}")
            self.bot.click_at(x, y, '退出确认_X')
            return True
        return False
    def navigate_to(self, target: str,
                    arrived_cb: Optional[Callable[[str, List[Tuple]], bool]]
                    = None) -> bool:
        """导航到目标（'playbook'/'idle_stage'/'labyrinth'/'goddess_trial'
        /'world_home'）；已在该层即返回 True（最短路径：不做多余跳转）。

        Args:
            target: 目标 key（NAV_TARGETS）
            arrived_cb: 可选「已到达」回调 (screen_id, items) → bool，
                用于续局等特殊到达判定（如迷宫玩法内部界面即算到达）

        Returns:
            bool: True = 已到达目标层（或目标页）；False = 超时/被停止
        """
        if target not in NAV_TARGETS:
            # v1.9.11（导航统一化审查）：非法目标此前只打一条警告后静默
            # 返回 False——调用方往往忽略返回值而继续跑。改为明确错误 +
            # 列出可用目标，便于定位拼写/注册遗漏
            self._log(
                f"[错误] 未知导航目标: {target!r}——未登记在 NAV_TARGETS，"
                f"可用目标: {sorted(NAV_TARGETS)}"
            )
            return False
        # v1.9.11：目标登记完整性自检（层映射缺失会导致判层 None →
        # 全程 unknown/ESC 空转，必须提前报出而不是运行时静默失败）
        _tgt_exact = self._LAYER_EXACT_SCREEN.get(target)
        if _tgt_exact is not None and _tgt_exact not in _SCREEN_LAYER:
            self._log(
                f"[错误] 导航目标 {target!r} 的精确到达画面 "
                f"{_tgt_exact!r} 未登记在 _SCREEN_LAYER，无法判层"
            )
            return False
        tgt_layer, card = NAV_TARGETS[target]
        self._card = card
        self._log(
            f"[信息] 全局导航 → 目标「{zh_target(target)}」（{tgt_layer} 层）"
        )
        for i in range(self.max_iter):
            if self._stop():
                return False
            # v2.0.2（真机 18:26）：窗口未就绪（0 尺寸截图）→ 明确等待，
            # 不计入「未识别超限」脱链（游戏启动加载期常见）
            if getattr(self.bot, 'last_snap_empty', False):
                self._log("[信息] 窗口画面未就绪（截图尺寸 0），等待中…")
                self._sleep(5.0)
                continue
            screen, items, cur = self._probe()
            # v2.0.4（用户 18:38）：退出确认弹窗（ESC 陷阱）——最高优先级
            # 检测到即点 X 取消，再继续导航（不计轮次浪费）
            if any(len(it) >= 4 and it[3] >= 0.8
                   and '确定要退出游戏' in it[2] for it in items):
                self._dismiss_quit_dialog(items)
                continue
            # v2.0.4：加载界面 → 等待（不计未识别脱链）
            if any(len(it) >= 4 and it[3] >= 0.8
                   and '正在加载游戏' in it[2] for it in items):
                self._log("[信息] 游戏加载中，等待…")
                self._sleep(5.0)
                continue
            self._log(
                f"[信息] 全局导航 | 当前「{zh_screen(screen)}」"
                f"（{cur if cur is not None else '?'} 层）"
                f"（{i + 1}/{self.max_iter}）"
            )
            # v2.1.24（用户 17:21）：**先判「是否已到位」，再打规划日志**。
            # 两种"不用走"的情形，旧实现都先打了一条**不会执行**的路径：
            #   ① arrived_cb 命中（任务已在执行域，如迷宫选门地图）→ 直接成功返回
            #   ② 当前画面**就是该目标的入口页**（如 labyrinth 的入口页
            #      nav.labyrinth_main=异界迷宫主页）→ _arrived 当场判到达
            # 真机 17:14:20 即情形②：打出「2 步：异界迷宫主页 → 玩法目录 →
            # labyrinth」（图上绕一圈，因为「进入」那步是任务自己做的、不在图里），
            # 实际一步没走、当场判到达 → 日志与真实行为不符。
            _in_domain: bool = bool(arrived_cb is not None
                                    and arrived_cb(screen, items))
            _at_target_page: bool = (screen == _TARGET_SCREEN.get(target))
            # ── v1.9.58：最短路径规划（BFS，自动累计比较候选步数）──
            if screen != 'unknown' and screen != self._planned_from:
                self._planned_from = screen
                if _in_domain or _at_target_page:
                    # 已到位 → 实际 0 步，不打印未执行的路径
                    self._plan_steps = 0
                    _why = "已在任务执行域" if _in_domain else "已在本任务入口页"
                    self._log(f"[规划] {_why}，0 步（跳过导航）")
                else:
                    _plan = plan_path(screen, target)
                    self._plan_steps = _plan[1] if _plan else None
                    if _plan is None:
                        self._log(f"[规划] 无可达路径（{screen} → {target}），"
                                  "按贪心规则继续")
                    elif _plan[1] == 0:
                        self._log("[规划] 已在目标画面，0 步")
                    else:
                        _chain = ' → '.join(
                            [zh_screen(screen)] + [zh_screen(t)
                             for t, _, _ in _plan[0]])
                        self._log(f"[规划] 最优路径 {_plan[1]} 步：{_chain}"
                                  "（同层等价路径优先 底部直达>ESC>卡片）")
            if getattr(self, '_give_up', False):
                self._log("[警告] 全局导航终止（滚动上限）")
                return False
            if _in_domain:
                self._log(
                    f"[成功] 全局导航到达目标：{zh_target(target)}"
                    "（续局/域内，0 步）"
                )
                return True
            if self._arrived(screen, cur, tgt_layer, card, target):
                _st = getattr(self, '_plan_steps', None)
                # v2.1.24（用户 17:21 日志修正）：实际步数 = **已执行的跳转次数**。
                # 循环「一轮 = 一次探测 + 一次跳转」，在第 i 轮（0 基）判定到达时，
                # 真正走过的跳转只有 i 次（第 i 轮只做了探测，没跳）。
                # 旧写 `i + 1` 会多报一步——与本次「日志要如实」的诉求同类，
                # 例如"层已到位、一步没跳"会被写成「实际 1 步」。
                _tail = (f"（规划 {_st} 步，实际 {i} 步）"
                         if _st else "")
                self._log(f"[成功] 全局导航到达目标：{zh_target(target)}"
                          f"{_tail}")
                return True
            # v1.8.45：未识别画面优先按「转场假设」处理——点卡片后的过场
            # 动画（迷宫首进 5-8s）会被判 unknown，立即 ESC/返回按钮会把
            # 过场打断弹回玩法目录（真机 9/7：反复进出女神试炼/迷宫直到
            # 滚动耗尽）。连续 _UNKNOWN_WAIT_MAX 帧仍未识别才升级脱离
            if screen == 'unknown':
                # v1.9.2（用户定案全局规则）：转场等待期发现「点击
                # 空白处」弹层一律先点掉（结算/奖励类，点掉后重新识别）
                _bc = self.bot.find_text(
                    items, '点击空白处', min_conf=0.5)
                if _bc:
                    self._log(
                        f"[信息] 未识别画面含「点击空白处」，点掉重识")
                    self.bot.click_at(_bc[0], _bc[1], '全局导航_点空白')
                    self._sleep()
                    self._unknown_streak = 0
                    continue
                self._unknown_streak += 1
                if self._unknown_streak <= _UNKNOWN_WAIT_MAX:
                    self._log(
                        f"[信息] 未识别画面（疑似转场，等待 "
                        f"{self._unknown_streak}/{_UNKNOWN_WAIT_MAX}）"
                    )
                    self._sleep(2.0)
                    continue
                self._log("[信息] 连续未识别超限，执行脱离")
                self._escape(screen)
                continue
            self._unknown_streak = 0

            # ── v1.8.61 登录界面（-1 层）：点「点击开始游戏」进游戏 ──
            # 游戏重启/掉线后停登录页，旧逻辑判 unknown 空转脱离（真机
            # 2026-09-07 21:08）。OCR 实测判据 conf=0.99，点击后等待加载
            # ── v1.8.70 月刊弹窗（-2 层）：点「领取奖励」即关闭 ──
            if cur == -2:
                pos = self.bot.find_text(items, '领取奖励', min_conf=0.6)
                if pos:
                    self._log("[点击] 月刊弹窗「领取奖励」")
                    self.bot.click_at(pos[0], pos[1], '领取奖励')
                    self._sleep(3.0)   # 领取动画+弹窗关闭
                else:
                    self._escape(screen)   # 判据词漏检帧交替脱离重试
                continue

            if cur == -1:
                pos = self.bot.find_text(items, '点击开始游戏', min_conf=0.6)
                if pos:
                    self._log("[点击] 登录界面「点击开始游戏」（进入游戏）")
                    self.bot.click_at(pos[0], pos[1], '点击开始游戏')
                    self._sleep(4.0)   # 登录加载约 5-10s，转场等待链兑底
                else:
                    self._escape(screen)   # 判据词漏检帧交替脱离重试
                continue

            # ── v1.8.83 玩法目录·活动页签：点「常规玩法」切页签 ──
            # 真机 2026-09-08 17:58（用户定案）：此画面点「玩法目录」
            # 命中的是左上角标题（点击无效，死循环 5 次），正确动作=
            # 点「常规玩法」页签切到常规玩法 → 下一帧即 nav.playbook
            if screen == 'nav.playbook_activity':
                pos = self.bot.find_text(items, '常规玩法', min_conf=0.6)
                if pos:
                    self._log("[点击] 玩法目录·活动页签 → 「常规玩法」")
                    self.bot.click_at(pos[0], pos[1], '常规玩法')
                    self._sleep(2.0)
                else:
                    self._escape(screen)   # 判据词漏检帧交替脱离重试
                continue

            # ── v1.9.48：0 层场景互切（世界 ↔ 家园，右下角按钮）──
            if (cur == 0 and target in ('home_scene', 'world_scene')
                    and screen != 'nav.' + target):
                btn = '家园' if target == 'home_scene' else '世界'
                pos = self.bot.find_text(items, btn, min_conf=0.8)
                if pos:
                    self._log(f"[点击] 右下角「{btn}」按钮（场景切换）")
                    self.bot.click_at(pos[0], pos[1],
                                      f'全局导航_{btn}按钮')
                    self._sleep(2.0)
                    continue
                self._escape(screen)
                continue
            # ── v1.8.48 底部直达模型（用户定案）─────────────────
            # 0 层与 1 层页面都有常驻底部 UI，点按钮可直达任意 1 层页面
            if cur in (0, 1) and tgt_layer == 1:
                btn = BOTTOM_BUTTONS.get(target)
                if btn:
                    pos = self.bot.find_text(items, btn, min_conf=0.6)
                    if pos:
                        self._log(f"[点击] 底部「{btn}」（直达 1 层）")
                        self.bot.click_at(pos[0], pos[1], btn)
                        self._sleep(2.0)
                        continue
                    # 底部按钮未找到（OCR 抖动）→ 交替脱离重试
                    self._escape(screen)
                    continue
            if tgt_layer >= 2 and self._card:
                # 进入 2 层玩法页：先经底部 UI 到玩法目录，再点卡片
                if screen != 'nav.playbook':
                    pos = self.bot.find_text(
                        items, '玩法目录', min_conf=0.6
                    )
                    if pos:
                        self._log("[点击] 底部「玩法目录」（去玩法目录找卡片）")
                        self.bot.click_at(pos[0], pos[1], '玩法目录')
                        self._sleep(2.0)
                        continue
                    # 当前画面没有底部玩法目录按钮（如已在 2 层它页）→ 脱离
                    self._escape(screen)
                    continue
                # 已在玩法目录：点目标卡片（含滚动找卡，上限见 _scroll）
                pos = self.bot.find_text(items, self._card, min_conf=0.6)
                if pos:
                    self._log(f"[点击] 卡片「{self._card}」（1 层 → 2 层）")
                    self.bot.click_at(pos[0], pos[1], self._card)
                    self._sleep(2.0)
                    continue
                if not self._scroll():
                    self._give_up = True
                continue
            # 目标 0 层（world_home）：逐层 ESC 退回
            self._escape(screen)
        self._log(f"[警告] 全局导航超时（目标「{zh_target(target)}」）")
        return False

    def step(self, target: str, items: List[Tuple],
             arrived_cb: Optional[Callable[[str, List[Tuple]], bool]] = None
             ) -> bool:
        """单步导航（供任务自有循环调用，判定/动作与 navigate_to 一致）。

        Returns:
            bool: True = 本步后已到达目标；False = 未到达（本步已执行动作）
        """
        screen: str = GLOBAL_SCREEN_REGISTRY.detect(items)
        cur: Optional[int] = _SCREEN_LAYER.get(screen)
        if arrived_cb is not None and arrived_cb(screen, items):
            return True
        if target not in NAV_TARGETS:
            # v1.9.11：此前 .get(target, (1, '')) 静默退化成 1 层且无日志
            # ——非法目标会把导航带偏到玩法目录。改为明确报错并中止本步
            self._log(
                f"[错误] 导航步进遇到未登记目标: {target!r}，"
                f"可用目标: {sorted(NAV_TARGETS)}"
            )
            return False
        tgt_layer, card = NAV_TARGETS[target]
        self._card = card
        if self._arrived(screen, cur, tgt_layer, card, target):
            return True
        # v1.8.70：月刊弹窗 → 点「领取奖励」（与 navigate_to 一致）
        if cur == -2:
            pos = self.bot.find_text(items, '领取奖励', min_conf=0.6)
            if pos:
                self.bot.click_at(pos[0], pos[1], '领取奖励')
            return False
        # v1.8.61：登录界面 → 点「点击开始游戏」（与 navigate_to 一致）
        if cur == -1:
            pos = self.bot.find_text(items, '点击开始游戏', min_conf=0.6)
            if pos:
                self.bot.click_at(pos[0], pos[1], '点击开始游戏')
            return False
        # v1.8.83：玩法目录·活动页签 → 点「常规玩法」切页签
        # （与 navigate_to 一致；真机 2026-09-08 17:58 用户定案）
        if screen == 'nav.playbook_activity':
            pos = self.bot.find_text(items, '常规玩法', min_conf=0.6)
            if pos:
                self.bot.click_at(pos[0], pos[1], '常规玩法')
            else:
                self._escape(screen)
            return False
        if cur in (0, 1) and tgt_layer == 1:
            btn = BOTTOM_BUTTONS.get(target)
            if btn:
                pos = self.bot.find_text(items, btn, min_conf=0.6)
                if pos:
                    self.bot.click_at(pos[0], pos[1], btn)
                    return False
        if tgt_layer >= 2 and self._card:
            if screen != 'nav.playbook':
                pos = self.bot.find_text(items, '玩法目录', min_conf=0.6)
                if pos:
                    self.bot.click_at(pos[0], pos[1], '玩法目录')
                    return False
            pos = self.bot.find_text(items, self._card, min_conf=0.6)
            if pos:
                self.bot.click_at(pos[0], pos[1], self._card)
            return False
        self._escape(screen)
        return False

    # ── 内部：识别 / 动作 ───────────────────────────────────
    def _probe(self) -> Tuple[str, List[Tuple], Optional[int]]:
        """OCR 一帧 → (screen_id, items, 层级)"""
        items: List[Tuple] = self.bot._ocr_screen()
        screen: str = GLOBAL_SCREEN_REGISTRY.detect(items)
        self.last_screen = screen
        return screen, items, _SCREEN_LAYER.get(screen)

    # 无卡片需求目标的精确到达画面（v1.8.47：同层假到达修复——
    # 公会/世界同为 0 层，层相同 ≠ 到达，必须画面精确匹配）
    _LAYER_EXACT_SCREEN: Dict[str, str] = {
        'playbook': 'nav.playbook',
        'world_home': 'nav.world_home',
        # v1.9.48：0 层场景细分精确匹配
        'home_scene': 'nav.home_scene',
        'world_scene': 'nav.world_scene',
        # v1.9.11：公会与玩法目录/活动页签同为 1 层——此前缺精确判定，
        # 停在活动页签就会被误判"已到公会"（假到达）。补上精确画面匹配
        'guild': 'nav.guild',
    }

    def _arrived(self, screen: str, cur: Optional[int], tgt_layer: int,
                 card: str, target: str) -> bool:
        """是否已达目标：优先精确画面匹配；无精确映射时到层即算。"""
        if cur is None or cur != tgt_layer:
            return False
        if card:
            return screen == _TARGET_SCREEN.get(target, '')
        exact = self._LAYER_EXACT_SCREEN.get(target)
        if exact:
            if target == 'world_home':
                # v1.9.48：0 层细分后 world_home = 任一场景
                return screen in ('nav.world_home',
                                  'nav.world_scene',
                                  'nav.home_scene')
            return screen == exact
        return True

    def _escape(self, screen: str) -> None:
        """脱离/退层：ESC 与返回按钮**严格交替**（v1.8.40）。

        真机 9/7 教训：女神选关页是已识别画面（unknown_streak 恒 0），
        旧逻辑「unknown 连续才用返回按钮」导致 ESC 无效时死循环 14 次。
        已知画面同样可能 ESC 无响应 → 交替才通用。
        """
        self._escape_count += 1
        if self._escape_count % 2 == 0:
            self._log("[信息] 全局导航：ESC 已试，改用返回按钮模板")
            # v1.8.44：facade.click_back_button(tpl_paths) 必填模板列表——
            # 无参调用曾炸 'missing 1 required positional argument'
            if self.bot.click_back_button(
                BACK_BUTTON_TPLS, threshold=BACK_THRESHOLD
            ):
                self._sleep()
                return
            self._log("[警告] 返回按钮模板未命中，退回 ESC")
        self.bot.press_esc(label='全局导航_ESC')
        self._sleep()

    def _scroll(self) -> bool:
        """玩法目录内滚动找卡片（可滚动区中心，负格数向下）。

        Returns:
            bool: False = 已达滚动上限（调用方应终止导航）
        """
        if self._scrolls >= self.max_scrolls:
            self._log(f"[警告] 滚动找卡片已达上限 {self.max_scrolls} 次，终止")
            return False
        self._scrolls += 1
        self.bot.scroll_wheel(
            -30, rx=0.6, ry=0.36, label='全局导航_滚动'
        )
        self._sleep()
        return True

    def _sleep(self, sec: Optional[float] = None) -> None:
        import time as _t
        _t.sleep(sec if sec is not None else self.poll)


# 目标 → 目标页 screen_id（供 _arrived 判定"是否进到目标玩法页"）
_TARGET_SCREEN: Dict[str, str] = {
    'labyrinth': 'nav.labyrinth_main',
    'goddess_trial': 'goddess.select_page',
    'idle_stage': 'nav.idle_stage',
}

# ── 日志中文化映射（v1.8.32 用户要求：日志尽量不出现英文）──────
ZH_TARGET: Dict[str, str] = {
    'world_home': '世界/家园',
    'playbook': '玩法目录',
    'activity': '活动页',
    'idle_stage': '挂机关卡页',
    'labyrinth': '异界迷宫',
    'goddess_trial': '女神试炼',
}
def zh_target(target: str) -> str:
    """导航目标 → 中文名。"""
    return ZH_TARGET.get(target, target)
