# -*- coding: utf-8 -*-
"""女神试炼任务 · 干跑（dry-run）逻辑测试（v1.8.17）

不依赖真机与游戏窗口：MockBot 模拟 phase 序列与 OCR 结果，
验证 `_navigate_to_trial()` 导航、`_play_loop()` 状态机、
`_detect_state()` 判据、全等匹配「扫荡」与降级链。

关键回归点：
  - 推图备战页（自动挑战+战斗，无「秘藏」）不得判 battle_prep（v1.8.8 同款撞词）
  - 「扫荡」全等匹配不撞「扫荡掉落」（用户 2026-09-04 明确）
  - 「挑战」子串匹配直点（说明文字在按钮内，点到即触发）
  - 大按钮文字驱动状态机：挑战态→挑战流程；扫荡态→直接扫荡（全部通关）

用法：
    D:/anaconda3/envs/afk_gpu/python.exe tests/test_goddess_trial_dryrun.py
"""

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.goddess_trial import GoddessTrialTask  # noqa: E402

# ── 真机 OCR 词表（用户 2026-09-04 截图实测）────────────────
SELECT_CHALLENGE = [   # 选关页-挑战态
    '今日可挑战层数上限：60', '攻秘藏', '挑战',
    '本次挑战不消耗女神徽记', '详情', '扫荡掉落', '首通', '战斗指南',
]
SELECT_SWEPT = [       # 选关页-扫荡态（无可挑战层/全部通关）
    '今日可挑战层数上限：60', '攻秘藏', '扫荡', '③6/1',
    '扫荡掉落', '首通', '详情', '明日',
]
BATTLE_PREP = [        # 女神备战页（顶栏含秘藏）
    '强攻秘藏-第56层', '自动挑战', '战斗', '通关阵容', '1200级', '12968亿',
]
PUSH_MAP_PREP = [      # 推图备战页（v1.8.8 撞词副本——必须判 unknown）
    '赛季先锋关卡588', '自动挑战', '战斗', '通关阵容', '编队', '先锋之炎',
]
AUTO_BATTLE_HUD = [    # 自动挑战中 HUD（战斗中正向识别，v1.8.19）
    '自动挑战中...', '1328级',
]
VICTORY_RESULT = [     # 最后一关通关结算页（需 ESC，v1.8.19）
    '战斗胜利', '首次通关', '再次挑战', '57', '58', '59', '60',
]
SWEEP_DIALOG = [       # 扫荡弹窗
    '强攻秘藏第55层', '女神试炼', '奖励预览', '扫荡次数', '扫荡', '450',
]
HOME_TEXTS = ['神秘屋', '玩法目录', '英雄厅堂', '公会']   # v1.8.32：补神秘屋（真实 0 层底栏，全局识别判据需要）
PLAYBOOK_TEXTS = ['玩法目录', '常规玩法', '活动', '女神试炼', '异界迷宫']

# ── 结果收集 ────────────────────────────────────────────────
_PASS: list = []
_FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    tag: str = '✅' if cond else '❌'
    print(f'{tag} {name}' + (f' — {detail}' if detail else ''))
    (_PASS if cond else _FAIL).append(name)


# ── Mock bot（同 labyrinth 干跑模式）────────────────────────

