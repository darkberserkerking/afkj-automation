"""
异界迷宫导航+玩法 · 干跑（dry-run）逻辑测试（v1.7.2 导航 / v1.7.12 玩法循环）

不依赖真机与游戏窗口：用 MockBot 模拟 phase 序列与 OCR 结果，
验证 `_navigate_to_labyrinth()` 状态机与 `_play_loop()` 玩法循环的
分支流转是否正确。

覆盖场景（1-12 导航，13-20 玩法循环 v1.7.12）：
  1. 直通：0层 → 点玩法目录 → 玩法目录 → 找到卡片 → 点 → 进入
  2. 滚动：前 N 屏找不到卡片，滚轮向下后找到并进入
  3. 未找到：滚满上限仍找不到 → 返回 failed
  4. 停止信号：中途 stop_event 置位 → 返回 stopped
  5. 暂停态 pause_event 未置位时，check_stop 行为（超时保护）
  6. 滚动后 phase 退化 activity 不误 ESC
  7-10. 点进入 + 弹窗绿✓处理（v1.7.10/11）
  11-12. 启动残留弹窗清理（v1.7.11）
  13. 玩法循环端到端：选门(遗物)→F开战→Boss通关→结束探索
  14. 小店不购物直接退出
  15. 烙印三选一：优先级选卡+确认（v1.8.20）
  16-17b. 失败处理：ESC×3 → 返回按钮兜底 → 仍失败判 failed
  18. 选门降级链（18d v1.8.21 改：双未命中不动作）
  19. unknown 连续超限放弃
  20. _detect_play_state 判定优先级纯逻辑

用法：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_labyrinth_nav_dryrun.py
"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.labyrinth import (            # noqa: E402
    LabyrinthTask, _CARD_TEXT, _SCROLL_CLICKS,
    # v1.8.88：_SCROLL_MAX（最多滚动次数上限）已按用户定案移除
    _UNKNOWN_GIVE_UP,
)

# ── 结果收集 ────────────────────────────────────────────────

_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


# v1.8.32：全局导航测试词表（玩法目录无卡/有卡形态，注册表计词 >4）
PB_NOCARD = ['玩法目录', '常规玩法', '活动', '女神试炼', '竞技场']
PB_CARD = PB_NOCARD + [_CARD_TEXT]

# ── Mock bot ────────────────────────────────────────────────

class MockBot:
    """按脚本返回 phase / OCR 结果，记录全部调用。"""

    def __init__(self, phases, texts_by_call=None, card_after_scrolls=0,
                 template_hit=True, back_hit=True, after_enter_texts=None,
                 fallback_texts=None):
        """
        Args:
            phases: detect_phase() 的返回序列（用完取最后一个）
            texts_by_call: 每次 _ocr_screen() 返回的文字列表序列
            card_after_scrolls: 滚几次后才出现「异界迷宫」卡片
            template_hit: v1.7.10 template_match 是否命中（False → 模拟模板未找到）
            back_hit: v1.7.12 click_back_button 是否命中（失败画面兜底用）
            after_enter_texts: 点「进入」后 texts_by_call 耗尽时的固定返回
                              （v1.7.12 玩法收尾用；模拟已进入迷宫内部画面）
            fallback_texts: texts 耗尽且非 entered 态的固定返回（v1.8.27，
                            供 unknown 超限用例注入乱码帧，防默认兜底帧
                            被 nav.playbook 识别破坏用例意图）
        """
        self._phases = list(phases)
        self._texts_by_call = list(texts_by_call or [])
        self._card_after_scrolls = card_after_scrolls
        self._template_hit = template_hit
        self._back_hit = back_hit
        self._after_enter_texts = after_enter_texts
        self._fallback_texts = list(fallback_texts) if fallback_texts else None
        self._entered = False
        self.calls: list = []
        self.scroll_count: int = 0
        self.clicked: list = []
        self.phase_calls: int = 0

    # ── 内部：取下一个 phase ──
    def detect_phase(self):
        self.phase_calls += 1
        self.calls.append('detect_phase')
        if self._phases:
            p = self._phases.pop(0)
        else:
            p = 'labyrinth'      # 序列耗尽 → 视为已到达
        return p

    # ── OCR ──
    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            texts = self._texts_by_call.pop(0)
        elif self._entered and self._after_enter_texts is not None:
            texts = list(self._after_enter_texts)
        elif self._fallback_texts is not None:
            texts = list(self._fallback_texts)   # v1.8.27 显式乱码兜底
        else:
            # 兜底：玩法目录的两个 tab 始终在（滚动不会让 tab 消失），
            # 卡片文字在滚动够次数后才出现。
            # v1.7.9 加「进入」让 _enter_labyrinth 走通（run() 会调它）
            texts = ['玩法目录', '常规玩法', '活动', '女神试炼', '进入', '继续']
            if self.scroll_count >= self._card_after_scrolls:
                texts = texts + [_CARD_TEXT]
        # 6 元组 (cy, cx, text, conf, x_min, x_max)
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, keyword, min_conf=0.5):
        self.calls.append(f'find_text:{keyword}')
        for it in items:
            text = it[2]
            if keyword in text:
                return (it[1], it[0])
        return None

    # ── 操作 ──
    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicked.append(label)
        # v1.7.12：点「进入」后画面切至迷宫内部（模拟真实流转）
        if label == '进入':
            self._entered = True

    def template_match(self, tpl_path, threshold=0.8, scale_range=(0.5, 2.0, 0.1),
                       silent=False, region=None):
        """v1.7.10 mock：记录模板名，命中返回屏幕坐标 (500,500)。"""
        name = os.path.basename(tpl_path)
        self.calls.append(f'template_match:{name}@{threshold}')
        if 'btn_skip_battle' in name:
            return None   # v1.8.72：测试场景跳过按钮默认不出现
        if self._template_hit:
            return (500, 500)
        return None

    def _click_abs(self, cx, cy):
        self.calls.append(f'_click_abs:({cx},{cy})')
        self.clicked.append(f'模板@{cx},{cy}')

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.scroll_count += 1
        self.calls.append(f'scroll_wheel:{clicks}@{rx},{ry}')

    def _handle_common_phase(self, phase):
        self.calls.append(f'common:{phase}')
        return phase      # 不改变 phase

    def find_and_focus(self):
        return 12345

    # ── v1.7.12 玩法循环新增操作 ──
    def click_ratio(self, rx, ry, label=''):
        self.calls.append(f'click_ratio:{label}@{rx},{ry}')
        self.clicked.append(label)

    def press_f(self, label=''):
        self.calls.append(f'press_f:{label}')

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append(f'click_back_button@{threshold}')
        if self._back_hit:
            return (500, 500)
        return None

    def screen_to_image(self, screen_x, screen_y):
        """v1.8.1：模板屏幕坐标 → 图坐标（mock 恒等，简化断言）"""
        self.calls.append(f'screen_to_image:({screen_x},{screen_y})')
        return (screen_x, screen_y)


def _make_task(mock_bot, stop=False, options=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()                 # 未暂停
    if stop:
        stop_ev.set()
    ctx = {
        'bot': mock_bot,
        'logger': lambda m: None,
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': options or {},  # v1.8.9：支持传 deep_challenge 等任务参数
        'max_time': None,
        
        'inst_id': 'labyrinth#dry',
    }
    return LabyrinthTask(ctx)


# ═══════════════════════════════════════════════════════════
#  1. 直通：0 层 → 玩法目录 → 找到卡片 → 进入
# ═══════════════════════════════════════════════════════════
print('\n── 1. 直通导航 ──')
bot1 = MockBot(phases=[],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],   # probe：迷宫主页
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # 全局导航：已在目标层 → 到达
        ['进入'],                        # _enter_labyrinth 点「进入」
        [],                              # 弹窗探测：无
        # ── 玩法循环收尾 ──
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
)
t1 = _make_task(bot1)
r1 = t1._run_once()
check('迷宫主页直通返回 done（最短路径）', r1 == 'done', f'result={r1}')
check('点了「进入」', '进入' in bot1.clicked, str(bot1.clicked))
check('未点玩法目录/卡片/滚动（最短路径无多余动作）',
      '玩法目录' not in bot1.clicked and _CARD_TEXT not in bot1.clicked
      and bot1.scroll_count == 0, str(bot1.clicked))

# ═══════════════════════════════════════════════════════════
#  2. 滚动：前 2 屏无卡片，第 3 屏出现
# ═══════════════════════════════════════════════════════════
print('\n── 2. 滚动找卡片 ──')
bot2 = MockBot(phases=[],
    texts_by_call=[
        PB_NOCARD,                       # probe：玩法目录（无卡片）
        PB_NOCARD,                       # 全局导航：1层 → 滚动 1
        PB_NOCARD,                       # 滚动 2
        PB_CARD,                         # 卡片出现 → 点击 → 到达迷宫主页
        ['难度15', '探索阵容(12/12)'],   # 全局导航：到达
        ['进入'],                        # 点「进入」
        [],                              # 弹窗探测
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
)
t2 = _make_task(bot2)
r2 = t2._run_once()
check('滚动找卡后导航返回 done', r2 == 'done', f'result={r2}')
check('滚动了 2 次', bot2.scroll_count == 2, f'scroll={bot2.scroll_count}')
check('滚动传了负格数（向下）',
      any('scroll_wheel:-30' in c for c in bot2.calls),
      str([c for c in bot2.calls if 'scroll' in c]))

# ═══════════════════════════════════════════════════════════
#  3. 滚满上限仍找不到 → failed
# ═══════════════════════════════════════════════════════════
print('\n── 3. 卡片不存在 ──')
bot3 = MockBot(phases=[],
    texts_by_call=[PB_NOCARD] * 8,   # 永远无卡片
)
t3 = _make_task(bot3)
r3 = t3._run_once()
check('找不到卡片返回 failed', r3 == 'failed', f'result={r3}')
check('滚动恰好 4 次后终止（上限）',
      bot3.scroll_count == 4, f'scroll={bot3.scroll_count}')

# ═══════════════════════════════════════════════════════════
#  4. 停止信号 → stopped
# ═══════════════════════════════════════════════════════════
print('\n── 4. 停止信号 ──')
bot4 = MockBot(phases=['home'])
t4 = _make_task(bot4, stop=True)
r4 = t4._run_once()
check('停止信号返回 stopped', r4 == 'stopped', f'result={r4}')
check('停止后不再点击', len(bot4.clicked) == 0, f'clicked={bot4.clicked}')

# ═══════════════════════════════════════════════════════════
#  5. 玩法目录判定：滚动后仍成立（不依赖"挂机关卡"条目）
# ═══════════════════════════════════════════════════════════
print('\n── 5. 玩法目录判定 ──')
bot5 = MockBot(phases=['unknown'])
t5 = _make_task(bot5)
bot5._texts_by_call = [['玩法目录', '常规玩法', '活动', '沙盘演兵']]  # 无"挂机关卡"
check('滚动后（无挂机关卡条目）仍判定为玩法目录',
      t5._is_on_playbook() is True)

bot5b = MockBot(phases=['unknown'])
t5b = _make_task(bot5b)
bot5b._texts_by_call = [['神秘屋', '英雄厅堂', '公会']]   # 0 层底部菜单（无玩法目录特征）
check('0 层底部菜单不误判为玩法目录',
      t5b._is_on_playbook() is False)

# ═══════════════════════════════════════════════════════════
#  6. 回归：滚动后 detector 退化成 'activity' 时不得误 ESC
#     （"挂机关卡"条目滚出屏幕 → detect_phase 判成 activity，
#       主循环若照 phase 行事就会发 ESC 退出玩法目录）
# ═══════════════════════════════════════════════════════════
print('\n── 6. 滚动后 phase 退化为 activity ──')
bot6 = MockBot(phases=[],
    texts_by_call=[
        PB_NOCARD,                       # probe
        PB_NOCARD,                       # 滚动 1
        PB_CARD,                         # 卡片出现 → 点击 → 到达
        ['难度15', '探索阵容(12/12)'],   # 到达
        ['进入'], [],
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
)
t6 = _make_task(bot6)
r6 = t6._run_once()
check('玩法目录滚动找卡导航成功', r6 == 'done', f'result={r6}')
check('未误发 ESC（进层动作不含 ESC）',
      not any('press_esc' in c for c in bot6.calls),
      str([c for c in bot6.calls if 'esc' in c.lower()]))
check('确实滚动找了卡片', bot6.scroll_count == 1, f'scroll={bot6.scroll_count}')


# ═══════════════════════════════════════════════════════════
#  7. _enter_labyrinth：弹窗存在 → 模板匹配绿 ✓（v1.7.10）
#     v1.7.9 用 OCR 找「继续」误命中弹窗文案 → v1.7.10 改模板匹配
#     btn_confirm_check.png。mock 技巧：phases=['labyrinth'] 让
#     _navigate_to_labyrinth 第 1 轮就 return True（不进状态机），
#     texts_by_call 全部用于 _enter_labyrinth 段
# ═══════════════════════════════════════════════════════════
print('\n── 7. _enter_labyrinth：弹窗 → 模板匹配绿 ✓ ──')
bot7 = MockBot(
    phases=['labyrinth'],  # 第 1 轮就 return True
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],                        # v1.7.11 阶段0：启动弹窗检查（无弹窗）
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['进入'],                                              # _click_text('进入') 1st
        ['是否继续探索', '探索阵容尚有空缺位置'],                 # 弹窗探测（命中特征）
        # ── v1.7.12：run() 阶段3 进入玩法循环，补收尾序列 ──
        ['难度15', '结束探索', '遗物之门'],          # door → 点遗物之门
        ['难度15', '4/15', '战斗', 'F'],                                # battle_prep → F
        ['难度通关', '结束探索'],                    # 战斗等待：boss_cleared
        ['难度通关', '结束探索'],                    # 主循环：boss_cleared
        ['难度通关', '结束探索'],                    # _click_text('结束探索')
        [],                                          # 确认弹窗探测：无
    ],
    card_after_scrolls=0,
)
t7 = _make_task(bot7)
r7 = t7._run_once()
check('弹窗模板确认后 run() 返回 done', r7 == 'done', f'result={r7}')
check('点击过「进入」', '进入' in bot7.clicked, str(bot7.clicked))
check('调用了绿✓模板匹配 btn_confirm_check',
      any('template_match:btn_confirm_check.png' in c for c in bot7.calls),
      str([c for c in bot7.calls if 'template' in c]))
check('点击过模板坐标（绿✓被点中）',
      any(c.startswith('_click_abs') for c in bot7.calls),
      str([c for c in bot7.calls if '_click_abs' in c]))
check('未再 OCR 找「继续」文字（不再点文案）',
      not any('find_text:继续' in c for c in bot7.calls),
      str([c for c in bot7.calls if 'find_text' in c]))


# ═══════════════════════════════════════════════════════════
#  8. _enter_labyrinth：未弹窗（阵容已满 12/12）→ 直接进（v1.7.9）
# ═══════════════════════════════════════════════════════════
print('\n── 8. _enter_labyrinth：未弹窗 ──')
bot8 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],                        # v1.7.11 阶段0：启动弹窗检查（无弹窗）
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['进入'],                       # _click_text('进入') 1st
        # 弹窗探测：没有"是否继续探索"等关键字（阵容已满 12/12）
        ['难度15', '进入', '探索阵容(12/12)'],
        # ── v1.7.12：玩法循环收尾序列 ──
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
    card_after_scrolls=0,
)
t8 = _make_task(bot8)
r8 = t8._run_once()
check('未弹窗时 run() 仍返回 done', r8 == 'done', f'result={r8}')
check('点击过「进入」', '进入' in bot8.clicked, str(bot8.clicked))
check('未点击「继续」（无弹窗）',
      '继续' not in bot8.clicked, str(bot8.clicked))


# ═══════════════════════════════════════════════════════════
#  9. _enter_labyrinth：找「进入」失败 → run() failed（v1.7.9）
#     texts_by_call 故意不含"进入"，让 _click_text('进入') 8s 超时失败
#     缩短 _TEXT_TIMEOUT 加速
# ═══════════════════════════════════════════════════════════
print('\n── 9. _enter_labyrinth：未找到「进入」 ──')
bot9 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        # v1.7.11 阶段0：启动弹窗检查（无弹窗）
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        # 故意不含"进入"——让 _click_text 失败
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
    ],
    card_after_scrolls=0,
)
t9 = _make_task(bot9)
import tasks.labyrinth as _lab
_orig_timeout = _lab._TEXT_TIMEOUT
_lab._TEXT_TIMEOUT = 0.5
try:
    r9 = t9._run_once()
finally:
    _lab._TEXT_TIMEOUT = _orig_timeout
check('未找到「进入」时 run() 返回 failed', r9 == 'failed', f'result={r9}')

# ═══════════════════════════════════════════════════════════
#  10. _enter_labyrinth：弹窗存在但绿 ✓ 模板未命中 → run() failed（v1.7.10）
#      template_hit=False：_click_template 重试 _TPL_RETRIES 次均失败
# ═══════════════════════════════════════════════════════════
print('\n── 10. _enter_labyrinth：弹窗存在但模板未命中 ──')
bot10 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],                        # v1.7.11 阶段0：启动弹窗检查（无弹窗）
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['进入'],                                              # _click_text('进入') 1st
        ['是否继续探索', '探索阵容尚有空缺位置'],                 # 弹窗探测（命中特征）
    ],
    card_after_scrolls=0,
    template_hit=False,      # 模板匹配永不命中
)
t10 = _make_task(bot10)
r10 = t10._run_once()
check('模板未命中时 run() 返回 failed', r10 == 'failed', f'result={r10}')
check('点击过「进入」', '进入' in bot10.clicked, str(bot10.clicked))
check('未点击模板（匹配失败不点）',
      not any(c.startswith('_click_abs') for c in bot10.calls),
      str([c for c in bot10.calls if '_click_abs' in c]))
check('绿✓模板重试了多次（_TPL_RETRIES 次）',
      sum(1 for c in bot10.calls if 'template_match:btn_confirm_check' in c) >= 2,
      str([c for c in bot10.calls if 'template' in c]))

# ═══════════════════════════════════════════════════════════
#  11. run() 启动阶段0：残留「是否继续探索」弹窗清理（v1.7.11）
#      真机 bug：上轮测试停在弹窗状态 → 重启后 detector 不识别该弹窗
#      → unknown → ESC 空转 14s。v1.7.11 在导航前先探测并点绿✓关掉。
# ═══════════════════════════════════════════════════════════
print('\n── 11. 启动残留弹窗清理 ──')
bot11 = MockBot(
    phases=['labyrinth'],  # 第 1 轮就 return True
    texts_by_call=[
        # 阶段0 启动检查：命中弹窗特征 → 模板匹配绿✓关闭（点掉后不再残留）
        ['是否继续探索', '探索阵容尚有空缺位置'],
        # _enter_labyrinth：点「进入」
        ['难度15', '进入', '探索阵容(5/12)'],
        # 点进入后探测：已无弹窗
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        # ── v1.7.12：玩法循环收尾序列 ──
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
    card_after_scrolls=0,
)
t11 = _make_task(bot11)
r11 = t11._run_once()
check('启动残留弹窗被清理后 run() 返回 done', r11 == 'done', f'result={r11}')
check('第一步先点绿✓关弹窗（启动即处理，无 ESC 空转）',
      bot11.clicked and bot11.clicked[0] == '模板@500,500',
      str(bot11.clicked))
check('绿✓后正常点「进入」', '进入' in bot11.clicked, str(bot11.clicked))
check('全程未按 ESC（不再空转）',
      not any('press_esc' in c for c in bot11.calls),
      str([c for c in bot11.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
print('\n── 12. 启动干净页：无残留弹窗直通 ──')
bot12 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],      # 阶段0 启动检查：无弹窗 → 直接进导航
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '进入', '探索阵容(5/12)'],  # _click_text('进入')
        ['难度15', '探索阵容(5/12)'],      # 点进入后探测：无弹窗
        # ── v1.7.12：玩法循环收尾序列 ──
        ['难度15', '结束探索', '遗物之门'],
        ['难度15', '4/15', '战斗', 'F'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
    card_after_scrolls=0,
)
t12 = _make_task(bot12)
r12 = t12._run_once()
check('干净启动 run() 返回 done', r12 == 'done', f'result={r12}')
check('未点绿✓模板（无弹窗不误点）',
      not any(c.startswith('_click_abs') for c in bot12.calls),
      str([c for c in bot12.calls if '_click_abs' in c]))
check('未按 ESC（无空转）',
      not any('press_esc' in c for c in bot12.calls),
      str([c for c in bot12.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
#  13. 玩法循环端到端：进入 → 选门(遗物) → F开战 → Boss通关 → 结束探索
#      v1.7.12：run() 阶段3 = _play_loop
# ═══════════════════════════════════════════════════════════
print('\n── 13. 玩法循环端到端（选门→F开战→15层通关）──')
bot13 = MockBot(
    phases=['labyrinth'],   # 导航第 1 轮即到达
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],              # 阶段0 启动检查：无弹窗
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['进入'],                                    # _click_text('进入')
        ['难度15', '探索阵容(12/12)'],              # 点进入后探测：无弹窗
        ['难度15', '结束探索', '遗物之门'],          # door → 按优先级点遗物之门
        ['难度15', '4/15', '战斗', 'F'],                                # battle_prep → 按 F
        ['难度通关', '结束探索'],                    # 战斗等待轮询：boss_cleared
        ['难度通关', '结束探索'],                    # 主循环：boss_cleared
        ['难度通关', '结束探索'],                    # _click_text('结束探索')
        [],                                          # 确认弹窗探测：无
    ],
    card_after_scrolls=0,
)
t13 = _make_task(bot13)
r13 = t13._run_once()
check('端到端玩法循环 run() 返回 done', r13 == 'done', f'result={r13}')
check('按优先级点了「遗物之门」按钮（v1.8.1：标题选门+按钮点击）',
      '门卡_遗物之门_按钮' in bot13.clicked,
      str(bot13.clicked))
check('组合点击：标题x + 按钮y 都从 bot 接口取（v1.8.1）',
      any('screen_to_image:(500,500)' in c for c in bot13.calls),
      str([c for c in bot13.calls if 'screen_to_image' in c]))
check('按了 F 键开战', any(c.startswith('press_f') for c in bot13.calls),
      str([c for c in bot13.calls if 'press_f' in c]))
check('点了「结束探索」', '结束探索' in bot13.clicked, str(bot13.clicked))

# ═══════════════════════════════════════════════════════════
#  14. 小店：不购物，点「继续前进」退出（用户定案）
# ═══════════════════════════════════════════════════════════
print('\n── 14. 玩法循环：菲茨小店直接退出 ──')
bot14 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        ['商品列表', '菲茨小店', '继续前进'],       # shop → 点继续前进
        ['商品列表', '菲茨小店', '继续前进'],       # _click_text('继续前进') 自行 OCR：命中点击
        ['难度通关', '结束探索'],                    # boss_cleared
        ['难度通关', '结束探索'],                    # _click_text('结束探索')
        [],
    ],
    card_after_scrolls=0,
)
t14 = _make_task(bot14)
r14 = t14._run_once()
check('小店场景 run() 返回 done', r14 == 'done', f'result={r14}')
check('点了「继续前进」（不购物直接退出）', '继续前进' in bot14.clicked,
      str(bot14.clicked))

# ═══════════════════════════════════════════════════════════
#  15. 烙印：点第一个稀有度标签所在卡（OCR 实时坐标）+ 确认按钮（v1.8.10 用户定案）
# ═══════════════════════════════════════════════════════════
print('\n── 15. 玩法循环：烙印点首个稀有度卡 ──')
bot15 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        # v2.2.10：弹层帧带上可读卡名（真实帧如此；'烙印·XX' 含「烙印」会被排除）
        ['选择烙印', '稀有', '藏品柜', '烙印·狂怒', '烙印·坚壁'],  # imprint → 读名+点稀有度标签
        ['选择烙印', '确认'],                          # 点卡后 OCR → 找确认钮
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
        [],
    ],
    card_after_scrolls=0,
)
t15 = _make_task(bot15)
r15 = t15._run_once()
check('烙印场景 run() 返回 done', r15 == 'done', f'result={r15}')
check('点了第一个稀有度标签所在卡（v1.8.10 OCR 实时坐标）',
      'click_at:烙印_选卡' in bot15.calls,
      str([c for c in bot15.calls if 'click_at' in c]))
check('点了确认钮「确认」', '烙印_确认' in bot15.clicked, str(bot15.clicked))

# ═══════════════════════════════════════════════════════════
#  16. 失败处理：第 1 次 ESC 即退出失败画面 → 结束探索 → done
# ═══════════════════════════════════════════════════════════
print('\n── 16. 玩法循环：失败 ESC 一次退出 ──')
bot16 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        ['战斗失败'],                                 # failure → ESC
        ['难度15', '结束探索'],                       # ESC 后复查：已退出
        ['难度15', '结束探索'],                       # _click_text('结束探索')
        [],
    ],
    card_after_scrolls=0,
)
t16 = _make_task(bot16)
r16 = t16._run_once()
check('失败场景 run() 返回 done', r16 == 'done', f'result={r16}')
check('只按了 1 次 ESC（首次即退出）',
      sum(1 for c in bot16.calls if 'press_esc' in c) == 1,
      str([c for c in bot16.calls if 'esc' in c.lower()]))
check('未动用返回按钮兜底',
      not any('click_back_button' in c for c in bot16.calls))
check('失败后点了「结束探索」', '结束探索' in bot16.clicked)

# ═══════════════════════════════════════════════════════════
#  17. 失败处理：ESC×3 画面不动 → 返回按钮模板兜底（用户定案）
# ═══════════════════════════════════════════════════════════
print('\n── 17. 玩法循环：失败 ESC×3 + 返回按钮兜底 ──')
bot17 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        ['战斗失败'],                                 # failure → ESC 循环
        ['战斗失败'],                                 # ESC1 复查：仍在
        ['战斗失败'],                                 # ESC2 复查：仍在
        ['战斗失败'],                                 # ESC3 复查：仍在
        ['难度15', '结束探索'],                       # 返回按钮后：已退出
        ['难度15', '结束探索'],                       # _click_text
        [],
    ],
    card_after_scrolls=0,
)
t17 = _make_task(bot17)
r17 = t17._run_once()
check('ESC×3 兜底场景 run() 返回 done', r17 == 'done', f'result={r17}')
# v1.8.88：移除 ESC 次数上限 → ESC 与返回按钮**同轮交替**直到退出
# （实测 ESC×2 + 返回按钮×2 即退出，比旧「先 ESC×3 再兜底」更快）
check('ESC 与返回按钮交替直到退出（无次数上限）',
      sum(1 for c in bot17.calls if 'press_esc' in c) >= 1
      and any('click_back_button' in c for c in bot17.calls),
      str([c for c in bot17.calls if 'esc' in c.lower()]))
check('ESC 失效后调用了返回按钮兜底',
      any('click_back_button' in c for c in bot17.calls),
      str([c for c in bot17.calls if 'back' in c]))
check('失败后点了「结束探索」', '结束探索' in bot17.clicked)

# ═══════════════════════════════════════════════════════════
#  17b. 失败处理：ESC×3 + 返回按钮也未命中 → failed
# ═══════════════════════════════════════════════════════════
print('\n── 17b. 玩法循环：失败画面完全退不出 ──')
bot17b = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        ['战斗失败'],
        ['战斗失败'],
        ['战斗失败'],
        ['战斗失败'],                                 # 返回按钮后仍在（兜底也失败）
    ],
    card_after_scrolls=0,
    back_hit=False,     # 返回按钮模板未命中
)
t17b = _make_task(bot17b)
r17b = t17b._run_once()
check('失败画面退不出时 run() 返回 failed', r17b == 'failed', f'result={r17b}')
check('尝试过返回按钮兜底',
      any('click_back_button' in c for c in bot17b.calls))

# ═══════════════════════════════════════════════════════════
#  18. 选门降级链（v1.8.1：标题选门 + 按钮点击）
#      18a 标题命中 + 按钮命中 → 组合点击（标题x, 按钮y）
#      18b 标题未识别 + 按钮命中 → 单门场景，直接点按钮（精英/BOSS）
#      18c 标题命中 + 按钮未命中 → 回退点标题（按钮图案未采样）
#      18d 双未命中 → 不动作（v1.8.21 删除固定坐标中央兜底）
# ═══════════════════════════════════════════════════════════
print('\n── 18. 选门降级链（v1.8.1） ──')

# 18a：标题+按钮都命中（template_hit=True 默认）→ 组合点击
t18a = _make_task(MockBot(phases=['labyrinth']))
items18a = [(100, 200, '遗物之门', 0.9, 150, 250),
            (100, 200, '难度15', 0.9, 150, 250)]
t18a._pick_door(items18a)
check('18a 组合点击：click_at(标题x, 按钮y)',
      any(c.startswith('click_at:门卡_遗物之门_按钮') for c in t18a.bot.calls),
      str(t18a.bot.calls))
check('18a 调过 screen_to_image 做坐标换算',
      any('screen_to_image' in c for c in t18a.bot.calls))

# 18b：标题未识别 + 按钮命中 → 单门场景点按钮
bot18b = MockBot(phases=['labyrinth'])
t18b = _make_task(bot18b)
items18b = [(100, 200, '神秘生物★★★', 0.9, 150, 250),
            (100, 200, '难度15', 0.9, 150, 250)]
# v1.8.85：标题未命中先等一帧重试（转场期 OCR 常漏标题），连续第 2 帧
# 仍无标题才按按钮盲点——避免单帧漏检就盲点（真机 19:35/38/44 多次）
t18b._pick_door(items18b)
check('18b0 首帧标题未命中 → 不盲点按钮（等一帧重试）',
      not any(c.startswith('_click_abs') for c in bot18b.calls),
      str([c for c in bot18b.calls if '_click_abs' in c]))
t18b._pick_door(items18b)
check('18b 单门场景：连续两帧无标题 → 点命中按钮（_click_abs）',
      any(c.startswith('_click_abs:(500,500)') for c in bot18b.calls),
      str([c for c in bot18b.calls if '_click_abs' in c]))
check('18b 未走 click_ratio 中央兜底',
      not any('click_ratio:门卡_中央兜底' in c for c in bot18b.calls))

# 18c：标题命中 + 按钮未命中（template_hit=False）→ 回退点标题
bot18c = MockBot(phases=['labyrinth'], template_hit=False)
t18c = _make_task(bot18c)
items18c = [(100, 200, '遗物之门', 0.9, 150, 250)]
t18c._pick_door(items18c)
check('18c 按钮未命中：回退点标题（门卡_遗物之门 旧 label）',
      any(c.startswith('click_at:门卡_遗物之门') for c in bot18c.calls),
      str([c for c in bot18c.calls if 'click' in c]))

# 18d：双未命中 → 不动作（v1.8.21 删除固定坐标中央兜底，交主循环重试）
bot18d = MockBot(phases=['labyrinth'], template_hit=False)
t18d = _make_task(bot18d)
items18d = [(100, 200, '神秘生物★★★', 0.9, 150, 250)]
t18d._pick_door(items18d)
check('18d 双未命中：不点击（无任何 click_ratio/click_at/_click_abs）',
      not any(c.startswith(('click_ratio', 'click_at', '_click_abs'))
              for c in bot18d.calls),
      str([c for c in bot18d.calls if 'click' in c]))
check('18d 双未命中：打印不动作日志',
      True)   # 日志走 _logger mock（lambda 丢弃），行为由上条无点击断言保证

# ═══════════════════════════════════════════════════════════
#  19. unknown 连续超限 → failed（转场卡死保护；v1.8.12 降级链：
#      超限先试模板返回键，无效才 failed）
# ═══════════════════════════════════════════════════════════
print('\n── 19. 玩法循环：unknown 连续超限放弃 ──')
bot19 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        [],   # 之后全是空 OCR → unknown 连续
    ],
    card_after_scrolls=0,
    back_hit=False,   # v1.8.12：返回键也无效 → 才允许 failed
    # v1.8.27：显式乱码兜底——默认兜底帧（玩法目录形态）会被 nav.playbook
    # 识别为"已离开执行域"优雅结束，破坏本用例"超限 failed"的意图
    fallback_texts=[['乱码画面']],
)
t19 = _make_task(bot19)
r19 = t19._run_once()
check('unknown 超限且返回键无效 run() 返回 failed', r19 == 'failed',
      f'result={r19}')
check('超限前先 ESC 重试过（第 4 轮起）',
      0 < sum(1 for c in bot19.calls if 'press_esc' in c) < _UNKNOWN_GIVE_UP,
      str([c for c in bot19.calls if 'esc' in c.lower()]))
check('v1.8.12 降级链：超限时尝试过模板返回键',
      any('click_back_button' in c for c in bot19.calls),
      str([c for c in bot19.calls if 'back' in c]))

# 19b：v1.8.12 降级链——超限时返回键命中 → 重置计数继续（不 failed）
bot19b = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        [], [], [], [], [], [], [], [], [], [],   # unknown ×10 → 超限
        ['难度通关', '结束探索'],   # 返回键命中重置后 → boss_cleared → 结束
        ['难度通关', '结束探索'],   # _click_text 内部 OCR
    ],
    card_after_scrolls=0,
)
t19b = _make_task(bot19b)
r19b = t19b._run_once()
check('19b unknown 超限但返回键命中 → 不 failed', r19b == 'done',
      f'result={r19b}')
check('19b 返回键被调用', any('click_back_button' in c for c in bot19b.calls),
      str([c for c in bot19b.calls if 'back' in c]))

# 19c：v1.8.12 全局闪烁弹窗——unknown 帧含「点击空白处继续」→ 点掉重置，不 ESC
bot19c = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧（迷宫主页 → 到达）
        ['进入'],
        ['难度15', '探索阵容(12/12)'],
        ['附加挑战', '挑战奖励', '点击空白处继续(7秒)'],  # 闪烁弹窗 → 点掉
        ['难度通关', '结束探索'],
        ['难度通关', '结束探索'],
    ],
    card_after_scrolls=0,
)
t19c = _make_task(bot19c)
r19c = t19c._run_once()
check('19c 闪烁弹窗帧 run() 返回 done', r19c == 'done', f'result={r19c}')
check('19c 弹窗被点击（点文字位置）', '闪烁弹窗_点空白' in bot19c.clicked,
      str(bot19c.clicked))
check('19c 弹窗未触发 ESC', not any('press_esc' in c for c in bot19c.calls),
      str([c for c in bot19c.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
#  20. _detect_play_state 纯逻辑：判定优先级（不动 bot）
# ═══════════════════════════════════════════════════════════
print('\n── 20. _detect_play_state 判定优先级 ──')
t20 = _make_task(MockBot(phases=['labyrinth']))


def _items(*texts):
    return [(100, 200, t, 0.9, 150, 250) for t in texts]


check('难度通关最优先（含战斗胜利/结束探索也判 boss_cleared）',
      t20._detect_play_state(_items('难度通关', '战斗胜利', '结束探索'))
      == 'boss_cleared')
check('战斗胜利 → victory', t20._detect_play_state(
    _items('战斗胜利', '获得奖励')) == 'victory')
check('战斗失败 → failure', t20._detect_play_state(
    _items('战斗失败', '再试一次')) == 'failure')
check('选择烙印 → imprint', t20._detect_play_state(
    _items('选择烙印', '烙印·狂怒')) == 'imprint')
check('商品列表 → shop', t20._detect_play_state(
    _items('商品列表', '继续前进')) == 'shop')
check('战斗+独立F+「难度」+/15 → battle_prep（v1.8.8 双重正向硬约束）',
      t20._detect_play_state(_items('难度15', '4/15', '战斗', 'F', '出战阵容')) == 'battle_prep')
check('只有「战斗」无独立F → 不判 battle_prep（防烙印描述误判）',
      t20._detect_play_state(_items('战斗后获得烙印')) == 'unknown')
check('仅结束探索 → door', t20._detect_play_state(
    _items('难度15', '结束探索')) == 'door')
check('空文本 → unknown', t20._detect_play_state([]) == 'unknown')

# ═══════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════
#  21. v1.8.3 续局：点「异界迷宫」卡片后直接进「继续进度」界面
#      （detector 恒 unknown：无「异界迷宫」标题、无「探索阵容」）
#      断言：导航成功、_resumed=True、未点「进入」、直接进入玩法循环
# ═══════════════════════════════════════════════════════════
print('\n── 21. 续局：点卡片直达「继续进度」界面 ──')
bot21 = MockBot(
    phases=[],
    # v1.8.32：续局新语义——0 层 → 玩法目录点卡片 → 直进玩法内部
    # （labyrinth.* 域画面由 arrived_cb 判定到达，_resumed=True）
    texts_by_call=[
        ['神秘屋', '玩法目录', '英雄厅堂', '公会'],      # probe：0 层
        ['玩法目录', '常规玩法', '活动', _CARD_TEXT],   # 1 层：点「异界迷宫」卡片
        ['难度15', '结束探索', '遗物之门'],             # 玩法内部 → arrived（续局）
        ['难度15', '结束探索', '遗物之门'],             # play i=0：door → 点门
        ['难度通关', '结束探索'],                       # play i=1：boss_cleared
        ['难度通关', '结束探索'],                       # play i=2：结束探索
        [],                                             # 无确认弹窗
    ],
)
t21 = _make_task(bot21)
r21 = t21._run_once()
check('续局：run() 返回 done', r21 == 'done', f'result={r21}')
check('续局：_resumed 为 True', t21._resumed is True)
check('续局：未调用点「进入」(_enter_labyrinth 被跳过)',
      '进入' not in bot21.clicked, str(bot21.clicked))
check('续局：直接进入玩法循环并选门',
      '门卡_遗物之门_按钮' in bot21.clicked, str(bot21.clicked))
check('续局：detector 全程 unknown 未误发 ESC',
      not any('press_esc' in c for c in bot21.calls),
      str([c for c in bot21.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
#  22. v1.8.3 回归：点卡片后正常进迷宫主页（detector 返回 'labyrinth'）
#      断言：仍走「进入」步骤，_resumed 为 False（确认未改坏原路径）
# ═══════════════════════════════════════════════════════════
print('\n── 22. 回归：正常进迷宫主页仍走「进入」 ──')
bot22 = MockBot(
    phases=['playbook', 'labyrinth'],  # 点卡片后 detector 正常返回 labyrinth
    texts_by_call=[
        ['难度15', '探索阵容(5/12)'],                 # 阶段0：无弹窗
        ['难度15', '探索阵容(5/12)'],   # v1.8.32：全局导航判定帧
        ['玩法目录', '常规玩法', '活动', _CARD_TEXT], # playbook + 卡片
        ['玩法目录', '常规玩法', '活动', _CARD_TEXT], # find_card
        ['进入'],                                     # _click_text('进入')
        ['难度15', '探索阵容(12/12)'],                # 点进入后：无弹窗
        ['难度15', '结束探索', '遗物之门'],           # play：door → 点门
        ['难度15', '4/15', '战斗', 'F'],                                # battle_prep → F
        ['难度通关', '结束探索'],                     # 战斗等待：boss_cleared
        ['难度通关', '结束探索'],                     # 主循环：boss_cleared
        ['难度通关', '结束探索'],                     # _click_text('结束探索')
        [],                                           # 确认弹窗：无
    ],
    card_after_scrolls=0,
)
t22 = _make_task(bot22)
r22 = t22._run_once()
check('回归：run() 返回 done', r22 == 'done', f'result={r22}')
check('回归：_resumed 为 False（走正常路径）', t22._resumed is False)
check('回归：仍点「进入」(_enter_labyrinth 执行)',
      '进入' in bot22.clicked, str(bot22.clicked))

# ═══════════════════════════════════════════════════════════
#  23. v1.8.3 续局残留：任务启动时画面已是迷宫内部（detector 恒 unknown）
#      断言：_navigate_to_labyrinth 直接返回 True，不 ESC、不滚动
# ═══════════════════════════════════════════════════════════
print('\n── 23. 续局残留：启动即在迷宫内部 ──')
bot23 = MockBot(
    phases=['unknown'],   # 启动画面 detector 恒 unknown
    texts_by_call=[
        ['难度15', '结束探索', '遗物之门'],   # _is_on_playbook：非玩法目录（无 tab）
        ['难度15', '结束探索', '遗物之门'],   # _is_in_labyrinth_play：续局特征词
    ],
)
t23 = _make_task(bot23)
r23 = t23._navigate_to_labyrinth()
check('启动即在内部：_navigate_to_labyrinth 返回 True', r23 is True, f'result={r23}')
check('启动即在内部：_resumed 为 True', t23._resumed is True)
check('启动即在内部：未按 ESC',
      not any('press_esc' in c for c in bot23.calls),
      str([c for c in bot23.calls if 'esc' in c.lower()]))
check('启动即在内部：未滚动',
      bot23.scroll_count == 0, f'scroll={bot23.scroll_count}')

# ═══════════════════════════════════════════════════════════
#  24. v1.8.7 启动位置识别：任务启动时画面已在「选择烙印」界面
#      （用户真机 2026-09-03：原逻辑 detector 判 unknown → 导航 ESC
#      死循环，烙印界面 ESC 无效）。_startup_probe 用玩法状态机判定
#      imprint → 'play' → 跳过导航与「进入」，直通 _play_loop 续跑
# ═══════════════════════════════════════════════════════════
print('\n── 24. 启动即在烙印界面：直通玩法循环续跑 ──')
bot24 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['选择烙印', '确认'],                   # probe：imprint → 'play' 直通
        ['选择烙印', '稀有', '藏品柜'],   # play：判定 imprint → 读名 → 点稀有度卡（v2.2.10）
        ['选择烙印', '狂怒', '确认'],           # 点卡后 OCR → 确认钮
                                               # （v1.9.34 卡名读取源帧）
        ['难度15', '结束探索', '遗物之门'],     # play：door → 点门
        ['难度通关', '结束探索'],               # play：boss_cleared
        ['难度通关', '结束探索'],               # _click_text('结束探索')
        [],                                     # 无确认弹窗
    ],
)
t24 = _make_task(bot24)
r24 = t24._run_once()
check('启动在烙印：run() 返回 done', r24 == 'done', f'result={r24}')
check('启动在烙印：跳过导航（无 detect_phase 调用）',
      not any('detect_phase' in c for c in bot24.calls),
      str([c for c in bot24.calls if 'detect' in c]))
check('启动在烙印：未点「进入」(_enter_labyrinth 被跳过)',
      '进入' not in bot24.clicked, str(bot24.clicked))
check('启动在烙印：直接点烙印第一个稀有度卡（v1.8.10）',
      'click_at:烙印_选卡' in bot24.calls,
      str([c for c in bot24.calls if 'click_at' in c]))
check('启动在烙印：烙印确认钮被点',
      '烙印_确认' in bot24.clicked, str(bot24.clicked))
check('启动在烙印：选完烙印继续推进选门 + 结束探索',
      '门卡_遗物之门_按钮' in bot24.clicked and '结束探索' in bot24.clicked,
      str(bot24.clicked))
check('启动在烙印：全程未按 ESC',
      not any('press_esc' in c for c in bot24.calls),
      str([c for c in bot24.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
#  25. v1.8.7 启动位置识别：任务启动时画面已在「备战」界面
#      → probe 判 battle_prep → 'play' 直通 → F 开战续跑
# ═══════════════════════════════════════════════════════════
print('\n── 25. 启动即在备战界面：直通 F 开战 ──')
bot25 = MockBot(
    phases=['labyrinth'],
    texts_by_call=[
        ['难度15', '4/15', '战斗', 'F', '出战阵容'],   # probe：battle_prep 双重硬约束（难度+/15+战斗+F）命中
        ['难度15', '4/15', '战斗', 'F', '出战阵容'],   # play：判定 battle_prep → 按 F
        ['难度15', '4/15', '战斗胜利'],                # _start_and_wait_battle 轮询命中
        ['难度15', '4/15', '战斗胜利'],                # 主循环 victory → 点空白关闭
        ['难度15', '4/15', '难度通关', '结束探索'],    # 主循环 boss_cleared
        ['难度15', '4/15', '难度通关', '结束探索'],    # _click_text('结束探索')
        [],                                            # 无确认弹窗
    ],
)
t25 = _make_task(bot25)
r25 = t25._run_once()
check('启动在备战：run() 返回 done', r25 == 'done', f'result={r25}')
check('启动在备战：跳过导航（无 detect_phase 调用）',
      not any('detect_phase' in c for c in bot25.calls),
      str([c for c in bot25.calls if 'detect' in c]))
check('启动在备战：直接按 F 开战',
      any('press_f' in c for c in bot25.calls),
      str([c for c in bot25.calls if 'press_f' in c]))
check('启动在备战：胜利弹层关闭后推进到结束探索',
      '结束探索' in bot25.clicked, str(bot25.clicked))
check('启动在备战：全程未按 ESC',
      not any('press_esc' in c for c in bot25.calls),
      str([c for c in bot25.calls if 'esc' in c.lower()]))

# ═══════════════════════════════════════════════════════════
#  26. v1.8.8 启动位置识别：任务启动时画面已在「挂机推图」备战界面
#      （用户真机 2026-09-03 22:10：v1.8.7 probe 把「赛季先锋关卡 588」
#      备战界面判成 maze battle_prep 直通按 F → 推图误开战）。
#      v1.8.8 加 _PUSH_MAP_MARKS 反向排除 + battle_prep 加「难度」「/15」
#      双重正向硬约束（用户明确「难度15」「X/15」为迷宫顶栏独有词）→
#      startup_probe 命中推图词 → 'clear' 走导航退回，不会进入 _play_loop
#      误按 F
# ═══════════════════════════════════════════════════════════
print('\n── 26. 启动即在推图备战界面：probe 反向排除，不误开战 ──')

# 26a：推图备战界面（用户截图实测 OCR 词集，0.97+ 置信度）
bot26 = MockBot(phases=['unknown'], texts_by_call=[
    ['赛季先锋关卡588', '战斗', 'F',
     '自动挑战', '通关阵容', '编队', '先锋之炎', '1586级'],
    ['赛季先锋关卡588', '战斗', 'F',
     '自动挑战', '通关阵容', '编队', '先锋之炎', '1586级'],
])
t26 = _make_task(bot26)
probe_push = t26.startup_probe()
items_push = bot26._ocr_screen()
check('启动在推图备战：startup_probe 返回 clear（非 ready，防误开战）',
      probe_push == 'clear', f'result={probe_push}')
check('启动在推图备战：_detect_play_state 命中推图词返 unknown',
      t26._detect_play_state(items_push) == 'unknown', '_detect_play_state 状态')

# 26b：迷宫备战界面（用户截图实测 OCR 词集，验证不误伤）
bot26b = MockBot(phases=['unknown'], texts_by_call=[
    ['难度15', '4/15', '战斗', 'F', '守护水晶'],
    ['难度15', '4/15', '战斗', 'F', '守护水晶'],
])
t26b = _make_task(bot26b)
probe_maze = t26b.startup_probe()
items_maze = bot26b._ocr_screen()
check('迷宫备战：startup_probe 返回 ready（不误伤）',
      probe_maze == 'ready', f'result={probe_maze}')
check('迷宫备战：_detect_play_state 命中 battle_prep',
      t26b._detect_play_state(items_maze) == 'battle_prep', '_detect_play_state 状态')

# ═══════════════════════════════════════════════════════════
#  27. v1.8.9 迷宫规则完整化（方法级单测，直击新逻辑）：
#      a) _end_exploration：deep_challenge + 15 层结算帧(含「继续探索」)
#         → 点「继续探索」（进入深层 16-20）
#      b) _end_exploration：deep_challenge + 20/20 结算帧 → 点「结束探索」
#         （20 层深层终点必结束，即使弹层残留「继续探索」）
#      c) _end_exploration：默认(不勾 deep) + 15 层结算 → 点「结束探索」
#      d) _detect_play_state：victory 变体「挑战成功」→ victory
#      e) _handle_imprint：四选一烙印(稀有度标签≥4) → 点首卡 (0.5,0.327)
#      f) _extract_progress：X=已通关数语义（分母 15/20）
# ═══════════════════════════════════════════════════════════
print('\n── 27. v1.8.9 深层/挑战成功/四选一烙印/进度解析 ──')

# 27a：deep_challenge=True + 15/15 结算帧（含继续探索）→ 点继续探索
bot27a = MockBot(phases=['labyrinth'], texts_by_call=[
    ['难度15', '15/15', '难度通关', '到达层数', '继续探索', '结束探索'],
    ['难度15', '15/20', '继续探索'],    # _click_text('继续探索') 内部 OCR
    [],                                 # 确认弹窗探测
])
t27a = _make_task(bot27a, options={'deep_challenge': True})
items27a = bot27a._ocr_screen()
ok27a = t27a._end_exploration(items27a)
check('27a 深层：15 层结算点「继续探索」（返回 True）',
      ok27a and '继续探索' in bot27a.clicked and '结束探索' not in bot27a.clicked,
      str(bot27a.clicked))

# 27b：deep_challenge=True + 20/20 结算帧 → 必点「结束探索」
bot27b = MockBot(phases=['labyrinth'], texts_by_call=[
    ['难度15', '20/20', '难度通关', '到达层数', '继续探索', '结束探索'],
    ['难度15', '20/20', '结束探索'],
    [],
])
t27b = _make_task(bot27b, options={'deep_challenge': True})
items27b = bot27b._ocr_screen()
ok27b = t27b._end_exploration(items27b)
check('27b 深层终点：20/20 必点「结束探索」（即使残留继续探索）',
      ok27b and '结束探索' in bot27b.clicked,
      str(bot27b.clicked))

# 27c：默认（不勾 deep）+ 15 层结算帧 → 点「结束探索」（v1.8.5 语义回归）
bot27c = MockBot(phases=['labyrinth'], texts_by_call=[
    ['难度15', '15/15', '难度通关', '到达层数', '继续探索', '结束探索'],
    ['难度15', '15/15', '结束探索'],
    [],
])
t27c = _make_task(bot27c)
items27c = bot27c._ocr_screen()
ok27c = t27c._end_exploration(items27c)
check('27c 默认：15 层结算点「结束探索」（不勾深层不误继续）',
      ok27c and '结束探索' in bot27c.clicked and '继续探索' not in bot27c.clicked,
      str(bot27c.clicked))

# 27d：_detect_play_state「挑战成功」→ victory（菲茨小店挑战奖励弹层）
bot27d = MockBot(phases=['labyrinth'])
t27d = _make_task(bot27d)
check('27d 挑战成功 → victory', t27d._detect_play_state(
    _items('挑战成功', '全场均价', '后续菲茨小店商品价格均变为50晶粹',
            '— 点击空白处关闭')) == 'victory', '_detect_play_state 状态')
check('27d 战斗胜利仍 victory（回归）', t27d._detect_play_state(
    _items('战斗胜利', '获得奖励')) == 'victory', '_detect_play_state 状态')

# 27e：_handle_imprint 四选一（3稀有+1精英，真机 OCR 形态）→ 点第一个稀有度
bot27e = MockBot(phases=['labyrinth'], texts_by_call=[
    ['炽焰锤炼', '腐化风暴', '狂暴攻击', '冲锋之矛',
     '稀有', '精英', '稀有', '稀有', '确认'],
    ['确认'],                              # 点卡后 OCR 找确认钮
])
t27e = _make_task(bot27e)
items27e = bot27e._ocr_screen()
t27e._handle_imprint(items27e)
check('27e 四选一烙印：点第一个稀有度卡（v1.8.10，无需区分三/四选一）',
      'click_at:烙印_选卡' in bot27e.calls,
      str([c for c in bot27e.calls if 'click_at' in c]))
check('27e 四选一烙印：确认钮被点', '烙印_确认' in bot27e.clicked,
      str(bot27e.clicked))

# 27f：_handle_imprint 三选一 → 同样点第一个稀有度卡（v1.8.10 统一流程）
bot27f = MockBot(phases=['labyrinth'], texts_by_call=[
    ['选择烙印', '稀有', '藏品柜', '确认'],   # v2.2.10：弹层帧带卡名
    ['确认'],
])
t27f = _make_task(bot27f)
items27f = bot27f._ocr_screen()
t27f._handle_imprint(items27f)
check('27f 三选一烙印：点第一个稀有度卡（不回归）',
      'click_at:烙印_选卡' in bot27f.calls,
      str([c for c in bot27f.calls if 'click_at' in c]))

# 27f2：稀有度全漏检 → 重试 OCR 后不动作（v1.8.21 删除固定比例兜底）
bot27f2 = MockBot(phases=['labyrinth'], texts_by_call=[
    ['选择烙印', '烙印·狂怒'],   # 首帧漏检
    [],                          # 重试 1：仍漏检
    [],                          # 重试 2：仍漏检 → 放弃
])
t27f2 = _make_task(bot27f2)
t27f2._handle_imprint(bot27f2._ocr_screen())
check('27f2 稀有度全漏检：重试 OCR 后不点击（无固定坐标兜底）',
      not any(c.startswith(('click_ratio', 'click_at'))
              for c in bot27f2.calls),
      str([c for c in bot27f2.calls if 'click' in c]))

# 27g：_extract_progress 纯逻辑（X=已通关数，分母 15/20）
t27g = _make_task(MockBot(phases=['labyrinth']))
check('27g 进度 4/15 → (4,15)', t27g._extract_progress(
    _items('难度15', '4/15', '结束探索')) == (4, 15), '_extract_progress')
check('27g 进度 19/20 → (19,20)', t27g._extract_progress(
    _items('难度15', '19/20', '结束探索')) == (19, 20), '_extract_progress')
check('27g 无分式 → None', t27g._extract_progress(
    _items('难度通关', '结束探索')) is None, '_extract_progress')
check('27g 分母噪声忽略', t27g._extract_progress(
    _items('难度15', '2/41/2', '结束探索')) is None, '_extract_progress')

# 28：v1.8.10 全场景识别修复（真机 28 张素材批量测试固化为回归用例）
t28 = _make_task(MockBot(phases=['labyrinth']))
# 28a 备战界面无独立「F」（F 图标 OCR 漏成 YE/Y=/E）→ 仍判 battle_prep
check('28a 备战无F：难度+战斗 → battle_prep', t28._detect_play_state(
    _items('战斗', 'YE', '73万', '15/15', '难度15', '+130.0%')
) == 'battle_prep', '_detect_play_state')
# 28b 推图备战仍被反向排除（回归：不能因放宽判据复活误判）
check('28b 推图备战反向排除仍生效', t28._detect_play_state(
    _items('赛季先锋关卡', '自动挑战', '战斗', 'F', '编队')
) == 'unknown', '_detect_play_state')
# 28c 门卡地图不误判 battle_prep（门卡地图无「战斗」；含难度/进度）
check('28c 门卡地图仍判 door', t28._detect_play_state(
    _items('结束探索', '英雄', '精英', '首领', '4/15', '难度15')
) == 'door', '_detect_play_state')
# 28d 四选一全「稀有」（种类=1 次数=3）→ 按出现次数判 imprint
check('28d 烙印三卡全稀有 → imprint（按次数计数）', t28._detect_play_state(
    _items('确认', '稀有', '绿裔神弓', '稀有', '亡灵法球', '稀有',
           '狂风涣散', '3/15', '难度15')
) == 'imprint', '_detect_play_state')
# 28e 小店局部画面（无「商品列表」）→「进去看看」兜底判 shop
check('28e 小店兜底「进去看看」 → shop', t28._detect_play_state(
    _items('菲茨小店', '进去看看', '神秘商人菲茨经营的小店，看')
) == 'shop', '_detect_play_state')
# 28f 门卡局部画面（无「结束探索」）→「XX之门」兜底判 door
check('28f 门卡兜底「晶粹之门」 → door', t28._detect_play_state(
    _items('晶粹之门', '击败敌人额外获得大量晶粹，', '可在菲茨小店购买商品。')
) == 'door', '_detect_play_state')
# 28g 门卡地图含小店卡「进去看看」→ 先判「结束探索」仍是 door（不误判 shop）
check('28g 门卡地图含进去看看仍判 door（优先级）', t28._detect_play_state(
    _items('结束探索', '菲茨小店', '进去看看', '晶粹之门',
           '3/15', '难度15')
) == 'door', '_detect_play_state')
# 28h v1.8.10b 战力单位反向排除（用户建议）：迷宫备战万级、推图亿级
# → 备战特征帧含「亿」不判 battle_prep（第三层推图防御）
check('28h 备战帧含「亿」→ 不判 battle_prep', t28._detect_play_state(
    _items('难度15', '15/15', '战斗', '915万', '2.1亿', 'F')
) == 'unknown', '_detect_play_state')
check('28h 迷宫备战万级帧不受影响', t28._detect_play_state(
    _items('战斗', '73万', '915万', '15/15', '难度15')
) == 'battle_prep', '_detect_play_state')

# 28i v1.8.11 门卡词表补「首领/深层挑战」（真机 9/4：BOSS/深层层大量标题漏检）
# 15关 BOSS：「首领」标题 + 按钮命中 → 组合点击（标题x+按钮y），不再走兜底分支
bot28i = MockBot(phases=['labyrinth'], texts_by_call=[
    ['结束探索', '英雄', '祝你好运', '首领', '14/15', '难度15'],
])
t28i = _make_task(bot28i)
t28i._pick_door(bot28i._ocr_screen())
check('28i BOSS 门卡「首领」→ 标题x+按钮y 组合点击',
      '门卡_首领_按钮' in bot28i.clicked,
      str([c for c in bot28i.calls if 'click_at' in c]))
# 16-19 深层：「深层挑战」标题同样命中
bot28j = MockBot(phases=['labyrinth'], texts_by_call=[
    ['结束探索', '英雄', '深层挑战', '勇者之路', '17/20', '难度15'],
])
t28j = _make_task(bot28j)
t28j._pick_door(bot28j._ocr_screen())
check('28j 深层门卡「深层挑战」→ 组合点击',
      '门卡_深层挑战_按钮' in bot28j.clicked,
      str([c for c in bot28j.calls if 'click_at' in c]))
# 「首领」为子串匹配，同时命中「深层首领」（20关）——单门场景等价
bot28k = MockBot(phases=['labyrinth'], texts_by_call=[
    ['结束探索', '深层首领', '19/20', '难度15'],
])
t28k = _make_task(bot28k)
t28k._pick_door(bot28k._ocr_screen())
check('28k 20关「深层首领」→ 经「首领」子串命中组合点击',
      '门卡_首领_按钮' in bot28k.clicked,
      str([c for c in bot28k.calls if 'click_at' in c]))

print('\n' + '=' * 54)
# ══════════════════════════════════════════════════════════════
#  v2.2.7：模式1 的「单门分支」不得选烙印晶球（用户 2026-09-15）
#    用户澄清「**游戏中不存在单门晶球**」→ 模式1 下若读到唯一的晶球标题，
#    该标题必为误读或另一扇门标题被漏读。而单门分支**不比较优先级**
#    （评分分支已有 score=99 拦截）→ 原实现会直选它 ✗
# ══════════════════════════════════════════════════════════════
print('── T29 v2.2.7 模式1 单门晶球守卫 ──')

_b1 = MockBot(phases=['labyrinth'])
_t1 = _make_task(_b1)                    # options 空 → _run_mode() = 'clear15'
_t1._pick_door([(300, 400, '烙印晶球', 0.9, 350, 450)])
check('G1a 【分叉】模式1 单门读到晶球 → 不点击（旧实现会直选）',
      not any(c.startswith('click_at:') for c in _b1.calls),
      str([c for c in _b1.calls if 'click' in c]))
check('G1b 也不盲点按钮（未走 _click_abs）',
      not any('_click_abs' in c for c in _b1.calls),
      str([c for c in _b1.calls if '_click_abs' in c]))

_b2 = MockBot(phases=['labyrinth'])
_t2 = _make_task(_b2, options={'run_mode': 'boss20'})
_t2._pick_door([(300, 400, '烙印晶球', 0.9, 350, 450)])
check('G2 深层模式（boss20）单门晶球 → 仍正常选（不误伤）',
      any(c.startswith('click_at:') for c in _b2.calls),
      str([c for c in _b2.calls if 'click' in c]))

_b3 = MockBot(phases=['labyrinth'])
_t3 = _make_task(_b3)
_t3._pick_door([(300, 400, '遗物之门', 0.9, 350, 450)])
check('G3 模式1 单门非晶球（遗物之门）→ 仍正常选（不误伤）',
      any(c.startswith('click_at:') for c in _b3.calls),
      str([c for c in _b3.calls if 'click' in c]))

# ══════════════════════════════════════════════════════════════
#  v2.2.8：双倍 + 三目标遗物 = 最优先（用户 2026-09-15 定案）
#    素材实证（异界迷宫/双倍遗物.png）：标题仍是「遗物之门」，描述含
#    「击败敌人可获得双倍奖励。」+「获得遗物：极速 (1/2)」→ 实给 2 个。
#    注：不测「晶球 vs 双倍遗物」——用户澄清「烙印晶球必定和商店共同出现」，
#        该组合在游戏中不存在（不可达场景不写测试）。
# ══════════════════════════════════════════════════════════════
print('── T30 v2.2.8 双倍三目标遗物优先级 ──')

_b30 = MockBot(phases=['labyrinth'])
_t30 = _make_task(_b30)


def _frame30(desc_lines, tx=400, ty=200):
    """标题「遗物之门」@(tx,ty) + 同卡描述行（其下 40px 起、同 x，归属最近标题）。"""
    out = [(ty, tx, '遗物之门', 0.95, tx - 50, tx + 50)]
    _y = ty + 40
    for line in desc_lines:
        out.append((_y, tx, line, 0.95, tx - 50, tx + 50))
        _y += 30
    return out


_s_single_strong = _t30._door_score(
    '遗物之门', _frame30(['获得遗物：强壮', '效果：攻击提升。']),
    400, 200, [400])
_s_double_fast = _t30._door_score(
    '遗物之门',
    _frame30(['强力敌人', '击败敌人可获得双倍奖励。',
              '获得遗物：极速 (1/2)', '效果：急速提升5点。']), 400, 200, [400])
_s_double_plain = _t30._door_score(
    '遗物之门', _frame30(['击败敌人可获得双倍奖励。', '获得遗物：护盾']),
    400, 200, [400])

check('H1 【分叉】双倍极速 优于 单倍强壮（旧实现按档位 → 强壮胜）',
      _s_double_fast < _s_single_strong,
      f'double极速={_s_double_fast} vs single强壮={_s_single_strong}')
check('H2 双倍强壮 优于 单倍强壮',
      _t30._door_score(
          '遗物之门',
          _frame30(['击败敌人可获得双倍奖励。', '获得遗物：强壮']),
          400, 200, [400]) < _s_single_strong, '')
check('H3 双倍+普通遗物（描述无三目标名）不抬高，仍在普通遗物档 5.5',
      _s_double_plain[0] == 5.5, str(_s_double_plain))
check('H4 单倍普通遗物门仍为 5.5（不误伤）',
      _t30._door_score('遗物之门', _frame30(['获得遗物：护盾']),
                       400, 200, [400])[0] == 5.5, '')
check('H5 双倍三目标 < 0（进入遗物类最高档，优于全部单倍目标）',
      _s_double_fast[0] < 0, str(_s_double_fast))

print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print(f'  ❌ 失败项：{_FAIL}')
else:
    print('  ✅ 异界迷宫导航干跑测试全部通过')
print('=' * 54)

sys.exit(0 if not _FAIL else 1)
