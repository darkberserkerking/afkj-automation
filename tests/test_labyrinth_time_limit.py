"""v1.8.4：LabyrinthTask 时间上限（max_time）纯逻辑测试。

验证 check_stop() 覆写：max_time 达标 → True（日志只打一次）+ _stop_result()=='done'；
手动停止 → 'stopped'；max_time 未设/<=0 → 永不因时间触发。
"""

import sys
import threading
import time

sys.path.insert(0, r'C:\Users\x9502\WorkBuddy\20260320211537')

from tasks.labyrinth import LabyrinthTask


PASS, FAIL = "✅", "❌"
_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, cond, detail))
    print(f"{PASS if cond else FAIL} {name}" + (f" — {detail}" if detail else ""))
    return cond


def _mk(max_time, stop_set=False):
    logs = []
    ev = threading.Event()
    if stop_set:
        ev.set()
    task = LabyrinthTask({
        "bot": None,
        "logger": logs.append,
        "stats": {},
        "stop_event": ev,
        "pause_event": None,
        "options": {},
        "max_time": max_time,
        "inst_id": "labyrinth#test",
    })
    return task, logs, ev


def main():
    # 用例 1：max_time=5，老化 10s → check_stop True / _stop_result 'done'
    #        日志恰 1 条含「任务指标达成: 时间 5s」；再调一次仍只 1 条
    t, logs, _ = _mk(5)
    t._started = time.time() - 10
    check("用例1 check_stop 超时达标=True", t.check_stop() is True)
    check("用例1 _stop_result=='done'", t._stop_result() == "done")
    hit = [m for m in logs if "任务指标达成: 时间 5s" in m]
    check("用例1 日志恰 1 条达标文案", len(hit) == 1, f"实际 {len(hit)} 条")
    # 再调一次 check_stop：仍 True，但日志不重复打
    _ = t.check_stop()
    hit2 = [m for m in logs if "任务指标达成: 时间 5s" in m]
    check("用例1 日志只打一次（再调仍 1 条）", len(hit2) == 1, f"实际 {len(hit2)} 条")

    # 用例 2：max_time=None，老化 10s → check_stop False
    t, _, _ = _mk(None)
    t._started = time.time() - 10
    check("用例2 max_time=None 不触发", t.check_stop() is False)

    # 用例 3：max_time=0，老化 10s → check_stop False（0=不限）
    t, _, _ = _mk(0)
    t._started = time.time() - 10
    check("用例3 max_time=0 不触发", t.check_stop() is False)

    # 用例 4：max_time=5 不老化（elapsed≈0）→ check_stop False
    t, _, _ = _mk(5)
    check("用例4 未超时 check_stop=False", t.check_stop() is False)

    # 用例 5：max_time=3600 + stop_event 置位 → check_stop True，
    #         但 _time_up False → _stop_result 'stopped'（手动停止语义）
    t, _, _ = _mk(3600, stop_set=True)
    check("用例5 手动停止 check_stop=True", t.check_stop() is True)
    check("用例5 手动停止 _time_up=False", t._time_up is False)
    check("用例5 _stop_result=='stopped'", t._stop_result() == "stopped")

    # 用例 6：_time_limit_reached 直接调用：max_time=5 老化 10s → True 且 _time_up True
    t, _, _ = _mk(5)
    t._started = time.time() - 10
    check("用例6 _time_limit_reached=True", t._time_limit_reached() is True)
    check("用例6 _time_up=True", t._time_up is True)

    passed = sum(1 for _, c, _ in _results if c)
    total = len(_results)
    print(f"\n全部通过 {passed}/{total}")
    return passed == total


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
