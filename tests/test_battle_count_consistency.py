# -*- coding: utf-8 -*-
"""v2.3.20：战斗计数与日志一致性回归（GUI「累计挑战/已通关」漏计修复）。

真机 2026-11-05 16:49 用户报障：日志里 5 次「点击挑战/按 F 键开始战斗」，
GUI 只显示「累计挑战 2 / 已通关 2」。

根因：主循环先让 `navigator._handle_common_phase` 处理 result 阶段，
结算页被关掉后 `phase == 'battle'` 分支走不到（loop.py 原 897 的计数点），
unknown 分支的「结算复查命中」也直接 continue —— 两条路径都绕过计数。

本测试验证 `_credit_battle()` 的补记语义，并用真机日志的 5 场序列复现
「修复前 2/2 → 修复后 4/4」。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_battle_count_consistency.py
"""
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import afk_bot.auto_farm.loop as L

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def make_core(**stats_over):
    """构造只暴露 _credit_battle 依赖的最小 core。"""
    logs, statuses, pushes = [], [], []

    def _log(level, msg):
        logs.append((level, msg))

    def set_status(**kw):
        statuses.append(kw)

    stats = {
        'total_battles': 0, 'stage_progress': 0, '本轮进度': 0,
        'huanling_wins': 0, 'normal_wins': 0,
    }
    stats.update(stats_over)
    core = SimpleNamespace(
        stats=stats,
        _log=_log,
        set_status=set_status,
        _push_stats=lambda: pushes.append(1),
    )
    return core, logs, statuses, pushes


_credit = L.AFKBotCore._credit_battle   # 未绑定方法，显式传伪 self

# ── 1. 胜利补记：挑战 +1、推进 +1、胜场 +1、推送 ──
try:
    c, logs, sts, psh = make_core()
    _credit(c, True, 'huanling', '结算复查路径补记')
    check('v2.3.20 胜利补记 → total_battles 1',
          c.stats['total_battles'] == 1, str(c.stats['total_battles']))
    check('v2.3.20 胜利补记 → stage_progress 1',
          c.stats['stage_progress'] == 1, str(c.stats['stage_progress']))
    check('v2.3.20 胜利补记 → 本轮进度同步',
          c.stats['本轮进度'] == 1, str(c.stats['本轮进度']))
    check('v2.3.20 胜利补记 → huanling_wins 1',
          c.stats['huanling_wins'] == 1, str(c.stats['huanling_wins']))
    check('v2.3.20 胜利补记 → set_status(round=1)',
          sts == [{'round': 1}], str(sts))
    check('v2.3.20 胜利补记 → 推送一次 stats', len(psh) == 1, str(len(psh)))
    check('v2.3.20 胜利补记 → 日志含「推进+1关」',
          any('推进+1关' in m for _l, m in logs),
          str([m for _l, m in logs][:2]))
except Exception as _e:
    check('v2.3.20 胜利补记', False, repr(_e))

# ── 2. 失败补记：只加挑战数，不推进、不算胜场 ──
try:
    c, logs, sts, psh = make_core(total_battles=3, stage_progress=2)
    _credit(c, False, 'normal', '')
    check('v2.3.20 失败补记 → total_battles 4',
          c.stats['total_battles'] == 4, str(c.stats['total_battles']))
    check('v2.3.20 失败补记 → stage_progress 不变（仍 2）',
          c.stats['stage_progress'] == 2, str(c.stats['stage_progress']))
    check('v2.3.20 失败补记 → normal_wins 不变',
          c.stats['normal_wins'] == 0, str(c.stats['normal_wins']))
    check('v2.3.20 失败补记 → 无「推进」日志',
          not any('推进+1关' in m for _l, m in logs))
    check('v2.3.20 失败补记 → 仍推送状态',
          sts == [{'round': 4}] and len(psh) == 1)
except Exception as _e:
    check('v2.3.20 失败补记', False, repr(_e))

# ── 3. 模式归属：normal 记到 normal_wins ──
try:
    c, *_ = make_core()
    _credit(c, True, 'normal', '')
    check('v2.3.20 normal 胜利记入 normal_wins',
          c.stats['normal_wins'] == 1 and c.stats['huanling_wins'] == 0,
          f"normal={c.stats['normal_wins']} huanling={c.stats['huanling_wins']}")
except Exception as _e:
    check('v2.3.20 模式归属', False, repr(_e))

# ── 4. 真机 5 场序列复现（核心分叉）──
# 日志实测：第1场→结算复查(胜) 第2场→navigator抢先(胜)
#           第3/4场→[结果]战斗胜利(正常计)  第5场→用户停止(未结算)
# 修复前：只有第3/4场被计 → 2/2（GUI 实测值）
# 修复后：第1/2场补记 + 第3/4场正常 → 4/4
try:
    c, logs, sts, psh = make_core()
    # 第1场：结算复查路径命中「战斗胜利」
    _credit(c, True, 'huanling', '结算复查路径补记')
    # 第2场：navigator 抢先关胜利结算
    _credit(c, True, 'huanling', '胜利结算被导航器抢先，已补记')
    # 第3/4场：正常 path（loop.py 原 897/1021 的等价累加）
    for _ in range(2):
        c.stats['total_battles'] += 1
        c.stats['stage_progress'] += 1
        c.stats['本轮进度'] = c.stats['stage_progress']
    check('v2.3.20 真机 5 场序列 → 累计挑战 4（修复前 2）',
          c.stats['total_battles'] == 4, str(c.stats['total_battles']))
    check('v2.3.20 真机 5 场序列 → 已通关 4（修复前 2）',
          c.stats['stage_progress'] == 4, str(c.stats['stage_progress']))
    check('v2.3.20 补记日志可追溯（含原因）',
          sum(1 for _l, m in logs if '推进+1关' in m) == 2,
          str([m for _l, m in logs if '推进' in m]))
except Exception as _e:
    check('v2.3.20 真机序列复现', False, repr(_e))

# ── 5. 源码一致性（防回归：两条补记路径都必须走 _credit_battle）──
try:
    with open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
              encoding='utf-8') as _f:
        _src = _f.read()

    check('v2.3.20 _credit_battle 已定义',
          'def _credit_battle(' in _src)
    check('v2.3.20 补记调用点 ≥2 处（navigator 抢先 + 结算复查）',
          _src.count('self._credit_battle(') >= 2,
          f"count={_src.count('self._credit_battle(')}")

    _pv = _src.find('self._pending_victory: bool = (')
    check('v2.3.20 新增 _pending_victory 判定', _pv > 0)
    check('v2.3.20 _pending_victory 依据 last_result == victory',
          _pv > 0 and "'victory'" in _src[_pv:_pv + 300])
    check('v2.3.20 补齐条件同时覆盖 defeat/victory',
          'if self._pending_defeat or self._pending_victory:' in _src)
    # 结算复查路径必须调 _credit_battle 再 continue（否则又漏）
    # 用带引号的日志字面量作锚点：裸「结算复查命中」也会命中 docstring 引用，
    # 取到错误位置（本测试第一版即因此误报）。
    _rs = _src.find("'[信息] 结算复查命中")
    _rs_seg = _src[_rs:_rs + 1400] if _rs > 0 else ''
    check('v2.3.20 结算复查路径已接入补记',
          _rs > 0 and 'self._credit_battle(' in _rs_seg,
          f'anchor={_rs}')
except Exception as _e:
    check('v2.3.20 源码一致性', False, repr(_e))

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)
