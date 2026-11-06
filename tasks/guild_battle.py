"""结社战线（工会玩法）自动挑战。

【入口】玩法目录 → 「工会玩法」→「结社战线」
【流程】（用户 2026-10-27 指定）
  1. 扫荡：参考女神试炼的写法 —— 点「扫荡」（**全等匹配**，避免命中
     「扫荡掉落」等标签）；无次数/无可扫荡 → 跳过，直接进挑战循环
  2. 挑战循环：
     挑战 →（备战）→ 通关阵容 →（阵容弹窗）→ 一键采用 → 回备战 → 按 F
     → 战斗中 →（有「跳过」就点）→ 结算 → 再点「挑战」→ 循环…
  3. 失败处理：**原阵容重试一次**；再次失败则计数后继续下一轮
  4. 停止条件：没有挑战次数 / 轮次上限 / 时间上限 / 用户手动停止

【限制】（用户 2026-10-27 要求"挑战次数/时间限制"）
  - 本任务选项 max_battles（0=不限）、max_minutes（0=不限）
  - 同时兼容 BaseTask 的 max_turns / max_time（调度器全局设置）

【判据】全部走 **OCR 文本**（不写死坐标）；找不到关键按钮时打印候选，
        便于真机按日志微调（本文件不依赖任何新模板）。
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from tasks.base import BaseTask
from task_registry import TaskMeta, TaskRegistry

task_id = "guild_battle"
label = "结社战线"
description = "自动扫荡并循环挑战，失败重试一次"

# ── 判据 ────────────────────────────────────────────────────
_STALL_MAX: int = 12          # 未知画面连续帧兜底
# 以下判据全部来自用户实拍截图 OCR（C://Users//x9502//Pictures//Screenshots//结社战线）
#  · 主界面   ：「结社战线」(464,36) +「扫荡」(464,951) +「今日剩余：N」(463,982)
#               +「挑战」(917,961)；另有「关卡扫荡奖励（…」含"扫荡"字样
#  · 备战     ：「通关阵容」(285,972) +「战斗」(942,945) +「F」(940,971)
#  · 阵容弹窗 ：「一键采用」(694,852) +「点击空白处关闭」(639,965)
#  · 战斗     ：「跳过」(886,832) +「结社战线-第N层」(648,34)
#  · 结算     ：「战斗失败/胜利」+「**再次挑战**」(863,967)（**没有**点空白）
_MAIN_WORDS: Tuple[str, ...] = ('结社战线',)          # 主界面标题（备战/战斗也有"-第N层"）
_BATTLE_WORDS: Tuple[str, ...] = ('挑战',)            # 主界面大按钮（**全等**，避免"自动挑战"）
_SWEEP_WORD: str = '扫荡'                             # **全等**匹配（避开"关卡扫荡奖励"）
_SWEEP_LEFT_KW: str = '今日剩余'                       # 「今日剩余：0」→ 无次数，跳过扫荡
# 真机 2026-11-04 14:07：**「编队」不能作判据** ✗
#   公会玩法页的「公会争霸战」卡片下写着「派遣**编队**中」→ 被判成备战界面 ✗
#   备战界面实测只有「通关阵容」+「战斗」+「F」→ 收窄判据
_READY_WORDS: Tuple[str, ...] = ('通关阵容', '一键采用')
# 真机 2026-11-04 15:15：**结算页完全没被识别** ✗ —— 任务卡在「战斗中」空转 5 分钟
# 根因：「战斗胜利/战斗失败」是**游戏大字艺术字**，OCR 常读不出 ✗（项目老经验）
# 而底部按钮「再次挑战 / 继续挑战」是**清晰正体字** ✓ → 也作为结算判据
# ── 结算页判据（v2.3.7 双向加固）────────────────────────────────
# ① 艺术字标题（**弱信号**：游戏大字常读不出 ✗，但不能丢）
_RESULT_TITLE_WORDS: Tuple[str, ...] = ('战斗胜利', '战斗失败')
# ② 底部按钮（**强信号**：正体字，OCR 可靠 ✓）
_RESULT_BTN_WORDS: Tuple[str, ...] = ('继续挑战', '再次挑战')
# ③ 胜利页独有词（12:46 胜利截图有「推荐提升」，15:15 失败截图没有 ✓）
_WIN_ONLY_WORDS: Tuple[str, ...] = ('推荐提升',)
_RESULT_WORDS: Tuple[str, ...] = (
    _RESULT_TITLE_WORDS + _RESULT_BTN_WORDS + _WIN_ONLY_WORDS
)
# ④ **反向排除**：出现这些词说明是主界面/备战/目录页，绝不可能是结算
_NOT_RESULT_WORDS: Tuple[str, ...] = (
    '扫荡', '通关阵容', '一键采用', '玩法目录', '公会玩法', '常规玩法',
    # 挂机极限进度弹窗写着「是否确认**继续挑战**？」→ 会撞"继续挑战" ✗
    '是否确认', '极限进度',
)
# 真机 2026-11-04 12:46（用户截图 + 日志）：
#   **胜利**结算页 = 「**继续挑战**」（绿色大按钮）
#   **失败**结算页 = 「**再次挑战**」
# 旧实现只认「再次挑战」✗ → 胜利后找不到按钮、点空白也关不掉结算 →
# 同一页被重复读 9 次，记成「9 连胜」✗
_CONTINUE_WORD: str = '继续挑战'                        # 胜利结算
_RETRY_WORD: str = '再次挑战'                           # 失败结算
_SKIP_WORD: str = '跳过'                               # 战斗页文本按钮（比模板可靠）


class GuildBattleTask(BaseTask):
    """结社战线自动挑战。"""

    task_id = task_id
    label = label

    options_schema = {
        'max_battles': {
            'type': 'int',
            'label': '挑战次数上限',
            'default': 0,
            # 用户 2026-10-27 明确：**该玩法允许无限重试，就是要无限重试**
            'desc': '0 = 不限 = **无限重试**（连败也一直打，直到没次数或你手动停）',
            'note': '统计"实际开打的局数"；填 N 才封顶',
        },
        'max_minutes': {
            'type': 'int',
            'label': '时间上限(分钟)',
            'default': 0,
            'desc': '0 = 不限（一直打）；填 N 则到点收手',
        },
    }

    def __init__(self, ctx: dict):
        super().__init__(ctx)
        self.battles: int = 0          # 实际开打局数
        self.wins: int = 0
        self.losses: int = 0
        # 用户 2026-10-27 17:30 优化：该模式通关概率极低（可能上百次才过一次）
        #   · 本轮首次进备战 → 通关阵容→一键采用→F
        #   · **失败**后回备战 → **直接 F**（沿用原阵容，省两次点击）
        #   · **成功**后 → 重新走 通关阵容→一键采用
        self._need_lineup: bool = True
        self._t0: float = time.time()

    # ── 基类契约 ────────────────────────────────────────────

    def preflight(self) -> Tuple[bool, str]:
        if self.bot is None:
            return False, "bot 实例缺失"
        hwnd = self.bot.find_and_focus()
        if not hwnd:
            return False, "未找到游戏窗口（请先启动游戏）"
        self.log(f"窗口就绪 hwnd={hwnd}")
        return True, ""

    def _stop_result(self) -> str:
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def startup_probe(self) -> str:
        items = self.bot._ocr_screen()
        texts = self._texts(items)
        if self._is_main(items, texts):
            self.log("[信息] 启动即在结社战线界面，直接续跑")
            return 'ready'
        if any(w in t for w in _READY_WORDS for t in texts):
            self.log("[信息] 启动即在备战界面，直接续跑")
            return 'ready'
        return 'clear'

    def run(self) -> str:
        self.log("启动（导航 + 扫荡 + 循环挑战）")
        if self.startup_probe() != 'ready':
            if not self._navigate_in():
                self.log_nav_failed("结社战线")
                return 'failed'
        if self.check_stop():
            return self._stop_result()
        # ① 扫荡（一次；无次数则跳过）
        self._sweep()
        if self.check_stop():
            return self._stop_result()
        # ② 挑战循环
        return self._play_loop()

    def summary(self) -> str:
        return (f"结社战线：开打 {self.battles} 局"
                f"（胜 {self.wins} / 负 {self.losses}）")

    # ── 工具 ────────────────────────────────────────────────

    def _find_exact(self, items: List[Tuple], word: str,
                    min_conf: float = 0.6
                    ) -> Optional[Tuple[int, int]]:
        """**全等**匹配（避免「扫荡掉落」这类标签被当成按钮）。"""
        for it in items:
            if (len(it) >= 4 and it[3] >= min_conf
                    and str(it[2]).strip() == word):
                return (it[1], it[0])
        return None

    def _find_any(self, items: List[Tuple], words: Tuple[str, ...],
                  min_conf: float = 0.6
                  ) -> Optional[Tuple[int, int]]:
        for w in words:
            pos = self._find_exact(items, w, min_conf)
            if pos:
                return pos
        for it in items:                       # 再退一步：子串
            if (len(it) >= 4 and it[3] >= min_conf
                    and any(w in str(it[2]) for w in words)):
                return (it[1], it[0])
        return None

    def _is_result_page(self, texts: List[str]) -> bool:
        """是否是**战斗结算页**（v2.3.7：双向加固，兼顾漏判与误判）。

        **不漏判**（三路任一命中即可）：
          ① 艺术字标题「战斗胜利 / 战斗失败」（弱信号，可能读不出）
          ② 底部按钮「继续挑战 / 再次挑战」（强信号，正体字可靠）
          ③ 胜利页独有词「推荐提升」
        **不误判**：
          ④ 反向排除——含「扫荡/通关阵容/一键采用/玩法目录/公会玩法/常规玩法」
             的页面（主界面、备战、目录）**一律不算结算** ✗
        """
        _t = list(texts or [])
        if any(w in t for t in _t for w in _NOT_RESULT_WORDS):
            return False
        return any(w in t for t in _t for w in _RESULT_WORDS)

    def _is_main(self, items: List[Tuple], texts: List[str]) -> bool:
        """结社战线**主界面**：标题 +（扫荡｜挑战）按钮**同时**存在。

        ⚠️ 真机 2026-10-27 16:39 踩坑：在「公会玩法」目录页启动时，该页也
        有「结社战线」**卡片**（641,358）→ 仅凭标题会误判"已在结社战线"，
        直接走"无挑战→次数用尽→完成"✗。故必须同时看到主界面按钮。
        """
        if not any('结社战线' in t for t in texts):
            return False
        return bool(self._find_exact(items, _SWEEP_WORD, 0.7)
                    or self._find_exact(items, '挑战', 0.7))

    def _cands(self, items: List[Tuple]) -> str:
        """诊断：打印范围内候选项（找不到按钮时附带证据）。"""
        out = [f'{it[2]}({int(it[1])},{int(it[0])},{it[3]:.2f})'
               for it in items if len(it) >= 4 and it[3] >= 0.5]
        return '; '.join(out[:18]) if out else '无'

    def _over_limit(self) -> Optional[str]:
        """到达任一上限则返回原因（None = 未到）。"""
        mb = int(self.options.get('max_battles') or 0)
        if mb > 0 and self.battles >= mb:
            return f"已达挑战次数上限 {mb}"
        mm = int(self.options.get('max_minutes') or 0)
        if mm > 0 and (time.time() - self._t0) >= mm * 60:
            return f"已达时间上限 {mm} 分钟"
        if self.max_turns is not None and self.turns >= self.max_turns:
            return f"已达轮次上限 {self.max_turns}"
        if self.max_time is not None and self.elapsed >= self.max_time:
            return f"已达时间上限 {self.max_time}s"
        return None

    # ── 导航 ────────────────────────────────────────────────

    def _click_card(self, word: str, tag: str) -> bool:
        """在卡片页里滚动查找并点击（玩法目录/工会玩法通用）。"""
        prev: Optional[List[str]] = None
        for _ in range(12):
            if self.check_stop():
                return False
            items = self.bot._ocr_screen()
            texts = self._texts(items)
            pos = self._find_any(items, (word,), min_conf=0.85)
            if pos:
                self.bot.click_at(pos[0], pos[1], tag)
                self._sleep(2.5)
                return True
            if prev is not None and texts == prev:
                self.log(f"[警告] 已滚到底仍未找到「{word}」｜候选={self._cands(items)}")
                return False
            prev = texts
            self.bot.scroll_wheel(-30, rx=0.61, ry=0.36, label=f'{tag}_向下')
            self._sleep(0.3)
        self.log(f"[警告] 滚动次数用尽仍未找到「{word}」")
        return False

    def _navigate_in(self) -> bool:
        """玩法目录 →（页签）公会玩法 → 结社战线。"""
        from tasks.global_nav import GlobalNavigator
        nav = GlobalNavigator(self.bot, logger=self.log,
                              stop_check=self.check_stop)
        if not nav.navigate_to('playbook'):
            self._nav_last_screen = getattr(nav, 'last_screen', '')
            return False
        if not self._click_card('公会玩法', '玩法目录_公会玩法'):
            return False
        if not self._click_card('结社战线', '工会玩法_结社战线'):
            return False
        self.log("[成功] 已进入结社战线界面")
        return True

    # ── 扫荡（参考女神试炼：全等匹配 + 无次数即跳过）─────────

    def _sweep(self) -> None:
        # 实测（截图 主界面.png）：「扫荡」(464,951) 下方有「今日剩余：N」(463,982)
        # → N=0 直接跳过（用户口径"若无次数则跳过"），不点按钮避免多余弹窗
        items0 = self.bot._ocr_screen()
        for t in self._texts(items0):
            _t = str(t).replace('：', ':').replace(' ', '')
            if _SWEEP_LEFT_KW in _t and _t.rstrip('次').endswith(':0'):
                self.log(f"[信息] 扫荡无次数（{t}）→ 跳过扫荡")
                return
        items = items0
        for _ in range(3):
            if self.check_stop():
                return
            pos = self._find_exact(items, _SWEEP_WORD, min_conf=0.7)
            if not pos:
                self.log("[信息] 主界面无「扫荡」按钮（可能无次数）→ 跳过扫荡")
                return
            self.log(f"[点击] 「扫荡」 | 图像=({pos[0]},{pos[1]})")
            self.bot.click_at(pos[0], pos[1], '结社战线_扫荡')
            self._sleep(1.5)
            items2 = self.bot._ocr_screen()
            texts2 = self._texts(items2)
            # 常见回执：确认弹窗 / 次数不足提示 / 结算页 → 一律关掉
            if any('次数不足' in t or '无可扫荡' in t or '没有' in t
                   for t in texts2):
                self.log("[信息] 扫荡无次数（提示已出）→ 跳过扫荡")
                self.dismiss_blink_popup(items2)
                self._sleep(1.0)
                return
            if any(w in t for t in texts2 for w in _RESULT_WORDS) or \
                    any('点击空白处关闭' in t for t in texts2):
                self.dismiss_blink_popup(items2)
                self._sleep(1.2)
            self.log("[成功] 扫荡已执行")
            return
        self.log("[信息] 扫荡未确认成功，继续走挑战流程")

    # ── 备战（每局都走：通关阵容 → 一键采用 → F）────────────

    def _setup_lineup(self) -> None:
        """备战界面：通关阵容 → 一键采用（只在"需要重设阵容"时调用）。"""
        # ① 通关阵容
        pos = self._find_exact(self.bot._ocr_screen(), '通关阵容', 0.7)
        if pos:
            self.log(f"[点击] 「通关阵容」 | 图像=({pos[0]},{pos[1]})")
            self.bot.click_at(pos[0], pos[1], '备战_通关阵容')
            self._sleep(1.5)
        # ② 一键采用（阵容选择弹窗内）
        pos2 = self._find_exact(self.bot._ocr_screen(), '一键采用', 0.7)
        if pos2:
            self.log(f"[点击] 「一键采用」 | 图像=({pos2[0]},{pos2[1]})")
            self.bot.click_at(pos2[0], pos2[1], '备战_一键采用')
            self._sleep(1.5)

    def _press_f(self) -> None:
        """按 F 开战 + 统计（每局都要走）。"""
        self.log("[信息] 按 F 开始战斗")
        self.bot.press_f('结社战线_按F')
        self._sleep(2.0)
        # turns 是 BaseTask 的**只读属性**（由 stats['total_battles'] 推算）→
        # 必须递增统计值，不能直接给 turns 赋值
        try:
            self.stats['total_battles'] = int(
                self.stats.get('total_battles', 0)) + 1
        except Exception:
            pass
        self.battles += 1

    # ── 主循环（状态机）────────────────────────────────────

    def _play_loop(self) -> str:
        stall: int = 0
        in_battle: bool = False
        battle_wait: int = 0      # 战斗中无判据词的等待计数（不参与 stall 失败判定）
        counted_settle: bool = False   # 同一结算页只计一次（v2.2.34）
        while True:
            if self.check_stop():
                return self._stop_result()

            reason = self._over_limit()
            if reason:
                self.log(f"[成功] {reason} → 结束结社战线")
                return 'done'

            items = self.bot._ocr_screen()
            texts = self._texts(items)

            # ⓪' 礼包/广告弹窗可能出现在**任何界面**（含战斗中）
            #     → 不看状态，每轮先扫一眼；只认「点击屏幕退出」类 ✗（不碰结算的「点击空白处」）
            _popup = self._find_any(items, ('点击屏幕退出', '点击任意处',
                                            '点击任意位置'), min_conf=0.5)
            if _popup:
                self.log(f"[点击] 礼包弹窗「点击屏幕退出」| ({_popup[0]},{_popup[1]})")
                self.bot.click_at(_popup[0], _popup[1], '结社战线_礼包弹窗退出')
                self._sleep(1.2)
                stall = 0
                continue

            # ⓪ 战斗进行中：实测战斗页有文本「跳过」(886,832) → 点它直接结算；
            #    没有「跳过」时（按钮可能隐藏）**耐心等待**，不计入 stall
            _skip = self._find_exact(items, _SKIP_WORD, 0.7)
            if _skip:
                self.log(f"[点击] 「跳过」 | 图像=({_skip[0]},{_skip[1]})")
                self.bot.click_at(_skip[0], _skip[1], '结社战线_跳过')
                in_battle = True
                stall = 0
                self._sleep(1.5)
                continue

            # ① 战斗结算页：**胜利 → 「继续挑战」 / 失败 → 「再次挑战」**
            #    （真机 2026-11-04 12:46 用户截图：胜利页大按钮是「继续挑战」）
            if self._is_result_page(texts):
                # 胜负：三路任一命中即判胜（v2.3.7：防漏判成失败 ✗）
                #  ① 艺术字「战斗胜利」 ② 按钮「继续挑战」 ③ 胜利独有「推荐提升」
                win = (any('战斗胜利' in t for t in texts)
                       or any('继续挑战' in t for t in texts)
                       or any('推荐提升' in t for t in texts))
                # 同一结算页只计一次（真机 12:46：关不掉结算页 → 重复计 9 次 ✗）
                if not counted_settle:
                    if win:
                        self.wins += 1
                    else:
                        self.losses += 1
                    counted_settle = True
                if win:
                    self._need_lineup = True      # 成功后重新设置阵容
                else:
                    # 用户口径（2026-10-27 16:52）：**连续失败也继续循环**
                    self._need_lineup = False     # 失败 → 下一局直接 F
                    self.log("[信息] 战斗失败 → 原阵容继续下一局"
                             "（连败也继续，直到次数/时间上限或手动停止）")
                self.log(f"[结果] 战斗{'胜利' if win else '失败'}"
                         f"（累计 {self.wins}胜/{self.losses}负）")
                # 胜利优先「继续挑战」，失败优先「再次挑战」，两者互为兜底
                _primary = _CONTINUE_WORD if win else _RETRY_WORD
                _backup = _RETRY_WORD if win else _CONTINUE_WORD
                _hit_word: Optional[str] = None
                _hit_pos: Optional[Tuple[int, int]] = None
                for _w in (_primary, _backup):
                    _p = self._find_exact(items, _w, 0.7)
                    if _p:
                        _hit_word, _hit_pos = _w, _p
                        break
                if _hit_pos is not None:
                    self.log(f"[点击] 「{_hit_word}」 | 图像=({_hit_pos[0]},{_hit_pos[1]})")
                    self.bot.click_at(_hit_pos[0], _hit_pos[1],
                                      f'结社战线_{_hit_word}')
                    self._sleep(2.0)
                else:
                    self.log("[信息] 结算页无「继续挑战/再次挑战」"
                             "（次数可能用尽）→ 关闭结算")
                    self.dismiss_blink_popup(items)
                    self._sleep(1.2)
                in_battle = False
                battle_wait = 0
                stall = 0
                continue

            # 非结算页 → 允许下一局重新计数
            counted_settle = False

            # ② 备战界面：每局都走 通关阵容 → 一键采用 → F
            if any(w in t for t in texts for w in _READY_WORDS) or \
                    any('战斗' == t.strip() for t in texts):
                if in_battle:
                    # 刚按过 F 还在备战页 → 等一等（加载/动画）
                    self._sleep(1.0)
                    stall += 1
                    if stall >= _STALL_MAX:
                        self.log(f"[警告] 战斗未开始（停在备战）｜候选={self._cands(items)}")
                        return 'failed'
                    continue
                if self._need_lineup:
                    self.log("[信息] 备战界面（结社战线）→ 首次/成功后："
                             "通关阵容 → 一键采用")
                    self._setup_lineup()
                    self._need_lineup = False
                else:
                    self.log("[信息] 备战界面（结社战线）→ 失败重试："
                             "**直接按 F**（沿用原阵容）")
                self._press_f()
                in_battle = True
                stall = 0
                continue

            # ③ 结社战线主界面：点「挑战」（标题+按钮双判据，避免撞公会玩法目录）
            if self._is_main(items, texts):
                pos = self._find_any(items, _BATTLE_WORDS, min_conf=0.7)
                if pos:
                    self.log(f"[点击] 「挑战」 | 图像=({pos[0]},{pos[1]})")
                    self.bot.click_at(pos[0], pos[1], '结社战线_挑战')
                    self._sleep(2.0)
                    stall = 0
                    continue
                # 主界面但无「挑战」→ 次数用尽
                self.log("[成功] 主界面已无「挑战」按钮（次数用尽）→ 结束")
                return 'done'

            # ④ 其它弹窗（奖励/提示）→ 关掉
            # v2.3.1：礼包/广告弹窗（真机 2026-11-04 截图）→ 点「点击屏幕退出」
            # ⚠️ 只点这句文字本身，绝不点中间的「一键购买」✗
            if any('点击空白处关闭' in t or '点击屏幕退出' in t
                   or '点击任意处' in t or '点击任意位置' in t
                   or '确定' == t.strip() or '取消' == t.strip()
                   for t in texts):
                self.log("[点击] 关闭弹窗（点空白）")
                self.dismiss_blink_popup(items)
                self._sleep(1.0)
                stall = 0
                continue

            # ⑤ 未知画面
            if in_battle:
                # 战斗中（无判据词很正常）→ 耐心等待，最长 ~90s
                battle_wait += 1
                if battle_wait % 10 == 0:
                    self.log(f"[信息] 战斗中…（约 {battle_wait * 1.5:.0f}s）")
                if battle_wait >= 60:
                    self.log("[警告] 战斗超过 90s 仍未结算 → 终止")
                    return 'failed'
                self._sleep(1.5)
                continue
            stall += 1
            self.log(f"[信息] 结社战线：未知画面（等待 {stall}/{_STALL_MAX}）")
            if stall >= _STALL_MAX:
                self.log(f"[错误] 连续 {stall} 帧未知画面，终止"
                         f"｜候选={self._cands(items)}")
                return 'failed'
            self._sleep(1.2)


TaskRegistry.register(TaskMeta(
    task_id=task_id,
    label=label,
    description=description,
    default_enabled=False,
    icon="🏯",
    group="游戏任务",
    status="ready",
    options_schema=GuildBattleTask.options_schema,
    nav_target='playbook',   # 入口在玩法目录
    oneshot=True,            # 自带走次/时限选项 → 隐藏全局"时间/轮次"
))
