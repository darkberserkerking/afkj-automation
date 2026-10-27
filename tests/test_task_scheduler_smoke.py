"""
任务层冒烟测试（v1.5.3 实例化版）

不需要真实游戏窗口，用 MockBot 验证任务层改造是否成立：
  1. import 链路（base / registry / scheduler / catalog / 实例）
  2. 多实例（同种任务多次添加，独立指标）
  3. 生命周期：preflight → on_start → run → on_end
  4. 停止信号桥接（调度器 ↔ bot 共享 Event）
  5. 跳过（skipped：planned 任务 / 未勾选）
  6. 结束指标：max_time / max_turns（check_completion OR 逻辑）
  7. 任务间状态隔离（recover_to_idle）

运行（纯逻辑，无需游戏依赖）：
  python tests/test_task_scheduler_smoke.py
"""

import os
import sys
import threading
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

from tasks.base import BaseTask
from tasks.scheduler import TaskScheduler
from tasks.catalog import TASK_CLASSES
from task_registry import TaskRegistry, TaskInstance, TaskMeta

PASS, FAIL = "✅", "❌"
_results = []


def check(name: str, cond: bool, detail: str = ""):
    _results.append((name, cond, detail))
    print(f"{PASS if cond else FAIL} {name}" + (f" — {detail}" if detail else ""))
    return cond


class MockBot:
    """模拟 AFKBot，仅实现任务层所需接口。"""

    def __init__(self, farm_result="done", hwnd=12345, turns=0):
        self._stop = False
        self._stop_event = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self.farm_result = farm_result
        self.hwnd = hwnd
        self.calls = []
        self.stats = {"total_battles": turns}

    def find_and_focus(self):
        self.calls.append("find_and_focus")
        return self.hwnd

    def run_auto_farm(self, max_time=None, max_turns=None, start_turns=0):
        self.calls.append(
            ("run_auto_farm", max_time, max_turns, start_turns))
        return self.farm_result

    def recover_to_idle(self):
        self.calls.append("recover_to_idle")
        return True


# ═══════════════════════════════════════════════════════════
#  1. 元数据 / 目录 / 多实例
# ═══════════════════════════════════════════════════════════
print("\n── 1. 元数据与多实例 ──")

check("catalog 注册全部任务类",
      set(TASK_CLASSES) == {'auto_farm', 'labyrinth', 'goddess_trial',
                            'home', 'arena', 'daily_quest', 'friend_gift',
                            'dream_realm',
                            'start_game', 'close_game'},
      str(list(TASK_CLASSES)))

check("auto_farm ready + planned 区分",
      TASK_CLASSES['auto_farm'].task_id == 'auto_farm',
      "ready 任务可调度")

# 多实例：同种任务添加 2 次，独立指标
TaskRegistry._instances = []
TaskRegistry._order = []
i1 = TaskRegistry.add_instance('auto_farm', max_time=60, max_turns=10)
i2 = TaskRegistry.add_instance('auto_farm')
check("同种任务多实例", i1.inst_id == 'auto_farm#1'
      and i2.inst_id == 'auto_farm#2', f"{i1.inst_id} / {i2.inst_id}")
check("实例独立指标", i1.max_time == 60 and i1.max_turns == 10
      and i2.max_time is None and i2.max_turns is None,
      f"#1: {i1.max_time}s/{i1.max_turns}轮  #2: {i2.max_time}/{i2.max_turns}")

# ═══════════════════════════════════════════════════════════
#  2. BaseTask 结束指标（check_completion OR 逻辑）
# ═══════════════════════════════════════════════════════════
print("\n── 2. 结束指标 ──")

bot = MockBot(turns=5)
t = TASK_CLASSES['auto_farm']({
    "bot": bot, "logger": lambda m: None, "stats": bot.stats,
    "max_time": 60, "max_turns": 10, "start_turns": 3, "inst_id": "auto_farm#1",
})
check("turns 相对实例增量", t.turns == 2, f"total=5 start=3 → {t.turns}")
check("指标未达 → 不结束", t.check_completion() == (False, ""),
      str(t.check_completion()))
t2 = TASK_CLASSES['auto_farm']({
    "bot": bot, "logger": lambda m: None, "stats": bot.stats,
    "max_time": 60, "max_turns": 2, "start_turns": 3, "inst_id": "auto_farm#2",
})
ok, reason = t2.check_completion()
check("轮次达上限 → 结束", ok and "轮次" in reason, reason)

