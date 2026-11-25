# -*- coding: utf-8 -*-
"""卡住检测回归（v2.4.26）。

背景：用户澄清真机 2026-11-25 21:49:56~21:50:07 连点 3 次「玩法目录」
不是缓存问题，而是**低配机器上游戏卡住了**。

复查发现真正缺陷：`stuck` 配置（sample_interval/threshold/
max_recovery_attempts）与 `facade._try_recover_from_stuck()` 都写好了，
但**主循环从未调用** —— 死代码。于是游戏卡死时程序只会每 5 秒重复点同一处。

判据选择：用**画面指纹连续不变**，而不是「同坐标重复点击」——
切换阵容时会正常连点 6 次同一个 `>` 按钮，按坐标判据必然误判。

守住四条：
  ① 画面一变就重置计数（正常推进不误判）
  ② 连续 N 轮不变 → 触发恢复（ESC + 重新导航）
  ③ 恢复次数用完 → 只告警不再动手（避免反复 ESC 搅乱正常流程）
  ④ 主循环确实调用了它（防再次变成死代码）
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afk_bot.auto_farm.loop import AFKBotCore

FAILED = []
PASSED = [0]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


class _Screen:
    def __init__(self, fps):
        self.fps = list(fps)
        self.i = 0
        self.shots = []

    def frame_fingerprint(self):
        v = self.fps[min(self.i, len(self.fps) - 1)]
        self.i += 1
        return v

    def debug_screenshot(self, tag):
        self.shots.append(tag)


class _Nav:
    def __init__(self):
        self.esc = 0
        self.nav = 0
        self.fail = False

    def esc_to_level0(self):
        self.esc += 1

    def navigate_to_idle_stage(self):
        self.nav += 1
        if self.fail:
            raise RuntimeError('模拟导航失败')
        return True


class _Fake:
    def __init__(self, fps, threshold=3, max_rec=2):
        self._screen_capture = _Screen(fps)
        self._navigator = _Nav()
        self._stop_event = threading.Event()
        self._cfg = {'stuck': {'threshold': threshold,
                               'max_recovery_attempts': max_rec}}
        self._stuck_fp = None
        self._stuck_count = 0
        self._stuck_recoveries = 0
        self.logs = []

    def _log(self, level, msg):
        self.logs.append((level, msg))

    def tick(self):
        return AFKBotCore._check_stuck_and_recover(self)


# ── ① 画面变化 → 不计数 ────────────────────────────────────
f = _Fake([1, 2, 3, 4])
res = [f.tick() for _ in range(4)]
check('1a 画面持续变化时永不触发', not any(res), str(res))
check('1b 计数始终为 0', f._stuck_count == 0, str(f._stuck_count))
check('1c 未执行任何恢复动作',
      f._navigator.esc == 0 and f._navigator.nav == 0)

# ── ② 连续不变 → 触发恢复 ─────────────────────────────────
# 第 1 次制定基准，第 2/3 次累计到 2，第 4 次到 3 时触发
f = _Fake([9, 9, 9, 9, 9], threshold=3, max_rec=2)
res = [f.tick() for _ in range(4)]
check('2a 前 3 次不触发', res[:3] == [False, False, False], str(res[:3]))
check('2b 第 4 次触发恢复', res[3] is True, str(res))
check('2c 执行了 ESC + 重新导航',
      f._navigator.esc == 1 and f._navigator.nav == 1,
      f'esc={f._navigator.esc} nav={f._navigator.nav}')
check('2d 落了卡住截图（便于事后排查）', f._screen_capture.shots == ['stuck'],
      str(f._screen_capture.shots))
check('2e 日志明确说明「判定卡住」',
      any('判定卡住' in m for _, m in f.logs), str(f.logs[-1:]))
check('2f 恢复后计数清零', f._stuck_count == 0 and f._stuck_fp is None)

# ── ③ 恢复次数上限 ────────────────────────────────────────
f = _Fake([7] * 12, threshold=2, max_rec=1)
for _ in range(12):
    f.tick()
check('3a 超过 max_recovery_attempts 后不再恢复',
      f._navigator.esc == 1 and f._navigator.nav == 1,
      f'esc={f._navigator.esc} nav={f._navigator.nav}')
check('3b 打了「不再自动恢复」的告警',
      any('不再自动恢复' in m for _, m in f.logs),
      str([m for _, m in f.logs if '恢复' in m][-1:]))

# ── ④ 恢复失败不抛 ────────────────────────────────────────
f = _Fake([5, 5, 5], threshold=2, max_rec=1)
f._navigator.fail = True
ok = True
try:
    for _ in range(3):
        f.tick()
except Exception as e:
    ok = False
    print('   异常：', e)
check('4a 导航失败时异常被吞掉（不中断任务）', ok)

# ── ⑤ 指纹取值异常时不崩 ──────────────────────────────────
class _BadScreen:
    def frame_fingerprint(self):
        raise RuntimeError('截图失败')

f2 = _Fake([1])
f2._screen_capture = _BadScreen()
ok2 = True
try:
    r = f2.tick()
except Exception:
    ok2 = False
    r = None
check('5a 取指纹异常时安全返回 False', ok2 and r is False, f'r={r}')

# ── ⑥ 源码约束：不能再次变成死代码 ────────────────────────
src = open(os.path.join(ROOT, 'afk_bot/auto_farm/loop.py'),
           encoding='utf-8').read()
check('6a 定义了 _check_stuck_and_recover',
      'def _check_stuck_and_recover' in src)
check('6b 主循环内确实调用了它',
      'if self._check_stuck_and_recover():' in src)
check('6c 判据用画面指纹而非点击坐标',
      'frame_fingerprint()' in src and '同坐标' not in src.split(
          'def _check_stuck_and_recover')[1].split('def _run_main_loop')[0])

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
