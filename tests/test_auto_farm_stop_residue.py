# -*- coding: utf-8 -*-
"""v2.3.27：`run_auto_farm` 入口 `_stop` 残留导致「未推图直接 stopped」回归。

真机 2026-11-05 20:33 现象：
```
[20:33:34] [点击] 「收获奖励」
[20:33:51] [信息] 未检测到沙漏剩余次数（xN），本轮不做沙漏领取
[20:33:51] [信息] 已收到停止信号，不进入推图循环
[20:33:51] [自动推图] 结束 | 结果=stopped
```

根因：`stop()` 同时置 `_stop=True` 和 `_stop_event.set()`；唯一的 `_stop`
复位点在 `_run_main_loop()` 开头，而那句在**入口检查之后**执行。
GUI `_on_start` 只 clear 了 `_stop_event`（afk_gui.py:2295），没复位 `_stop`
→ 上一轮按过停止后，本轮一进入口就返回 stopped，从未开始推图。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_auto_farm_stop_residue.py
"""
import os
import sys
import threading
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


_run = L.AFKBotCore.run_auto_farm     # 未绑定方法，显式传伪 self


def make_core(stop_flag: bool, event_set: bool):
    """构造只暴露 run_auto_farm 依赖的最小 core（主循环用哨兵代替）。"""
    ev = threading.Event()
    if event_set:
        ev.set()
    logs = []
    core = SimpleNamespace(
        _stop=stop_flag,
        _stop_event=ev,
        _task_max_time=None,
        _task_max_turns=None,
        _task_start_time=0.0,
        _task_start_turns=0,
        _task_completed=False,
        _log=lambda lvl, m: logs.append(m),
        set_status=lambda **kw: None,
        _run_main_loop=lambda: logs.append('__main_loop__'),
    )
    return core, logs


# ── 1. 核心分叉：上一轮 `_stop` 残留（Event 已被 GUI clear）→ 必须进推图 ──
try:
    c1, l1 = make_core(stop_flag=True, event_set=False)
    r1 = _run(c1)
    check('1a `_stop` 残留 + Event 已清 → 进入主循环（修复前直接 stopped）',
          '__main_loop__' in l1,
          str(l1[:3]))
    check('1b 返回 done（未中途停止）', r1 == 'done', f'r={r1!r}')
    check('1c `_stop` 已被复位为 False', c1._stop is False, str(c1._stop))
    check('1d 日志不含「已收到停止信号」',
          not any('已收到停止信号' in m for m in l1), str(l1[:3]))
except Exception as _e:
    check('1 `_stop` 残留', False, repr(_e))

# ── 2. 本轮导航期用户点停止（Event 已 set）→ 必须不推图 ──
try:
    c2, l2 = make_core(stop_flag=False, event_set=True)
    r2 = _run(c2)
    check('2a Event 已 set → 不进入主循环', '__main_loop__' not in l2, str(l2))
    check('2b 返回 stopped', r2 == 'stopped', f'r={r2!r}')
    check('2c 日志含「已收到停止信号」',
          any('已收到停止信号' in m for m in l2), str(l2[:2]))
except Exception as _e:
    check('2 Event set', False, repr(_e))

# ── 3. 两者都为真 → Event 权威，仍返回 stopped ──
try:
    c3, l3 = make_core(stop_flag=True, event_set=True)
    r3 = _run(c3)
    check('3a `_stop` 与 Event 同时为真 → stopped', r3 == 'stopped', f'r={r3!r}')
    check('3b 未进入主循环', '__main_loop__' not in l3, str(l3))
except Exception as _e:
    check('3 双真', False, repr(_e))

# ── 4. 源码一致性：复位必须在入口检查之前 ──
try:
    with open(os.path.join(ROOT, 'afk_bot', 'auto_farm', 'loop.py'),
              encoding='utf-8') as f:
        _src = f.read()
    _i = _src.find('def run_auto_farm')
    _seg = _src[_i:_i + 2600] if _i > 0 else ''
    _p_reset = _seg.find('self._stop = False')
    # 用带引号的日志字面量作锚点：裸文案会先命中注释里的说明（老坑）
    _p_check = _seg.find("'已收到停止信号，不进入推图循环'")
    check('4a 入口有 `_stop` 复位', _p_reset > 0)
    check('4b 复位位于停止检查之前（否则残留仍会拦截）',
          0 < _p_reset < _p_check, f'reset={_p_reset} check={_p_check}')
    check('4c 入口检查只用 `_stop_event`（Event 为权威）',
          'if self._stop_event.is_set():' in _seg)
except Exception as _e:
    check('4 源码一致性', False, repr(_e))

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)
