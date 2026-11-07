# -*- coding: utf-8 -*-
"""v2.4.3：推荐阵容为空 → 直接切换挑战类型（只动自动推图）。

真机（2026-11-07 用户截图）：推荐阵容页顶部灰字「暂未有符合条件的录相」，
同屏「一键采用」按钮**置灰**。旧流程照样 OCR 到「一键采用」→ 点 5 次
（无效）→ break → 按 F 用默认阵容硬打，与「推荐阵容用完 → 切挑战类型」
的既有策略不一致。用户要求：一个阵容都没有 → 直接切幻灵推图/普通推图。

本测试覆盖：
  A. detect_recommend_empty 判据真值表（含误伤防护、置信度过滤）
  B. prepare_and_start_battle：空 → 不点按钮、不下场、置标志
  C. allow_empty_rec=True：上层兜底时照常下场（避免两模式互踢）
  D. loop.py 接线（标志消费点在 formation 分支、翻转 mode、二次退让）

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_rec_empty_switch.py
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import afk_bot.auto_farm.formation as F

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# ── 桩件 ────────────────────────────────────────────────────

class _FakeScreen:
    """按帧序列返回 OCR 结果；耗尽后保持最后一帧。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self.n = 0
        self.activated = 0

    def ocr_screen(self, **kw):
        f = self._frames[min(self.n, len(self._frames) - 1)]
        self.n += 1
        return f

    def win_dims(self):
        return (0, 0, 1280, 720)

    def activate_window(self):
        self.activated += 1


class _FakeRecognizer:
    """find_text：包含匹配，返回首个命中框中心 (cx, cy)。"""

    def find_text(self, items, keyword, min_conf=0.5):
        for cy, cx, t, c, *_ in items:
            if isinstance(t, str) and keyword in t and c >= min_conf:
                return (cx, cy)
        return None

    def find_text_exclude(self, items, keyword, exclude_keywords, min_conf=0.5):
        for cy, cx, t, c, *_ in items:
            if not isinstance(t, str) or c < min_conf:
                continue
            if keyword in t and not any(k in t for k in exclude_keywords):
                return (cx, cy)
        return None


class _FakeInput:
    def __init__(self):
        self.clicks = []

    def click_at(self, x, y, label=''):
        self.clicks.append(label)

    def send_esc(self):
        self.clicks.append('<ESC>')


def _items(*pairs):
    """(文本, 置信度) → OCR items 元组 (cy, cx, text, conf, x0, x1)。"""
    out = []
    for i, p in enumerate(pairs):
        t, c = p if isinstance(p, tuple) else (p, 0.92)
        out.append((200 + i * 40, 300 + i * 90, t, c, 250, 350))
    return out


def make_mgr(frames, battle_mode='manual'):
    screen = _FakeScreen(frames)
    rec = _FakeRecognizer()
    inp = _FakeInput()
    logs = []
    mgr = F.FormationManager(
        screen_capture=screen,
        recognizer=rec,
        input_controller=inp,
        detector=SimpleNamespace(detect_phase=lambda: 'formation'),
        log_callback=lambda m: logs.append(m),
        battle_mode=battle_mode,
    )
    return mgr, screen, inp, logs


# 让 _sleep 不真等（分段可中断实现在 timing 里）
_sleeps = []
F.sleep_interruptible = lambda sec, cb: (_sleeps.append(sec), True)[1]


# ── A. 判据真值表 ───────────────────────────────────────────
print('── A. detect_recommend_empty 判据 ──')
try:
    mgr, _, _, _ = make_mgr([[]])
    cases = [
        ('A1 真机原句「暂未有符合条件的录相」→ 空', '暂未有符合条件的录相', True),
        ('A2 OCR 读成「录像」也命中', '暂未有符合条件的录像', True),
        ('A3 掉字但「暂未+符合」同句 → 空', '暂未有符合条�的录相', True),
        ('A4 正常面板「一键采用/阵容匹配」→ 非空', '一键采用', False),
        ('A5 「暂无邮件」等无关文案不得误伤', '暂无邮件', False),
        ('A6 「未拥有该英雄」弹窗文案不得误伤', '未拥有该英雄', False),
    ]
    for name, text, want in cases:
        got = mgr.detect_recommend_empty(_items(text))
        check(name, got is want, f'got={got}')

    # 置信度过滤：灰字提示读成 0.15 的低分噪声时不应生效
    low = mgr.detect_recommend_empty(
        [(300, 400, '暂未有符合条件的录相', 0.15, 380, 420)]
    )
    check('A7 conf 0.15 的弱噪声不判空（避免误伤）', low is False, f'got={low}')

    # 同帧混排：正常文案 + 空提示 → 空优先
    mixed = mgr.detect_recommend_empty(
        _items(('一键采用', 0.9), ('暂未有符合条件的录相', 0.88))
    )
    check('A8 同帧混排仍判空', mixed is True, f'got={mixed}')

    # items=None 时自行取帧
    mgr2, screen2, _, _ = make_mgr([_items('暂未有符合条件的录相')])
    check('A9 items=None 时自取一帧', mgr2.detect_recommend_empty() is True,
          f'ocr_calls={screen2.n}')
