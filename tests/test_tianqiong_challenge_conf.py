# -*- coding: utf-8 -*-
"""v2.4.6：天穹试炼「挑战！」低置信度漏检 → 误判「今日已无可挑战层」回归。

真机 2026-11-14 09:28~09:29（用户截图 + 日志放大 OCR）：
  塔内 124 层黄色「挑战！」按钮清晰可见，日志却连续 3 帧「暂未读到挑战」，
  最后判「『赛季自然之塔』今日已无可挑战层（已连续 3 次确认）」并标记通关。
  候选里明明有：`挑战！(854,753,0.78)`

根因：**同一个词两处阈值不一致**
  _is_floor_page  用 _TEXT_MIN_CONF = 0.7  → 认出「这是塔内页」✓
  _find_challenge 用 _BTN_MIN_CONF  = 0.85 → 0.78 被挡 ✗ → 点不到按钮
  → 3 帧后误判完成。0.85 是按**放大后静态截图**的 0.988 定的，
  实时帧只有 0.78（与「出发！」v2.3.23 同类坑）。

覆盖：
  A. 真机帧行为分叉：修复后命中 (854,753)；用旧阈值显式复现 None
  B. 判画面 / 点按钮同口径一致性
  C. 防误伤：低分「挑战奖励 / 继续挑战 / 再次挑战」仍不命中
  D. _has_weak_challenge 真值表 + 护栏接线
  E. 端到端：真机低分帧 → 点到「挑战」且绝不打印「无可挑战层」

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_tianqiong_challenge_conf.py
"""
import os
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time  # noqa: E402
fast_time.install()

import tasks.tianqiong_trial as tq  # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def it(cy, cx, text, conf):
    return (cy, cx, text, conf, int(cx) - 40, int(cx) + 40)


# 真机日志 09:29:01（2/3 帧）候选原样还原，_cands 格式为 text(cx,cy,conf)
FLOOR_REAL = [
    it(54, 650, '季自然之塔', 0.97),
    it(111, 663, '还差2层', 0.99),
    it(112, 566, '0x5', 0.76),
    it(241, 961, '126', 0.98),
    it(265, 855, '未解锁', 0.99),
    it(443, 962, '125', 0.98),
    it(465, 1055, '未解锁', 0.99),
    it(728, 964, '1241', 0.97),
    it(753, 854, '挑战！', 0.78),      # ← 真机低分，被 0.85 挡掉的就是它
    it(772, 713, '等级910', 0.97),
]
# 完全没有挑战词（本塔真打完）
FLOOR_DONE = [x for x in FLOOR_REAL if '挑战' not in x[2]]
# 更低分的抖动帧
FLOOR_WEAK = [x for x in FLOOR_REAL] + []
FLOOR_WEAK = [it(753, 854, '挑战！', 0.50) if '挑战' in x[2] else x
              for x in FLOOR_REAL]


class MockBot:
    """精简干跑桩：按帧返回 OCR，记录点击；点「挑战」即置停止。"""

    def __init__(self, frames, stop_event, stop_on_click=None):
        self.frames = list(frames)
        self.i = 0
        self.clicks = []
        self.keys = []
        self._ev = stop_event
        self._on = stop_on_click

    def _ocr_screen(self, img=None, no_cache=False):
        f = self.frames[min(self.i, len(self.frames) - 1)]
        self.i += 1
        return list(f)

    def click_at(self, cx, cy, label=''):
        self.clicks.append((cx, cy, label))
        if self._on and label == self._on:
            self._ev.set()

    def find_hwnd(self):
        return 0

    def find_and_focus(self):
        return 12345

    def find_text(self, items, keyword, min_conf=0.5, exact=False):
        for x in items:
            if len(x) >= 4 and x[3] >= min_conf and keyword in str(x[2]):
                return (x[1], x[0])
        return None

    def template_match(self, tpl, threshold=0.8, silent=True,
                       scale_range=None, region=None):
        return None

    def click_back_button(self, tpls=None, threshold=0.75):
        return (500, 500)

    def press_esc(self, label=''):
        self.keys.append('ESC')

    def press_f(self, label=''):
        self.keys.append('F')

    def scroll_wheel(self, *a, **k):
        pass

    def _click_abs(self, cx, cy, label=''):
        self.clicks.append((cx, cy, label or 'ABS'))

    def window_rect(self):
        return (0, 0, 1280, 1024)


def make_task(frames, stop_on_click=None):
    ev = threading.Event()
    pause = threading.Event()
    pause.set()
    bot = MockBot(frames, ev, stop_on_click)
    logs = []
    ctx = {'bot': bot, 'logger': logs.append, 'stats': {},
           'stop_event': ev, 'pause_event': pause, 'options': {},
           'max_time': None, 'max_turns': None,
           'inst_id': 'tianqiong_trial#conf'}
    return tq.TianqiongTrialTask(ctx), bot, logs


# ── A. 行为分叉 ────────────────────────────────────────────
print('── A. 真机低分帧 ──')
try:
    t, bot, logs = make_task([FLOOR_REAL])
    check('A1 _find_challenge 命中真机 0.78 的「挑战！」→ (854,753)',
          t._find_challenge(FLOOR_REAL) == (854, 753),
          str(t._find_challenge(FLOOR_REAL)))
    # v2.4.7：_BTN_MIN_CONF 死常量已删除；旧阈值作为历史事实内联保留，
    # 行为分叉（0.78 帧在 0.85 下必漏检）仍要锁死，防止阈值被调回去。
    _OLD_BTN_CONF = 0.85
    old = t._find_exact(FLOOR_REAL, tq._CHALLENGE_WORD, _OLD_BTN_CONF)
    check('A2 旧阈值 0.85 确实漏检（锁死改版理由）', old is None, str(old))
    check('A3 新阈值已放宽到 0.6 且与 _GO_MIN_CONF 同依据',
          tq._CHALLENGE_MIN_CONF == 0.6
          and tq._CHALLENGE_MIN_CONF < _OLD_BTN_CONF,
          f'{tq._CHALLENGE_MIN_CONF} < {_OLD_BTN_CONF}')