class MockBot:
    """按脚本返回 phase / OCR 结果，记录全部调用。"""

    def __init__(self, phases=None, texts_by_call=None, template_hit=True,
                 back_hit=True, fallback_texts=None):
        self._phases = list(phases or [])
        self._texts_by_call = list(texts_by_call or [])
        self._template_hit = template_hit
        self._back_hit = back_hit
        # texts 耗尽后的固定返回（默认选关页挑战态；unknown 用例须显式给
        # 乱码 fallback，否则主循环会死循环点「挑战」）
        self._fallback = list(fallback_texts) if fallback_texts else None
        self.calls: list = []
        self.clicked: list = []
        self.scroll_count: int = 0

    def detect_phase(self):
        self.calls.append('detect_phase')
        return self._phases.pop(0) if self._phases else 'unknown'

    def _ocr_screen(self, img=None, no_cache=False):
        self.calls.append('_ocr_screen')
        if self._texts_by_call:
            texts = self._texts_by_call.pop(0)
        elif self._fallback is not None:
            texts = list(self._fallback)
        else:
            texts = list(SELECT_CHALLENGE)   # 耗尽兜底：选关页挑战态
        return [(100, 200, t, 0.9, 150, 250) for t in texts]

    def find_text(self, items, keyword, min_conf=0.5):
        self.calls.append(f'find_text:{keyword}')
        for it in items:
            if keyword in it[2]:
                return (it[1], it[0])
        return None

    def click_at(self, cx, cy, label=''):
        self.calls.append(f'click_at:{label}')
        self.clicked.append(label)

    def template_match(self, tpl_path, threshold=0.8, scale_range=(0.5, 2.0, 0.1),
                       silent=False, region=None):
        name = os.path.basename(tpl_path)
        self.calls.append(f'template_match:{name}@{threshold}')
        return (500, 500) if self._template_hit else None

    def _click_abs(self, cx, cy):
        self.calls.append(f'_click_abs:({cx},{cy})')

    def press_esc(self, label=''):
        self.calls.append(f'press_esc:{label}')

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        self.scroll_count += 1
        self.calls.append(f'scroll_wheel:{clicks}')

    def _handle_common_phase(self, phase):
        self.calls.append(f'common:{phase}')
        return phase

    def find_and_focus(self):
        return 12345

    def click_back_button(self, tpls=None, threshold=0.75):
        self.calls.append('click_back_button')
        return (500, 500) if self._back_hit else None


def _make_task(mock_bot, stop=False, max_time=None):
    stop_ev = threading.Event()
    pause_ev = threading.Event()
    pause_ev.set()
    if stop:
        stop_ev.set()
    ctx = {
        'bot': mock_bot,
        'logger': lambda m: None,
        'stats': {},
        'stop_event': stop_ev,
        'pause_event': pause_ev,
        'options': {},
        'max_time': max_time,
        'inst_id': 'goddess_trial#dry',
    }
    return GoddessTrialTask(ctx)


# ═══════════════════════════════════════════════════════════
#  1. _detect_state 判据（真机 OCR 词表）
# ═══════════════════════════════════════════════════════════
print('\n── 1. 状态判定 ──')
bot = MockBot()
t = _make_task(bot)
mk = lambda texts: [(100, 200, x, 0.9, 150, 250) for x in texts]
check('选关页(挑战态) → select_page',
      t._detect_state(mk(SELECT_CHALLENGE)) == 'select_page')
check('选关页(扫荡态) → select_page',
      t._detect_state(mk(SELECT_SWEPT)) == 'select_page')
check('备战页 → battle_prep',
      t._detect_state(mk(BATTLE_PREP)) == 'battle_prep')
check('推图备战页(无秘藏) → unknown（v1.8.8 撞词防御）',
      t._detect_state(mk(PUSH_MAP_PREP)) == 'unknown',
      str(t._detect_state(mk(PUSH_MAP_PREP))))
check('扫荡弹窗 → sweep_dialog',
      t._detect_state(mk(SWEEP_DIALOG)) == 'sweep_dialog')
check('战斗胜利结算页 → victory_result（v1.8.19）',
      t._detect_state(mk(VICTORY_RESULT)) == 'victory_result')
check('自动挑战中 HUD → auto_battle（v1.8.19）',
      t._detect_state(mk(AUTO_BATTLE_HUD)) == 'auto_battle')
check('0层 → unknown', t._detect_state(mk(HOME_TEXTS)) == 'unknown')

# ═══════════════════════════════════════════════════════════
#  2. 「扫荡」全等匹配（不撞「扫荡掉落」）
# ═══════════════════════════════════════════════════════════
print('\n── 2. 全等匹配 ──')
items = mk(['扫荡掉落', '扫荡神藏', '扫荡', '扫荡掉落'])
pos = t._find_exact_word(items, '扫荡')
check('全等匹配命中孤立「扫荡」', pos == (200, 100), str(pos))
items_no = mk(['扫荡掉落', '扫荡神藏', '本次挑战不消耗女神徽记'])
check('无全等词时返回 None（不撞扫荡掉落）',
      t._find_exact_word(items_no, '扫荡') is None)

# v1.8.17 真机 20:55 回归：判据文字「今日可挑战层数上限：60」含"挑战"子串
# 且 OCR 顺序先于大按钮 → 全等匹配必须跳过它，点真正的按钮
items_trap = mk(['今日可挑战层数上限：60', '攻秘藏', '挑战',
                 '本次挑战不消耗女神徽记'])