except Exception as _e:
    check('A 判据真值表', False, repr(_e))

# ── B. 空阵容 → 不点、不下场、置标志 ───────────────────────
print('── B. prepare_and_start_battle：推荐阵容为空 ──')
try:
    sent = []
    _orig_kb = F.keyboard
    F.keyboard = SimpleNamespace(send=lambda k: sent.append(k))

    empty_frame = _items(
        ('通关阵容', 0.95), ('一键采用', 0.90),
        ('暂未有符合条件的录相', 0.92),
    )
    mgr, screen, inp, logs = make_mgr([empty_frame])
    r = mgr.prepare_and_start_battle(switch_count=0, battle_mode='manual')
    check('B1 返回 False（由上层切挑战类型）', r is False, f'r={r!r}')
    check('B2 置 _no_recommended_formation=True',
          mgr._no_recommended_formation is True, str(mgr._no_recommended_formation))
    check('B3 未点击「一键采用」（置灰按钮点了也白点）',
          '一键采用' not in inp.clicks, str(inp.clicks))
    check('B4 未按 F 下场（不该用默认阵容硬打）',
          sent == [], str(sent))
    check('B5 日志说明原因',
          any('推荐阵容为空' in m for m in logs), str(logs[-2:]))

    # 重入：标志必须被重置（否则切过去后仍带旧标志）
    mgr._no_recommended_formation = True
    mgr._screen = _FakeScreen([_items(('通关阵容', .95), ('一键采用', .9))])
    mgr._input = _FakeInput()
    mgr.prepare_and_start_battle(switch_count=0, battle_mode='manual')
    check('B6 每次备战重置标志（旧标志不残留）',
          mgr._no_recommended_formation is False,
          str(mgr._no_recommended_formation))
finally:
    F.keyboard = _orig_kb

# ── C. allow_empty_rec 兜底：照常下场 ─────────────────────
print('── C. allow_empty_rec=True 兜底 ──')
try:
    sent2 = []
    F.keyboard = SimpleNamespace(send=lambda k: sent2.append(k))
    mgr, screen, inp, logs = make_mgr([empty_frame])
    r = mgr.prepare_and_start_battle(
        switch_count=0, battle_mode='manual', allow_empty_rec=True
    )
    check('C1 兜底模式下仍返回 True（两种挑战都空时不卡死）', r is True, f'r={r!r}')
    check('C2 兜底模式点了「一键采用」', '一键采用' in inp.clicks, str(inp.clicks))
    check('C3 兜底模式按 F 下场', sent2 == ['f'], str(sent2))
finally:
    F.keyboard = _orig_kb

# ── D. loop.py 接线 ────────────────────────────────────────
print('── D. loop.py 接线 ──')
try:
    with open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
              encoding='utf-8') as f:
        src = f.read()

    _form_at = src.index("elif phase == 'formation':")
    _idle_at = src.index("elif phase == 'idle_stage':")
    _consume_at = src.index("'_no_recommended_formation'")
    check('D1 标志消费点在 formation 分支内',
          _form_at < _consume_at < _idle_at,
          f'form={_form_at} consume={_consume_at} idle={_idle_at}')

    _seg = src[_consume_at:_idle_at]
    check('D2 命中后调 navigate_to_other_challenge',
          'navigate_to_other_challenge' in _seg)
    check('D3 命中后翻转 current_mode（幻灵↔普通）',
          "else 'huanling'" in _seg and "'normal' if current_mode == 'huanling'" in _seg)
    check('D4 命中后不等待战斗（continue 跳出本轮）',
          'continue' in _seg)
    check('D5 二次退让：两种都空 → allow_empty_rec=True 照常打',
          'allow_empty_rec=True' in _seg)
    check('D6 计数器在 __init__ 初始化（_credit_battle 可直接重置）',
          'self._empty_rec_switch_count: int = 0' in src)
    check('D7 胜利后清零计数（新关卡重新给两次机会）',
          src.count('self._empty_rec_switch_count = 0') >= 3,
          f'count={src.count("self._empty_rec_switch_count = 0")}')

    # 边界：只改自动推图，女神/天穹任务不得受牵连
    tq = os.path.join(ROOT, 'tasks', 'tianqiong_trial.py')
    with open(tq, encoding='utf-8') as f:
        tq_src = f.read()
    check('D8 天穹任务未被改动（只动自动推图）',
          '_no_recommended_formation' not in tq_src
          and 'detect_recommend_empty' not in tq_src)
except Exception as _e:
    check('D loop 接线', False, repr(_e))

# ── 汇总 ───────────────────────────────────────────────────
print('')
print(f'通过 {PASSED[0]} / {PASSED[0] + len(FAILED)}')
if FAILED:
    print('失败用例：')
    for n in FAILED:
        print(' -', n)
    sys.exit(1)
print('ALL PASS')
