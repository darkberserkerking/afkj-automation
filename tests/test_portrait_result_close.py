# -*- coding: utf-8 -*-
"""v2.3.41 回归：竖屏结算页死循环（假成功 + 漏点按钮）。

真机 2026-11-06 20:31（750x1334 竖屏）连转 42 轮不退出，用户手动停止。
现场截图 MaaOCR 实测：
  「挂机收益提升」(376,862) conf .99  ← 结算页特有副标题
  「赛季挂机进度」(125,918) conf .99
  「幻灵挑战」(536,1248) conf .99     ← 底部横幅（用户确认点它回备战）
  无「点击空白处关闭」/「×」/独立「挑战」按钮

两个根因（本测试各自断言）：
  ① 找按钮条件 `'幻灵' not in _t` 把「幻灵挑战」排除 → 打印「未找到按钮」
  ② 判「已回到挂机关卡页」只看「幻灵挑战」→ 结算页底部就有 → 每轮假成功

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_portrait_result_close.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot.auto_farm.battle import BattleMonitor

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def it(cy, cx, text, conf=0.99):
    return (cy, cx, text, conf, cx - 10, cx + 10)


# ── 真机 20:31 竖屏结算页实测文案/坐标 ─────────────────────
PORTRAIT_RESULT = [
    it(862, 376, '挂机收益提升'),
    it(918, 125, '赛季挂机进度'),
    it(966, 186, '16732/小时》16733/小时'),
    it(1026, 484, '最高等级 800级'),
    it(1090, 382, '已达4天后极限速率'),
    it(1248, 536, '幻灵挑战'),      # 底部横幅：唯一可点
    it(1294, 541, '90亿'),
]

# 真正的挂机关卡页（有「挑战」但无结算特征）
IDLE_STAGE = [
    it(900, 640, '挑战'),
    it(700, 400, '幻灵挑战'),
]

# 结算特征但 OCR 漏读按钮（应继续尝试，不得假成功）
RESULT_NO_BTN = [
    it(862, 376, '挂机收益提升'),
    it(918, 125, '赛季挂机进度'),
]


class _Screen:
    def __init__(self, items):
        self._items = items
        self.snap_calls = 0

    def snap(self):
        self.snap_calls += 1
        return (None, 0, 0, 750, 1334)

    def _ocr_img(self, img):
        return list(self._items)


class _Input:
    def __init__(self):
        self.clicks = []

    def click_at(self, x, y, label=''):
        self.clicks.append(('click_at', x, y, label))

    def click_abs(self, x, y):
        self.clicks.append(('click_abs', x, y, ''))

    def click_ratio_dual(self, landscape, portrait, label=''):
        self.clicks.append(('ratio_dual', landscape, portrait, label))

    def send_esc(self):
        self.clicks.append(('esc', 0, 0, ''))


def make_monitor(items, battle_mode='manual'):
    inp = _Input()
    logs = []
    mon = BattleMonitor(
        screen_capture=_Screen(items),
        recognizer=None,
        detector=None,
        input_controller=inp,
        stats_tracker=None,
        log_callback=lambda m: logs.append(str(m)),
        status_callback=None,
        config={'battle': {'max_attempts_close_result': 3}},
        stop_check=lambda: False,
        pause_check=lambda: True,
        battle_mode=battle_mode,
    )
    return mon, inp, logs


def run(items, battle_mode='manual'):
    mon, inp, logs = make_monitor(items, battle_mode)
    res = mon.step_close_result(max_attempts=3)
    return res, inp, logs


# ══ 1. 竖屏结算页：必须点「幻灵挑战」而不是打印未找到 ──────
print('── 1. 竖屏结算页（真机 20:31）──')
res1, inp1, logs1 = run(PORTRAIT_RESULT)
clicks1 = [c for c in inp1.clicks if c[0] == 'click_at']
check('1a 点到了「幻灵挑战」按钮',
      any(c[3] == '幻灵挑战' for c in clicks1),
      str(clicks1[:3]))
check('1b 点击坐标 = 真机实测 (536,1248)',
      any(c[1] == 536 and c[2] == 1248 for c in clicks1),
      str([(c[1], c[2]) for c in clicks1]))
check('1c 不再打印「未找到按钮」',
      not any('未找到按钮' in l for l in logs1),
      str([l for l in logs1 if '未找到' in l]))
check('1d 返回 True（点完让主循环重判阶段）', res1 is True, str(res1))
check('1e 没有走「点空白处」（全屏页点空白无效）',
      not any(c[0] == 'ratio_dual' for c in inp1.clicks),
      str(inp1.clicks[:2]))

# ══ 2. 假成功防护：结算特征在 → 不得判「已回到挂机关卡」──
print('\n── 2. 假成功防护 ──')
res2, inp2, logs2 = run(RESULT_NO_BTN)
check('2a 漏读按钮时不得假成功 return True', res2 is False, str(res2))
check('2b 不得打印「已回到挂机关卡」',
      not any('已回到挂机关卡' in l for l in logs2),
      str([l for l in logs2 if '回到' in l]))

# ══ 3. 真·挂机关卡页仍正确判定（不误伤）────────────────────
print('\n── 3. 挂机关卡页不误伤 ──')
res3, inp3, logs3 = run(IDLE_STAGE)
check('3a 真的挂机关卡页 → return True', res3 is True, str(res3))
check('3b 打印「已回到挂机关卡」',
      any('已回到挂机关卡' in l for l in logs3),
      str([l for l in logs3 if '回到' in l]))
check('3c 该页无结算特征 → 不点按钮',
      not any(c[0] == 'click_at' for c in inp3.clicks),
      str(inp3.clicks[:2]))

# ══ 4. 横屏原有行为不回退（有独立「挑战」按钮优先）──────────
print('\n── 4. 横屏按钮优先级 ──')
LAND = [it(900, 640, '挑战'), it(862, 376, '挂机收益提升')]
res4, inp4, logs4 = run(LAND)
clicks4 = [c for c in inp4.clicks if c[0] == 'click_at']
check('4a 优先点独立「挑战」而非幻灵挑战',
      any(c[3] == '挑战' for c in clicks4), str(clicks4[:2]))
check('4b 横屏仍 return True', res4 is True, str(res4))


# ══ 5. v2.3.42 真因回归：竖屏读不出艺术字「战斗胜利」 ═════════
#   用户 20:58 提供同画面横竖屏两张对照截图，项目 MaaOCR 实测：
#     横屏 1279x1029「战斗胜利」conf 0.96（0.3/0.1/0.0 均命中）
#     竖屏 757x1367「战斗胜利」三档阈值全部 0 命中
#   旧代码「胜利结算界面」分支只认「战斗胜利」→ 竖屏不命中 →
#   掉到「已回到挂机关卡」（用「幻灵挑战」判定）→ 假成功。
print('\n── 5. 竖屏缺艺术字不得掉进假成功 ──')

# 5a 竖屏真实 OCR 形态：无「战斗胜利」，有「挂机收益提升」+「幻灵挑战」
res5, inp5, logs5 = run(PORTRAIT_RESULT)
check('5a 读不出艺术字时仍优先点「幻灵挑战」（而非假成功）',
      any(c[3] == '幻灵挑战' for c in inp5.clicks if c[0] == 'click_at'),
      str(inp5.clicks[:2]))
check('5b 不得出现「已回到挂机关卡」假成功',
      not any('已回到挂机关卡' in l for l in logs5),
      str([l for l in logs5 if '回到' in l]))

# 5c 有副标题但无按钮（且无幻灵挑战）→ 应走「点空白」，不是直接 return
RESULT_SUBTITLE_ONLY = [
    it(862, 376, '挂机收益提升'),
    it(918, 125, '赛季挂机进度'),
    it(1026, 484, '最高等级 800级'),
]
res5c, inp5c, logs5c = run(RESULT_SUBTITLE_ONLY)
check('5c 只有副标题 → 走点空白（结算分支已认副标题兜底）',
      any(c[0] == 'ratio_dual' for c in inp5c.clicks),
      str(inp5c.clicks[:2]))
check('5d 同理不得假成功', res5c is False, str(res5c))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