pos_t = t._find_exact_word(items_trap, '挑战')
check('「挑战」全等不撞「今日可挑战层数上限」(排在前面)',
      pos_t == (200, 100), str(pos_t))
check('「挑战」全等不撞说明文字',
      t._find_exact_word(
          mk(['今日可挑战层数上限：60', '本次挑战不消耗女神徽记']),
          '挑战') is None)

# ═══════════════════════════════════════════════════════════
#  3. startup_probe 三态
# ═══════════════════════════════════════════════════════════
print('\n── 3. 启动探测 ──')
bot3a = MockBot(texts_by_call=[SELECT_CHALLENGE])
t3a = _make_task(bot3a)
check('启动在选关页 → ready', t3a.startup_probe() == 'ready')
bot3b = MockBot(texts_by_call=[BATTLE_PREP])
t3b = _make_task(bot3b)
check('启动在备战页 → ready', t3b.startup_probe() == 'ready')
bot3c = MockBot(texts_by_call=[HOME_TEXTS])
t3c = _make_task(bot3c)
check('启动在0层 → clear', t3c.startup_probe() == 'clear')

# ═══════════════════════════════════════════════════════════
#  4. 端到端：0层 → 玩法目录 → 挑战 → 自动挑战 → 扫荡拉满 → done
# ═══════════════════════════════════════════════════════════
print('\n── 4. 端到端完整流程 ──')
# texts 序列与真实 OCR 调用次序对齐：
#   probe → 导航i0(自判+playbook判+点玩法目录OCR) → 导航i1(自判+playbook判
#   +找卡片+wait进入) → 主循环(挑战→备战→点自动挑战→等待×2→扫荡→弹窗
#   →确认→等待回页→done)
bot4 = MockBot(
    phases=['home', 'playbook'],
    # v1.8.32：导航接入全局导航系统后，序列改为：probe → 全局导航(0层点
    # 玩法目录 → 1层点女神试炼卡片 → 到达选关页) → 主循环
    texts_by_call=[
        HOME_TEXTS,          # startup_probe（0层 → clear）
        HOME_TEXTS,          # 全局导航：0层 → 点「玩法目录」
        PLAYBOOK_TEXTS,      # 全局导航：1层 → 点「女神试炼」卡片
        SELECT_CHALLENGE,    # 全局导航：到达选关页
        SELECT_CHALLENGE,    # 主循环：大按钮=挑战 → 点
        BATTLE_PREP,         # 主循环：备战页
        BATTLE_PREP,         # 点「自动挑战」OCR
        ['战斗中', '1328级'],  # 自动挑战中（无特征）
        VICTORY_RESULT,      # 最后一关结算页 → ESC
        SELECT_SWEPT,        # 回选关页 → 等待结束
        SELECT_SWEPT,        # 主循环：大按钮=扫荡 → 点
        SWEEP_DIALOG,        # 等弹窗命中
        SWEEP_DIALOG,        # 主循环：⏭ 拉满
        SWEEP_DIALOG,        # 点「扫荡」确认 OCR
        SELECT_SWEPT,        # 回选关页
        SELECT_SWEPT,        # 主循环：_swept → done
    ],
    fallback_texts=SELECT_SWEPT,   # 帧不足时兜底（扫荡完成态 → 收敛结束）
)
t4 = _make_task(bot4)
r4 = t4.run()
check('端到端返回 done', r4 == 'done', f'result={r4}')
check('点击过「玩法目录」', '玩法目录' in bot4.clicked, str(bot4.clicked))
check('点击过卡片「女神试炼」', '女神试炼' in bot4.clicked, str(bot4.clicked))
check('点过「挑战」(全等)', '挑战_大按钮' in bot4.clicked, str(bot4.clicked))
check('点过「自动挑战」', '自动挑战' in bot4.clicked, str(bot4.clicked))
check('点过 ⏭ 拉满模板', '_click_abs:(500,500)' in bot4.calls,
      str([c for c in bot4.calls if '_click_abs' in c]))
check('点过「扫荡」大按钮+确认',
      '扫荡_大按钮' in bot4.clicked and '扫荡_确认' in bot4.clicked,
      str(bot4.clicked))
check('任务标记已扫荡', t4._swept is True)
check('结算页被 ESC 退出（v1.8.19）',
      'press_esc:自动挑战_结算页_ESC' in bot4.calls,
      str([c for c in bot4.calls if 'press_esc' in c]))