# ═══════════════════════════════════════════════════════════
#  3. 实例调度（生命周期）
# ═══════════════════════════════════════════════════════════
print("\n── 3. 实例调度 ──")

logs = []
bot2 = MockBot(farm_result="done", turns=0)
sched = TaskScheduler(bot2, logger=logs.append)
sched.add(TaskInstance('auto_farm#1', 'auto_farm',
                       max_time=300, max_turns=50))
res = sched.run_all()
check("实例执行返回 done", res.get("auto_farm#1") == "done", str(res))
check("指标透传到 bot.run_auto_farm",
      any(c[0] == "run_auto_farm" and c[1] == 300 and c[2] == 50
          for c in bot2.calls), str(bot2.calls))
check("日志含实例标识", any("auto_farm#1" in m for m in logs),
      logs[0] if logs else "(无日志)")

# ═══════════════════════════════════════════════════════════
#  4. 跳过：planned 任务 / 未勾选 / 未知类型
# ═══════════════════════════════════════════════════════════
print("\n── 4. 跳过 ──")

bot3 = MockBot()
s3 = TaskScheduler(bot3, logger=lambda m: None)
# tower 已随 v1.8.0 删除，用临时 planned 占位保持 skipped 路径覆盖
TaskRegistry.register(TaskMeta(task_id='planned_stub', label='占位任务',
                               description='', status='planned'))
s3.add(TaskInstance('planned_stub#1', 'planned_stub'))
s3.add(TaskInstance('auto_farm#off', 'auto_farm', enabled=False))  # 未勾选
s3.add(TaskInstance('unknown#1', 'not_exist'))  # 未知类型
res3 = s3.run_all()
check("planned 任务 skipped", res3.get("planned_stub#1") == "skipped", str(res3))
check("未勾选实例不执行", "auto_farm#off" not in res3, str(res3))
check("未知类型 skipped", res3.get("unknown#1") == "skipped", str(res3))

# ═══════════════════════════════════════════════════════════
#  5. 停止信号桥接 + 重启
# ═══════════════════════════════════════════════════════════
print("\n── 5. 停止信号 ──")

bot4 = MockBot(farm_result="done")
s4 = TaskScheduler(bot4, logger=lambda m: None)
s4.add(TaskInstance('auto_farm#1', 'auto_farm'))
bot4._stop_event.set()   # 模拟已停止（Event 残留 set）
res4 = s4.run_all()
check("stop_event 预置 → 调度中止（空结果）", res4 == {}, str(res4))

# 修复验证：clear 后能正常执行
bot4._stop_event.clear()
s5 = TaskScheduler(bot4, logger=lambda m: None)
s5.add(TaskInstance('auto_farm#1', 'auto_farm'))
res5 = s5.run_all()
check("clear 后重启正常", res5.get("auto_farm#1") == "done", str(res5))

# ═══════════════════════════════════════════════════════════
#  6. 任务间隔离
# ═══════════════════════════════════════════════════════════
print("\n── 6. 状态隔离 ──")

bot6 = MockBot(farm_result="done")
s6 = TaskScheduler(bot6, logger=lambda m: None)
s6.add(TaskInstance('auto_farm#1', 'auto_farm'))
s6.add(TaskInstance('auto_farm#2', 'auto_farm'))
s6.run_all()
# v1.8.42：任务间改为全局导航（auto_farm nav_target=idle_stage），
# recover_to_idle 仅作导航失败时的兜底，正常路径不应触发
check("多实例间不再调用 recover_to_idle（改走全局导航最短路径）",
      bot6.calls.count("recover_to_idle") == 0, str(bot6.calls))

# ═══════════════════════════════════════════════════════════
#  汇总
# ═══════════════════════════════════════════════════════════
failed = [n for n, ok, _ in _results if not ok]
print("\n" + "=" * 46)
print(f"  通过 {len(_results) - len(failed)}/{len(_results)}")
if failed:
    print(f"  {FAIL} 失败项：{failed}")
    sys.exit(1)
print(f"  {PASS} 任务层冒烟测试全部通过（v1.5.3 实例化版）")
print("=" * 46)
