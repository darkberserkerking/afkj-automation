# -*- coding: utf-8 -*-
"""关卡统计口径回归（v2.4.25）。

真机 2026-11-26 01:20~01:22（用户观察）：
    01:20:40 [数据] 当前关卡: 589（幻灵:589, 挂机:0）
    ...推荐阵容用完 → 切换到 normal...
    01:22:01 [数据] 当前关卡: 307（幻灵:0, 挂机:307）   ← 幻灵的 589 丢了

幻灵与挂机是**两套独立进度**。切换挑战类型后界面上只显示当前模式的数字，
另一模式读数为 0；旧代码在 formation 分支里直接 `max(hl, nm)`，
于是把已知进度覆盖成 0，表现为「关卡归零」。

两条不变量：
  ① 某模式本次读不到（=0）时，保留它上次的已知值，展示值不回退
  ② 两份实现（formation 分支 / 战前记录）必须共用同一方法，不允许再漂移
"""
import os
import sys

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


class _Fake:
    """只提供 _refresh_stage_stats 需要的三个依赖。"""

    def __init__(self, stats, hl, nm):
        self.stats = stats
        self._hl = hl
        self._nm = nm
        self.logs = []

    def _detect_season_stages(self):
        return self._hl, self._nm

    def _log(self, level, msg):
        self.logs.append((level, msg))


def refresh(stats, hl, nm):
    f = _Fake(stats, hl, nm)
    AFKBotCore._refresh_stage_stats(f)
    return f


# ── ① 首次读到幻灵 ─────────────────────────────────────────
st = {}
f = refresh(st, 589, 0)
check('1a 首次：current=589', st.get('current_stage') == 589, str(st))
check('1b 幻灵进度已存', st.get('huanling_stage') == 589)
check('1c 日志文案正确',
      any('589' in m and '幻灵:589' in m and '挂机:0' in m
          for _, m in f.logs), str(f.logs[-1:]))

# ── ② 切到挂机后：幻灵读数为 0，不得归零 ───────────────────
f = refresh(st, 0, 307)
check('2a 【核心】切模式后不回退（current 仍 589）',
      st.get('current_stage') == 589, str(st))
check('2b 幻灵历史值保留', st.get('huanling_stage') == 589, str(st))
check('2c 挂机进度已记录', st.get('normal_stage') == 307, str(st))
check('2d 日志同时显示两个模式的已知值',
      any('589' in m and '幻灵:589' in m and '挂机:307' in m
          for _, m in f.logs), str(f.logs[-1:]))

# ── ③ 两模式都能读到 → 取较大者 ────────────────────────────
st3 = {}
refresh(st3, 600, 307)
check('3a 两者都有时取 max', st3.get('current_stage') == 600, str(st3))
refresh(st3, 589, 650)
check('3b 挂机反超则展示挂机值', st3.get('current_stage') == 650, str(st3))

# ── ④ 都读不到 → 不改状态、不打日志 ────────────────────────
st4 = {'current_stage': 589, 'huanling_stage': 589, 'normal_stage': 0}
f = refresh(st4, 0, 0)
check('4a 两者都读不到时状态不变', st4.get('current_stage') == 589, str(st4))
check('4b 且不产生日志（避免刷屏）', f.logs == [], str(f.logs))

# ── ⑤ 真正推进才变大 ───────────────────────────────────────
st5 = {'current_stage': 589, 'huanling_stage': 589, 'normal_stage': 307}
refresh(st5, 590, 0)
check('5a 幻灵推进 → 590', st5.get('current_stage') == 590, str(st5))
refresh(st5, 0, 400)
check('5b 挂机到 400 不覆盖幻灵的 590（取 max）',
      st5.get('current_stage') == 590, str(st5))
refresh(st5, 0, 700)
check('5c 挂机反超 → 700', st5.get('current_stage') == 700, str(st5))

# ── ⑥ 源码约束：两份实现必须共用 ───────────────────────────
src = open(os.path.join(ROOT, 'afk_bot/auto_farm/loop.py'),
           encoding='utf-8').read()
check('6a 定义了公共方法 _refresh_stage_stats',
      'def _refresh_stage_stats' in src)
check('6b 战前记录调用公共方法',
      'self._refresh_stage_stats()' in src)
check('6c 不再有裸露的 max(hl, nm) 关卡覆盖写法',
      'max(hl, nm, display_hl, display_nm)' not in src
      and 'max(hl or 0, nm or 0)' not in src)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