# ═══════════════════════════════════════════════════════════
#  5. 端到端：启动即在选关页扫荡态（全部通关）→ 直接扫荡 → done
# ═══════════════════════════════════════════════════════════
print('\n── 5. 全部通关直扫荡 ──')
bot5 = MockBot(texts_by_call=[
    SELECT_SWEPT,        # startup_probe → ready
    SELECT_SWEPT,        # 主循环：大按钮=扫荡 → 点
    SWEEP_DIALOG,        # 主循环：扫荡弹窗
    SWEEP_DIALOG,        # 点「扫荡」确认
    SELECT_SWEPT,        # 回选关页
    SELECT_SWEPT,        # 主循环：_swept → done
])
t5 = _make_task(bot5)
r5 = t5.run()
check('直扫荡流程返回 done', r5 == 'done', f'result={r5}')
check('未点过「挑战」', '挑战_大按钮' not in bot5.clicked, str(bot5.clicked))
check('未点过「自动挑战」', '自动挑战' not in bot5.clicked, str(bot5.clicked))

# ═══════════════════════════════════════════════════════════
#  6. ⏭ 模板未命中 → 降级直接扫荡（不卡死）
# ═══════════════════════════════════════════════════════════
print('\n── 6. 模板未命中降级 ──')
bot6 = MockBot(texts_by_call=[
    SELECT_SWEPT, SWEEP_DIALOG, SWEEP_DIALOG, SELECT_SWEPT, SELECT_SWEPT,
], template_hit=False)
t6 = _make_task(bot6)
r6 = t6.run()
check('模板未命中仍完成扫荡 done', r6 == 'done', f'result={r6}')
check('未发生 _click_abs（模板未命中）',
      not any('_click_abs' in c for c in bot6.calls))

# ═══════════════════════════════════════════════════════════
#  7. unknown 降级链：超限 → 返回键兜底 → 无效 failed
# ═══════════════════════════════════════════════════════════
print('\n── 7. unknown 放弃 ──')
# probe 用选关页帧直通主循环，之后全部乱码帧 → unknown 降级链
bot7 = MockBot(
    texts_by_call=[SELECT_CHALLENGE] + [['乱码画面']] * 14,
    fallback_texts=['乱码画面'],
    back_hit=False,
)
t7 = _make_task(bot7)
r7 = t7.run()
check('unknown 超限且返回键无效 → failed', r7 == 'failed', f'result={r7}')
check('走过 ESC 重试', any('press_esc' in c for c in bot7.calls))
check('走过返回键兜底', 'click_back_button' in bot7.calls)

# ═══════════════════════════════════════════════════════════
#  8. 停止信号 / max_time
# ═══════════════════════════════════════════════════════════
print('\n── 8. 停止与上限 ──')
bot8 = MockBot(phases=['home'])
t8 = _make_task(bot8, stop=True)
check('停止信号 → stopped', t8.run() == 'stopped')

bot9 = MockBot(texts_by_call=[SELECT_SWEPT] * 30)
t9 = _make_task(bot9, max_time=0.01)
import time as _time
_time.sleep(0.02)
r9 = t9.run()
check('max_time 达标 → done（不中断队列）', r9 == 'done', f'result={r9}')

# ═══════════════════════════════════════════════════════════
#  9. 扫荡完成判定（v1.8.23：真机 9/6"无法识别扫荡完成画面"）
#     扫荡完成后的选关页与扫荡前视觉一致 → 旧逻辑无限重点「扫荡」。
#     新逻辑：点扫荡后限时等弹窗，连续 2 次弹不出 → 次数用尽 → done
# ═══════════════════════════════════════════════════════════
print('\n── 9. 扫荡完成判定（v1.8.23） ──')

# 9a 真机 bug2 帧词表（魔力秘藏页，已达上限+扫荡按钮）→ select_page
GODDESS_SWEPT_DONE = [
    '魔', '力秘藏', '战斗指南', '1今日掉落：', '合成魔饰', '魔饰总览',
    '已达今日可挑战层数上限', '上限', '扫荡掉落', '首通', '扫荡',
    '详情', '明日', '60',
]
bot9a = MockBot()
t9a = _make_task(bot9a)
check('9a 扫荡完成帧（已达上限+扫荡钮）→ select_page',
      t9a._detect_state(mk(GODDESS_SWEPT_DONE)) == 'select_page',
      str(t9a._detect_state(mk(GODDESS_SWEPT_DONE))))