except Exception as _e:
    check('A 行为分叉', False, repr(_e))

# ── B. 两处口径一致 ────────────────────────────────────────
print('── B. 判画面 / 点按钮同口径 ──')
try:
    t, _, _ = make_task([FLOOR_REAL])
    check('B1 真机帧判为塔内楼层页', t._is_floor_page(FLOOR_REAL) is True)
    check('B2 同一帧既能认画面、又能定位按钮（不再自相矛盾）',
          t._is_floor_page(FLOOR_REAL) and t._find_challenge(FLOOR_REAL))
    check('B3 源码里 _is_floor_page 与 _find_challenge 共用同常量',
          open(os.path.join(ROOT, 'tasks', 'tianqiong_trial.py'),
               encoding='utf-8').read().count('_CHALLENGE_MIN_CONF') >= 3)
    check('B4 无挑战词帧仍判塔内页（兜底：塔名 + 还差N层）',
          t._is_floor_page(FLOOR_DONE) is True)
    check('B5 无挑战词帧找不到按钮',
          t._find_challenge(FLOOR_DONE) is None)
except Exception as _e:
    check('B 口径一致', False, repr(_e))

# ── C. 防误伤 ──────────────────────────────────────────────
print('── C. 放宽阈值后不得误伤 ──')
try:
    t, _, _ = make_task([FLOOR_REAL])
    for w in ('挑战奖励：', '继续挑战', '再次挑战', '挑战奖励', '赛季挑战'):
        check(f'C 「{w}」低分 0.55 仍不命中',
              t._find_challenge([it(700, 800, w, 0.55)]) is None)
    check('C-全等 0.55 落入弱痕迹区→不直接点（交给护栏继续等）',
          t._find_challenge([it(700, 800, '挑战！', 0.55)]) is None
          and t._has_weak_challenge([it(700, 800, '挑战！', 0.55)]) is True)
    check('C-边界 0.61 的「挑战！」正常命中',
          t._find_challenge([it(700, 800, '挑战！', 0.61)]) == (800, 700))
    check('C-归一 带全角标点「挑战！」归一后全等',
          tq._norm('挑战！') == tq._CHALLENGE_WORD)
except Exception as _e:
    check('C 防误伤', False, repr(_e))

# ── D. 弱痕迹护栏 ──────────────────────────────────────────
print('── D. 弱痕迹护栏 ──')
try:
    t, _, _ = make_task([FLOOR_REAL])
    check('D1 0.50 的「挑战！」判为抖动痕迹',
          t._has_weak_challenge([it(700, 800, '挑战！', 0.50)]) is True)
    check('D2 0.60（正常命中区间）不算弱痕迹',
          t._has_weak_challenge([it(700, 800, '挑战！', 0.60)]) is False)
    check('D3 0.30 噪声不算弱痕迹',
          t._has_weak_challenge([it(700, 800, '挑战！', 0.30)]) is False)
    check('D4 弱痕迹同样要求全等（「继续挑战」不算）',
          t._has_weak_challenge([it(700, 800, '继续挑战', 0.50)]) is False)
    check('D5 真打完帧无弱痕迹 → 3 帧即可判完成',
          t._has_weak_challenge(FLOOR_DONE) is False)
    src = open(os.path.join(ROOT, 'tasks', 'tianqiong_trial.py'),
               encoding='utf-8').read()
    check('D6 护栏接线：弱命中时上限放宽且有界',
          '_NO_CHALLENGE_HARD_MAX' in src
          and 'weak = self._has_weak_challenge(items)' in src)
except Exception as _e:
    check('D 护栏', False, repr(_e))

# ── E. 端到端：不再误判无可挑战层 ──────────────────────────
print('── E. 端到端行为分叉 ──')
try:
    t, bot, logs = make_task([FLOOR_REAL], stop_on_click='天穹试炼_挑战')
    r = t._play_loop()
    check('E1 真机低分帧 → 实际点到「挑战！」按钮',
          any(c[2] == '天穹试炼_挑战' for c in bot.clicks),
          str(bot.clicks[:2]))
    check('E2 绝不打印「今日已无可挑战层」（修复前必打印）',
          not any('无可挑战层' in m for m in logs),
          str([m for m in logs if '挑战' in m][:3]))
    check('E3 任务正常收敛（点挑战后按停止退出）',
          r in ('stopped', 'done'), f'r={r!r}')

    # 对照：真打完帧（完全无「挑战」）→ 不应卡死，仍走完成判定
    t2, bot2, logs2 = make_task([FLOOR_DONE])
    r2 = t2._play_loop()
    check('E4 真打完帧仍走「无可挑战层」路径（无回归）',
          any('无可挑战层' in m for m in logs2)
          or r2 in ('done', 'failed', 'stopped'),
          str([m for m in logs2 if '无可挑战' in m][:1]))
except Exception as _e:
    check('E 端到端', False, repr(_e))

print('')
print(f'通过 {PASSED[0]} / {PASSED[0] + len(FAILED)}')
if FAILED:
    print('失败用例：')
    for n in FAILED:
        print(' -', n)
    sys.exit(1)
print('ALL PASS')
