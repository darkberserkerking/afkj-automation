# -*- coding: utf-8 -*-
"""测试调度器（tests/run_tests.py）并发隔离回归测试。

背景（v2.4.16）：
  L1 并行下 `test_status_display_chain` 与 `test_gui_drawer_smoke` 偶发失败。
  根因不是用例本身，而是调度器把「串行批次」写在了
  `with ThreadPoolExecutor(...)` **块内部**——Tk 用例与并行批次同时运行，
  所谓「独占」并不独占。GUI 用例因此会拿到未完成的布局
  （winfo_width 偏小 / 两个 Tk 进程抢窗口），断言随机抖动。

本测试守住四件事：
  ① Tk 用例必须被识别：显式标记（SUITES[..][1]）与源码导入双通道
  ② 串行批次必须在**全部**并行用例结束后才开始（时间区间不重叠）
  ③ 并行批次期间不得有任何 Tk 用例在跑
  ④ 空集合 / 全 Tk / 全并行三种边界都不崩

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_run_tests_scheduler.py
"""
import importlib.util
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def _load_run_tests():
    path = os.path.join(ROOT, 'tests', 'run_tests.py')
    spec = importlib.util.spec_from_file_location('_afkj_run_tests', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


RT = _load_run_tests()

# ── ① Tk 用例识别 ─────────────────────────────────────────────
# 真实脚本：gui_drawer_smoke 直接 import customtkinter/tkinter
_tk_auto = RT.detect_tk_suites([
    'tests/test_gui_drawer_smoke.py',
    'tests/test_data_dir.py',
    'tests/test_update_flow.py',
])
check('1a 源码导入通道识别出 test_gui_drawer_smoke（Tk）',
      'tests/test_gui_drawer_smoke.py' in _tk_auto, sorted(_tk_auto))
check('1b 纯逻辑脚本不被误判为 Tk',
      'tests/test_data_dir.py' not in _tk_auto
      and 'tests/test_update_flow.py' not in _tk_auto, sorted(_tk_auto))
check('1c 不存在的脚本不抛异常（容错）',
      RT.detect_tk_suites(['tests/__not_exist__.py']) == set())

# ── ② / ③ 两阶段顺序：串行必须在并行全部结束后 ─────────────────
# 用注入的 run_fn 记录每个用例的 (开始, 结束) 与线程名，避免真跑脚本。
_events = []
_lock = threading.Lock()


def _fake_runner(suite, real=False, timeout=1800):
    t0 = time.time()
    time.sleep(0.05 if 'parallel' in suite else 0.01)
    t1 = time.time()
    with _lock:
        _events.append({'suite': suite, 'start': t0, 'end': t1,
                        'thread': threading.current_thread().name})
    return {'suite': suite, 'ok': True, 'dur': t1 - t0,
            'summary': ('1', '1'), 'fails': [], 'out_tail': ''}


# 造三个「并行」与两个「Tk」用例；Tk 用 SUITES 里真实存在的脚本，
# 以便同时验证显式标记通道。
try:
    RT.SUITES['tests/_fake_parallel_a.py'] = ({1, 2, 3}, False)
    RT.SUITES['tests/_fake_parallel_b.py'] = ({1, 2, 3}, False)
    RT.SUITES['tests/_fake_parallel_c.py'] = ({1, 2, 3}, False)
    _suites = ['tests/_fake_parallel_a.py',
               'tests/_fake_parallel_b.py',
               'tests/_fake_parallel_c.py',
               'tests/test_gui_drawer_smoke.py',      # 源码导入 → Tk
               'tests/test_bug_report.py']            # 显式标记 → Tk
    _results, _serial = RT.execute_suites(_suites, 3, False, run_fn=_fake_runner)

    check('2a 返回结果数与用例数一致', len(_results) == len(_suites),
          f'{len(_results)}/{len(_suites)}')
    check('2b 串行批次含两个 Tk 用例',
          set(_serial) == {'tests/test_gui_drawer_smoke.py',
                           'tests/test_bug_report.py'}, _serial)

    _par = [e for e in _events if e['suite'] not in _serial]
    _ser = [e for e in _events if e['suite'] in _serial]
    check('2c 三个非 Tk 用例确实并行（都跑在工作线程）',
          len(_par) == 3 and all(e['thread'] != 'MainThread' for e in _par),
          [e['thread'] for e in _par])
    check('2d Tk 用例在主线程串行执行',
          len(_ser) == 2 and all(e['thread'] == 'MainThread' for e in _ser),
          [e['thread'] for e in _ser])

    _par_first_start = min(e['start'] for e in _par) if _par else 0
    _par_last_end = max(e['end'] for e in _par) if _par else 0
    _ser_first_start = min(e['start'] for e in _ser) if _ser else 0
    check('3a 串行批次在并行批次全部结束后才开始（不重叠）',
          _ser_first_start >= _par_last_end,
          f'par_end={_par_last_end:.3f} ser_start={_ser_first_start:.3f}')
    check('3b 并行期间无 Tk 用例在跑',
          all(not (e['start'] < _par_last_end and e['end'] > _par_first_start)
              for e in _ser),
          f'par=[{_par_first_start:.3f},{_par_last_end:.3f}]')
    check('3c 两个 Tk 用例之间也不重叠（同一时刻只一个）',
          len(_ser) == 2 and _ser[1]['start'] >= _ser[0]['end'],
          f"{_ser[0]['end']:.3f} -> {_ser[1]['start']:.3f}")
except Exception as _e:
    check('2/3 两阶段执行断言', False, repr(_e))

# ── ④ 边界：空 / 全 Tk / 全并行 ───────────────────────────────
try:
    _r, _s = RT.execute_suites([], 4, False, run_fn=_fake_runner)
    check('4a 空用例集返回空结果且不崩', _r == [] and _s == [])
except Exception as _e:
    check('4a 空用例集返回空结果且不崩', False, repr(_e))

try:
    _r, _s = RT.execute_suites(['tests/test_bug_report.py'], 4, False,
                               run_fn=_fake_runner)
    check('4b 全 Tk 用例集全部进串行', _s == ['tests/test_bug_report.py']
          and len(_r) == 1, _s)
except Exception as _e:
    check('4b 全 Tk 用例集全部进串行', False, repr(_e))

try:
    _r, _s = RT.execute_suites(['tests/_fake_parallel_a.py'], 2, False,
                               run_fn=_fake_runner)
    check('4c 全并行用例集串行批次为空', _s == [] and len(_r) == 1, _s)
except Exception as _e:
    check('4c 全并行用例集串行批次为空', False, repr(_e))

# ── ⑤ real 参数确实透传给执行函数 ─────────────────────────────
_seen = []


def _spy_runner(suite, real=False, timeout=1800):
    _seen.append((suite, real))
    return {'suite': suite, 'ok': True, 'dur': 0.0,
            'summary': ('1', '1'), 'fails': [], 'out_tail': ''}


RT.execute_suites(['tests/_fake_parallel_a.py', 'tests/test_bug_report.py'],
                  2, True, run_fn=_spy_runner)
check('5a --real 透传到并行与串行两侧',
      len(_seen) == 2 and all(r is True for _s, r in _seen), _seen)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
