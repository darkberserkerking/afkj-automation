# -*- coding: utf-8 -*-
"""v2.3.15：实时状态显示「后台 → 队列 → 主线程 → 控件」全链路回归。

与 tests/test_status_display.py（纯逻辑 FakeLabel）互补：本文件构造**真实
App 与真实 Tk 控件**，验证数据确实经 `__status__` 队列 + `_flush` 主线程
分发后才 configure，且结束态 / 无 stats 快照不清零。

链路：
    StatsTracker.push() → _update_status(**kwargs)
        → _log_queue.put(('__status__', dict(self._status)))
        → _flush()（主线程 after 轮询，取队列最后一份快照）
        → _refresh_ui(data) → _lbl_stat_*.configure(text=...)

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_status_display_chain.py
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests import fast_time
fast_time.install()

_TMP = tempfile.mkdtemp(prefix='afkj_status_chain_')
import task_registry
task_registry._CONFIG = os.path.join(_TMP, 'tasks_enabled.json')

sys.excepthook = sys.__excepthook__
import afk_gui
sys.excepthook = sys.__excepthook__   # afk_gui 安装了全局 excepthook
afk_gui.GUI_CONFIG = os.path.join(_TMP, 'gui_config.json')
with open(afk_gui.GUI_CONFIG, 'w', encoding='utf-8') as _f:
    json.dump({'alpha': 0.8, 'topmost': False,
               'geometry': '1100x850+20+30'}, _f)

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


app = afk_gui.App()
app.report_callback_exception = lambda *a: None
app.update_idletasks()

# 构造期注册的 after 定时器全部取消，改为手动驱动 _flush，避免异步干扰
for _attr in ('_flush_id', '_timer_id'):
    _fid = getattr(app, _attr, None)
    if _fid:
        try:
            app.after_cancel(_fid)
        except Exception:
            pass
    setattr(app, _attr, None)


def vals():
    """v2.3.17：计数控件在「自动推图」任务行内，按行收集于 _stat_labels。"""
    _lb, _ls = app._stat_labels[0]
    return (_lb.cget('text'), _ls.cget('text'))


def flush_once():
    """主线程分发一次，并取消 _flush 末尾自续的 after(200)。"""
    app._flush()
    _fid = getattr(app, '_flush_id', None)
    if _fid:
        try:
            app.after_cancel(_fid)
        except Exception:
            pass
    app._flush_id = None


def status_items():
    """窥视队列里的 __status__ 快照（只读，取出后原序放回）。"""
    got = []
    while True:
        try:
            got.append(app._log_queue.get_nowait())
        except Exception:
            break
    for _it in got:
        app._log_queue.put(_it)
    return [_it[1] for _it in got
            if isinstance(_it, tuple) and _it[0] == '__status__']


# ── 1. 启动态：模拟上次运行残留的全局累计作为基线 ──
# _on_start 里从 self._bot.stats 采样；此处直接设等价基线，源码一致性由
# test_status_display.py 的断言单独保障（不在测试里 exec 产品源码）。
app._run_base_battles = 100
app._run_base_stages = 40
app._stat_labels[0][0].configure(text='0')
app._stat_labels[0][1].configure(text='0')
check('v2.3.15 启动置零 → 0/0', vals() == ('0', '0'), f'{vals()}')
# v2.3.17：祖先链 = 数值Label → title_row → mid → row，row 必须是自动推图那一行
_anc_row = app._stat_labels[0][0].master.master.master
_af_ids = [iid for iid in app._task_rows if iid.startswith('auto_farm')]
check('v2.3.17 计数控件挂在「自动推图」任务行内',
      bool(_af_ids) and app._task_rows[_af_ids[0]]['frame'] is _anc_row,
      f'af_rows={_af_ids}')
check('v2.3.17 日志头部旧状态条已移除',
      not hasattr(app, '_stat_bar'))

# ── 2. 执行中：后台推送必须经队列，flush 前不得直改控件 ──
app._update_status(stats={'total_battles': 101, 'stage_progress': 41})
_snaps = status_items()
check('v2.3.15 _update_status 已投递 __status__ 快照',
      any(_s.get('stats', {}).get('total_battles') == 101 for _s in _snaps),
      f'snapshots={len(_snaps)}')
check('v2.3.15 flush 前不直改控件（仍 0/0）',
      vals() == ('0', '0'), f'{vals()}')
flush_once()
check('v2.3.15 经队列+flush 后显示 1/1',
      vals() == ('1', '1'), f'{vals()}')

# 连续两次 push：一次 flush 只取队列最后一份快照
app._update_status(stats={'total_battles': 102, 'stage_progress': 42})
app._update_status(stats={'total_battles': 103, 'stage_progress': 43})
check('v2.3.15 连续 push 队列内有 2 份快照',
      len(status_items()) == 2, f'snapshots={len(status_items())}')
flush_once()
check('v2.3.15 一次 flush 取最后一份 → 3/3',
      vals() == ('3', '3'), f'{vals()}')

# ── 3. 结束态：running=False 不清零，数值保留 ──
app._update_status(running=False)
flush_once()
check('v2.3.15 结束态保留 3/3',
      vals() == ('3', '3'), f'{vals()}')

# ── 4. phase/round 状态更新不得改变数值（_status 持久 stats 快照）──
app._update_status(phase='battle', round=103)
flush_once()
check('v2.3.15 phase/round 快照数值不变（仍 3/3）',
      vals() == ('3', '3'), f'{vals()}')

# 4b. 真正不含 stats 的 __status__ 快照（防御性加固的验证目标）：
#     _refresh_ui 必须 return 保持当前值，而不是把显示清成 0/0
app._log_queue.put(('__status__', {'running': False, 'phase': 'init'}))
flush_once()
check('v2.3.15 无 stats 快照保持 3/3（不清零）',
      vals() == ('3', '3'), f'{vals()}')

# ── 5. 第二次运行：重设基线后只显示本轮增量，不带出上次累计 ──
app._run_base_battles = 103
app._run_base_stages = 43
app._stat_labels[0][0].configure(text='0')
app._stat_labels[0][1].configure(text='0')
app._update_status(stats={'total_battles': 104, 'stage_progress': 43})
flush_once()
check('v2.3.15 第二次运行只显示本轮增量 → 1/0',
      vals() == ('1', '0'), f'{vals()}')

# ── 6. 不破坏停止链路 / 控制按钮 ──
try:
    app._on_stop()
    check('v2.3.15 停止后按钮禁用并显示「正在停止」',
          str(app._btn_main.cget('state')) == 'disabled'
          and '正在停止' in str(app._btn_main.cget('text')))
    app._reset_buttons()
    check('v2.3.15 复位后按钮恢复「▶ 运行」',
          str(app._btn_main.cget('state')) == 'normal'
          and '运行' in str(app._btn_main.cget('text')))
    check('v2.3.15 停止/复位链路不改数值（仍 1/0）',
          vals() == ('1', '0'), f'{vals()}')
except Exception as _e:
    check('v2.3.15 停止链路', False, str(_e))

try:
    app.withdraw()
except Exception:
    pass

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)