check('9b 战斗指南含战斗+秘藏但无自动挑战 → 不误判 battle_prep',
      t9a._detect_state(mk(GODDESS_SWEPT_DONE)) != 'battle_prep')

# 9c 次数用尽：反复点扫荡弹不出弹窗 → 2 次后判完成 → done
bot9c = MockBot(texts_by_call=[SELECT_SWEPT], fallback_texts=SELECT_SWEPT)
t9c = _make_task(bot9c)
r9c = t9c.run()
check('9c 弹窗始终不出现 → 判扫荡完成返回 done', r9c == 'done',
      f'result={r9c}')
n_sweep_clicks = sum(1 for c in bot9c.clicked if c == '扫荡_大按钮')
check('9c 只点了 2 次扫荡大按钮（不多点）', n_sweep_clicks == 2,
      f'实际 {n_sweep_clicks}')
check('9c 未进入扫荡弹窗流程（无 ⏭ 模板调用）',
      not any('btn_sweep_max' in c for c in bot9c.calls))

# 9d 正常扫荡回归：点扫荡 → 弹窗出现 → 完整扫荡流程 → done
bot9d = MockBot(texts_by_call=[
    SELECT_SWEPT,        # probe：选关页
    SELECT_SWEPT,        # 主循环 select_page → 点扫荡
    SWEEP_DIALOG,        # 等弹窗轮询命中 → 计数清零
    SWEEP_DIALOG,        # 主循环 sweep_dialog → ⏭ + 扫荡确认
    SELECT_SWEPT,        # 等回选关页 → _swept=True
    SELECT_SWEPT,        # 主循环 select_page & _swept → done
])
t9d = _make_task(bot9d)
r9d = t9d.run()
check('9d 弹窗正常出现 → 走完整扫荡流程 done（回归）', r9d == 'done',
      f'result={r9d}')
check('9d ⏭ 模板被调用（弹窗流程执行）',
      any('btn_sweep_max' in c for c in bot9d.calls))

# ═══════════════════════════════════════════════════════════
#  10. 扫荡次数徽记判别（v1.8.28：用户提供 扫荡前/扫荡后.png——
#      6/1 白字 OCR 可读 ↔ 0/1 灰字读不出，判别规则反转）
# ═══════════════════════════════════════════════════════════
print('\n── 10. 扫荡次数徽记判别（v1.8.28） ──')
it10 = lambda cy, cx, t, c=0.9: (cy, cx, t, c, cx - 40, cx + 40)
BTN10 = (687, 855)
t10 = _make_task(MockBot())
items_pre = [it10(855, 687, '扫荡'), it10(886, 690, '③6/1', 0.67)]
check('10a 扫荡前徽记 ③6/1 → 剩余 6',
      t10._read_sweep_remaining(items_pre, BTN10) == 6,
      str(t10._read_sweep_remaining(items_pre, BTN10)))
items_post = [it10(855, 687, '扫荡'), it10(886, 690, '③1', 0.67)]
check('10b 扫荡后徽记 ③1（0/1 灰字噪声）→ None',
      t10._read_sweep_remaining(items_post, BTN10) is None,
      str(t10._read_sweep_remaining(items_post, BTN10)))
items_far = [it10(855, 687, '扫荡'), it10(300, 100, '3/1')]
check('10c 远处 X/1 不误读（位置过滤）',
      t10._read_sweep_remaining(items_far, BTN10) is None)
check('10d toast「今日已无扫荡次数」→ 检测命中',
      t10._sweep_exhausted_toast([it10(500, 640, '今日已无扫荡次数。')]))
check('10e 无 toast → 不误报',
      not t10._sweep_exhausted_toast([it10(500, 640, '别的文字')]))

# 10f 端到端：0/1 徽记（读不出）+ 点扫荡无弹窗 → 2 次后 done（v1.8.23 兜底）
bot10f = MockBot(texts_by_call=[SELECT_SWEPT], fallback_texts=SELECT_SWEPT)
t10f = _make_task(bot10f)
r10f = t10f.run()
check('10f 0/1 态端到端 → 判定扫荡完成 done', r10f == 'done', f'result={r10f}')

# ═══════════════════════════════════════════════════════════
print('\n' + '─' * 54)
if _FAIL:
    print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
    print(f'  ❌ 失败: {_FAIL}')
    sys.exit(1)
print(f'  通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)}')
print('  ✅ 女神试炼干跑测试全部通过')
sys.exit(0)
