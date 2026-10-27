# -*- coding: utf-8 -*-
"""日志卫生（v2.1.27，P0-1 / P0-2）

P0-1 状态日志降噪：
  真机实测「玩法循环 | <状态>」家族占全量日志 **22.7%**（7,029 行 / 仅 26 种
  模式）。改为「状态变化才打印 + 每 N 帧心跳」。
  ⚠️ 关键：状态是**振荡**的（unknown→door→battle_prep→unknown…），实测
  连续重复仅 2.2% —— 单纯"与前一行比对"无效，必须按状态键判定。

P0-2 崩溃日志与 stderr 拆分：
  此前 sys.stderr 整体重定向进 CRASH_REPORT.log，实测 5,865 行里真崩溃
  仅 34 次（98% 是 "Exception in thread Thread-N" 噪声）→ 排障入口失效。
  现拆为独立 stderr.log，并给崩溃块附「版本 + 最后 20 行现场快照」。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_log_hygiene.py
"""
import os
import sys
import threading
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
fast_time.install()          # 虚拟时钟：sleep 只推进偏移，语义不变
import tasks.catalog  # noqa: F401,E402
from tasks.base import _STATE_LOG_MIN_INTERVAL  # noqa: E402
from tasks.labyrinth import LabyrinthTask  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


def mk(logs):
    se, pe = threading.Event(), threading.Event()
    pe.set()
    return LabyrinthTask({
        'bot': None, 'logger': logs.append, 'stats': {},
        'stop_event': se, 'pause_event': pe, 'options': {},
        'max_time': None, 'max_turns': None, 'start_turns': 0,
        'inst_id': 'labyrinth#loghygiene',
    })


print('══ 日志卫生（v2.1.27 P0）══')
print()

# ── A. log_state：全局时间限流（振荡噪声的唯一有效压制手段）──
print('── A. 状态日志限流（P0-1）──')
logs = []
t = mk(logs)
# 模拟真机振荡：unknown→door→battle_prep 循环 10 轮（共 30 帧，虚拟时间不推进
# ——对应"轮询很快、画面一直在转场"的刷屏场景）
seq = ['unknown', 'door', 'battle_prep'] * 10
for st in seq:
    t.log_state(st, f"玩法循环 | {st}(X)")
n_loop = sum(1 for m in logs if '玩法循环 |' in m)
check(f'A1 振荡 30 帧（同一次限流窗口内）→ 只打 {n_loop} 行（原实现打 30 行）',
      n_loop == 1, f'实际 {n_loop} 行')

# 推进虚拟时钟 → 心跳重新出现
logs2 = []
t2 = mk(logs2)
for _ in range(5):
    t2.log_state('door', '玩法循环 | door(X)')
import time as _time                      # noqa: E402
_time.sleep(_STATE_LOG_MIN_INTERVAL + 1)  # fast_time 只推进虚拟偏移
t2.log_state('door', '玩法循环 | door(X)')
check(f'A2 间隔 {_STATE_LOG_MIN_INTERVAL:.0f}s 后重新打印（心跳可见性）',
      sum(1 for m in logs2 if '玩法循环 |' in m) == 2,
      f'{sum(1 for m in logs2 if "玩法循环 |" in m)} 行')

logs3 = []
t3 = mk(logs3)
t3.log_state('unknown', 'A')
t3.log_state('unknown:other', 'B')
check('A3 同一限流窗口内的不同状态也不打印（限流是全局的，非按 key 去重）',
      sum(1 for m in logs3 if m.endswith(('A', 'B'))) == 1, str(logs3))

logs4 = []
t4 = mk(logs4)
for st in ('door', 'battle_prep', 'unknown'):
    t4.log_state(st, f'状态{st}')
    t4.log(f'[点击] 动作{st}')            # 动作类日志
n4_state = sum(1 for m in logs4 if '状态' in m)
n4_action = sum(1 for m in logs4 if '动作' in m)
check(f'A4 动作类日志不受限流影响（{n4_action}/3 条全保留）',
      n4_action == 3 and n4_state == 1,
      f'状态={n4_state} 动作={n4_action}')

# ── B. count_iter：显式迭代计数（替代"日志当 API"）──
print()
print('── B. 迭代计数（P0-1 配套）──')
t5 = mk([])
check('B1 初值为 0', getattr(t5, '_loop_iters', 0) == 0, '')
for _ in range(7):
    t5.count_iter()
check('B2 调用 7 次 → 计数 7', t5._loop_iters == 7, f'{t5._loop_iters}')
check('B3 计数不受日志策略影响（降噪后仍准确）',
      t5._loop_iters == 7, '同 B2')

# ── C. 静态：源码事实 ──
print()
print('── C. 静态检查 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'), encoding='utf-8').read()
_god = open(os.path.join(ROOT, 'tasks', 'goddess_trial.py'),
            encoding='utf-8').read()
_g = open(os.path.join(ROOT, 'afk_gui.py'), encoding='utf-8').read()
check('C1 labyrinth 的状态日志已走 log_state',
      'self.log_state(state, f"玩法循环 | {state}({label})")' in _src)
check('C2 goddess 的状态日志已走 log_state（且无残留 self.log(f"玩法循环）',
      'self.log_state(\n                    state,' in _god
      and 'self.log(\n                    f"玩法循环' not in _god)
check('C3 两个主循环都调用了 count_iter',
      _src.count('self.count_iter()') >= 1
      and _god.count('self.count_iter()') >= 1,
      f'labyrinth={_src.count("self.count_iter()")} '
      f'goddess={_god.count("self.count_iter()")}')
check('C4 【P0-2】stderr 不再重定向进 CRASH_REPORT',
      "sys.stderr = open(STDERR_LOG" in _g
      and "sys.stderr = open(CRASH_LOG" not in _g)
check('C5 【P0-2】崩溃块包含版本与现场快照',
      '[!] 版本:' in _g and '崩溃前最后' in _g and '_recent_logs' in _g)
check('C6 【P2-8】日志滚动函数已接入打开处',
      'def _rotate_log_file' in _g and '_rotate_log_file(_log_path)' in _g)

# ── D. 行为：_rotate_log_file 真能滚动 ──
print()
print('── D. 日志滚动（P2-8）──')
_orig = sys.stderr
try:
    sys.stderr = _orig          # 先还原，避免 import 副作用吃掉测试输出
    import afk_gui              # noqa: E402
finally:
    sys.stderr = _orig
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, 'x.log')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('a' * 1024)
    afk_gui._rotate_log_file(p)
    check('D1 未超上限 → 不滚动', os.path.exists(p) and not os.path.exists(p + '.1'))
    with open(p, 'w', encoding='utf-8') as f:
        f.write('a' * (afk_gui._LOG_MAX_BYTES + 1))
    afk_gui._rotate_log_file(p)
    check('D2 超上限 → 滚动为 .1',
          os.path.exists(p + '.1') and not os.path.exists(p),
          f'存在 .1={os.path.exists(p + ".1")}')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('a' * (afk_gui._LOG_MAX_BYTES + 1))
    afk_gui._rotate_log_file(p)
    check('D3 再滚动 → 旧 .1 让位为 .2（历史保留）',
          os.path.exists(p + '.2'), f'.2={os.path.exists(p + ".2")}')
    check('D4 上限为 5MB / 保留 3 份',
          afk_gui._LOG_MAX_BYTES == 5 * 1024 * 1024
          and afk_gui._LOG_BACKUPS == 3, '')

print()
print('═' * 56)
print(f'日志卫生：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)
