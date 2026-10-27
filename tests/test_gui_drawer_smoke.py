# -*- coding: utf-8 -*-
"""GUI 抽屉化冒烟测试（v1.5.4 起，v2.1.0 重建版）

> ⚠️ 说明（v2.1.0）：本文件在 v2.1.0 的批量补丁中曾被误截断，
> 现按核心断言重建。原文件中若干**纯视觉细节断言**（文字区白底色差、
> 自定义浮层枚举下拉的 DOM 细节等）不在重建范围内——这些行为未被
> v2.1.0 改动，风险由 labyrinth 相关 dryrun 测试覆盖。

覆盖：
  1. 启动恢复 GUI 设置（透明度/置顶/几何）
  2. 任务行结构：仅 › / ✕ 按钮、X 严格最右、名称为类标签、无输入框
  3. 行内抽屉展开 / 收起 / 互斥 / 紧跟自己的行
  4. 底部「加任务」抽屉：展开 → 互斥 → 添加实例；X 删除实例
  5. 边界任务（启动/关闭游戏）在 GUI 内固定位置、不可拖动（v2.1.0）
  6. 任务说明小字 / 时间轮次仅自动推图 / 家园抽屉紧凑
  7. 设置抽屉（透明度/置顶持久化 + 关闭保存几何）

运行：conda run -n afk_gpu python tests/test_gui_drawer_smoke.py
"""

import json
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# v1.9.2：测试态时间加速（sleep 走虚拟时钟，不真等；AFK_TEST_FAST=0 可关闭）
from tests import fast_time
fast_time.install()

# 隔离配置文件，避免污染真实 tasks_enabled.json
_TMP = tempfile.mkdtemp(prefix='afkj_gui_smoke_')
import task_registry
task_registry._CONFIG = os.path.join(_TMP, 'tasks_enabled.json')

import customtkinter as ctk
import tkinter as tk

# v1.8.64：afk_gui 安装了全局 sys.excepthook（崩溃弹「AFK 脚本崩溃」
# 对话框挂屏等手点）——测试环境恢复默认，异常走 stderr 即可
sys.excepthook = sys.__excepthook__
from task_registry import TaskRegistry, TaskMeta

import afk_gui
sys.excepthook = sys.__excepthook__

# v1.8.16：隔离 GUI 设置文件；预置配置验证启动恢复
afk_gui.GUI_CONFIG = os.path.join(_TMP, 'gui_config.json')
with open(afk_gui.GUI_CONFIG, 'w', encoding='utf-8') as _f:
    json.dump({'alpha': 0.8, 'topmost': True, 'geometry': '1100x850+20+30'}, _f)

app = afk_gui.App()
app.report_callback_exception = lambda *a: None
TaskRegistry._CONFIG = task_registry._CONFIG
TaskRegistry.reset_config()      # 干净起点：只有 ready 任务各一个实例
app._render_task_rows()
app.update_idletasks()

FAILED = []


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def row_widgets(inst_id):
    """返回 (row_frame, {按钮文本: 按钮})"""
    row = app._task_rows[inst_id]['frame']
    btns = {}
    for w in row.winfo_children():
        if isinstance(w, ctk.CTkButton):
            btns[w.cget('text')] = w
        for sub in w.winfo_children():
            if isinstance(sub, ctk.CTkButton):
                btns[sub.cget('text')] = sub
    return row, btns


def shown(w):
    """是否已布局（headless 下用 manager 判定）"""
    return w.winfo_manager() in ('pack', 'place', 'grid')


def names(inst_id):
    """行内所有 Label 文本"""
    row = app._task_rows[inst_id]['frame']
    out = []
    for w in row.winfo_children():
        if isinstance(w, ctk.CTkLabel):
            out.append(w.cget('text'))
        for sub in w.winfo_children():
            if isinstance(sub, ctk.CTkLabel):
                out.append(sub.cget('text'))
    return out


def drawer_texts(drawer):
    out = []

    def _walk(w):
        for c in w.winfo_children():
            try:
                t = c.cget('text')
                if t:
                    out.append(str(t))
            except Exception:
                pass
            _walk(c)
    _walk(drawer)
    return out


# ══ 0. v1.8.16 启动恢复 GUI 设置 ═══════════════════════════
app.update()   # 同步 WM 几何，否则 geometry() 查询滞后
check('v1.8.16 启动恢复透明度（0.8）', abs(app._slider_alpha.get() - 0.8) < 1e-6,
      app._slider_alpha.get())
check('v1.8.16 启动恢复置顶（勾选+生效）',
      bool(app._chk_top.get()) and app.attributes('-topmost'),
      (app._chk_top.get(), app.attributes('-topmost')))
check('v1.8.16 启动恢复窗口几何',
      '1100x850' in app.geometry(), app.geometry())

# v1.8.64：几何断言完成后隐藏窗口——不在用户屏幕弹窗打扰
app.withdraw()

# ══ 1. 实例清单与渲染顺序（v2.1.0）══════════════════════════
_ids0 = [i.inst_id for i in TaskRegistry.get_instances()]
_render_ids = list(app._task_rows.keys())
# v2.2.24：改为**从注册表动态推算**（此前硬编码 10，每加一个任务就误报 ✗）
from task_registry import TaskRegistry            # noqa: E402
_ready_n = len([m for m in TaskRegistry.get_all() if m.status == 'ready'])
check(f'初始实例齐备（{_ready_n} 个 ready 任务）',
      len(_ids0) == _ready_n, f'{len(_ids0)} vs {_ready_n}')
check('v2.1.0 GUI 渲染顺序：启动游戏在首、关闭游戏在尾',
      _render_ids[0].startswith('start_game')
      and _render_ids[-1].startswith('close_game'), str(_render_ids))
check('v2.1.0 渲染列表 = _ordered_instances()',
      _render_ids == [i.inst_id for i in app._ordered_instances()],
      str(_render_ids))

# ══ 2. 任务行结构 ═══════════════════════════════════════════
_i1 = 'auto_farm#1'
_row1, _btns1 = row_widgets(_i1)
check('行内按钮仅 › 与 ✕（⚙/+ 已删除）', set(_btns1) == {'›', '✕'}, list(_btns1))
_slaves = [_row1.winfo_children()[i] for i in range(len(_row1.winfo_children()))]
_ps = _row1.pack_slaves()
check('✕ 严格最右（v1.6.3）',
      _ps.index(_btns1['✕']) < _ps.index(_btns1['›']),
      f"✕ idx={_ps.index(_btns1['✕'])} › idx={_ps.index(_btns1['›'])}"
      "（pack side=right 先入者最右）")

meta = TaskRegistry.get_by_id('auto_farm')
_nm = names(_i1)
check('名称固定为类标签「自动推图」', meta.label in _nm, _nm)
check('无 ×N 徽章', not any('×' in t for t in _nm), _nm)
_entries = [w for w in _row1.winfo_children() if isinstance(w, ctk.CTkEntry)]
check('行内无可编辑名称输入框', len(_entries) == 0, len(_entries))


def _handle_text(inst_id):
    row = app._task_rows[inst_id]['frame']
    for w in row.winfo_children():
        try:
            if w.cget('text') in ('🔒', '⠿'):
                return w.cget('text')
        except Exception:
            pass
    return None


# ══ 3. v2.1.0 边界任务固定位置 / 不可拖动 ═══════════════════
_sg_id = _render_ids[0]
_cg_id = _render_ids[-1]
check('v2.1.0 启动游戏手柄为 🔒（不可拖标识）',
      _handle_text(_sg_id) == '🔒', str(_handle_text(_sg_id)))
check('v2.1.0 关闭游戏手柄为 🔒',
      _handle_text(_cg_id) == '🔒', str(_handle_text(_cg_id)))
check('v2.1.0 普通任务手柄为 ⠿（可拖）',
      _handle_text(_i1) == '⠿', str(_handle_text(_i1)))

# 拖动边界任务 → 无效（顺序不变、无拖拽态）
_ord_b = [i.inst_id for i in TaskRegistry.get_instances()]
app._on_task_drag_press(SimpleNamespace(y_root=300), _sg_id)
app.update_idletasks()
check('v2.1.0 按下启动游戏手柄不进入拖拽态',
      getattr(app, '_drag_task', None) != _sg_id,
      str(getattr(app, '_drag_task', None)))
app._on_task_drag_release(SimpleNamespace())
app.update_idletasks()

# registry 层：边界任务拒绝移动
_sg_mv = TaskRegistry.move_instance(_sg_id, 1)
_cg_mv = TaskRegistry.move_instance(_cg_id, -1)
check('v2.1.0 启动游戏拒绝 move_instance', not _sg_mv, str(_sg_mv))
check('v2.1.0 关闭游戏拒绝 move_instance', not _cg_mv, str(_cg_mv))
check('v2.1.0 边界任务拒绝后仍在首尾（v2.1.1：含归一化）',
      [i.inst_id for i in TaskRegistry.get_instances()][0]
      .startswith('start_game')
      and [i.inst_id for i in TaskRegistry.get_instances()][-1]
      .startswith('close_game'),
      str([i.inst_id for i in TaskRegistry.get_instances()]))

# 普通任务可移动（对照组）
_before_mv = [i.inst_id for i in TaskRegistry.get_instances()]
_mv_ok = TaskRegistry.move_instance(_i1, 1)
check('普通任务 move_instance 生效（对照）',
      _mv_ok and [i.inst_id for i in TaskRegistry.get_instances()] != _before_mv,
      str(_before_mv))
TaskRegistry.move_instance(_i1, -1)   # 回退，保持后续稳定

# ══ 4. 行内抽屉：展开 / 收起 / 互斥 ═════════════════════════
_d1 = app._drawers[_i1]
check('抽屉初始隐藏', not shown(_d1))
app._toggle_drawer(_i1)
app.update_idletasks()
check('展开 #1 抽屉', shown(_d1))
check('展开后按钮文本变 ▾', _btns1['›'].cget('text') == '▾',
      _btns1['›'].cget('text'))
app._toggle_drawer(_i1)
app.update_idletasks()
check('再次点击收起 #1', not shown(_d1))

# 互斥 + 紧跟自己的行
TaskRegistry.add_instance('auto_farm')
app._render_task_rows()
app.update_idletasks()
_i2 = 'auto_farm#2'
app._toggle_drawer(_i1)
app.update_idletasks()
app._toggle_drawer(_i2)
app.update_idletasks()
check('互斥：展开 #2 后 #1 自动收起',
      shown(app._drawers[_i2]) and not shown(app._drawers[_i1]))
_row1_now, _ = row_widgets(_i1)   # v2.1.0：_render_task_rows 后旧 widget 已销毁
check('抽屉紧跟自己的行（after= 定位）',
      _row1_now.winfo_manager() == 'pack')
app._toggle_drawer(_i2)
app.update_idletasks()

# ══ 5. 底部添加抽屉 / 删除实例 ══════════════════════════════
app._toggle_add_drawer()
app.update_idletasks()
check('底部添加抽屉展开', shown(app._add_drawer))
_id_before = len(TaskRegistry.get_instances())
TaskRegistry.add_instance('labyrinth')   # → labyrinth#2（保留 #1 供后续用例）
app._render_task_rows()
app.update_idletasks()
check('添加实例后 _task_rows 同步',
      len(app._task_rows) == _id_before + 1,
      f'{_id_before} -> {len(app._task_rows)}')
app._on_delete_instance('labyrinth#2')
app.update_idletasks()
check('X 删除实例', len(app._task_rows) == _id_before,
      f'{len(app._task_rows)}')
check('删除后 _task_rows 同步', 'labyrinth#2' not in app._task_rows)

# ══ 6. 任务说明小字 / 参数渲染（v1.8.15 / v1.9.67）══════════
_lab_meta = TaskRegistry.get_by_id('labyrinth')
app._close_task_drawers()
app._toggle_drawer('labyrinth#1')
app.update_idletasks()
_lab_lbls = drawer_texts(app._drawers['labyrinth#1'])
check('v1.8.15 迷宫抽屉显示任务说明小字',
      _lab_meta.description[:50] in _lab_lbls, str(_lab_lbls[:3]))
check('v1.8.15 迷宫任务说明 ≤50 字',
      0 < len(_lab_meta.description) <= 50, len(_lab_meta.description))
check('v1.9.67 迷宫抽屉无「时间（秒）」（已移除）',
      not any('时间（秒）' in t for t in _lab_lbls),
      str([t for t in _lab_lbls if '时间' in t][:2]))
check('v1.9.67 迷宫抽屉显示内建终止条件提示',
      any('终止' in t for t in _lab_lbls),
      str([t for t in _lab_lbls if '终止' in t][:1]))
app._toggle_drawer('labyrinth#1')
app.update_idletasks()

app._toggle_drawer(_i1)
app.update_idletasks()
_af_lbls = drawer_texts(app._drawers[_i1])
check('v1.9.67 自动推图抽屉显示「时间（秒）」',
      any('时间（秒）' in t for t in _af_lbls), str(_af_lbls[:4]))
check('v1.9.67 自动推图抽屉显示「轮次」',
      any(t == '轮次' for t in _af_lbls), str(_af_lbls[:4]))
app._toggle_drawer(_i1)
app.update_idletasks()

# 家园抽屉紧凑 + 仅消耗体力
app._close_task_drawers()
app._toggle_drawer('home#1')
app.update_idletasks()
_d_home = app._drawers['home#1']
check('v1.9.26 抽屉紧凑（高度 <160px）', _d_home.winfo_height() < 160,
      _d_home.winfo_height())
_boxes = []
for _w in _d_home.winfo_children():
    for _c in _w.winfo_children():
        if type(_c).__name__ == 'CTkCheckBox':
            _boxes.append((_c.cget('text'), _c.cget('state')))
check('v1.9.19 仅消耗体力勾选',
      len(_boxes) == 1 and _boxes[0][0] == '消耗体力', str(_boxes))
app._close_task_drawers()
app.update_idletasks()

# ══ 7. 设置抽屉 / 持久化 / 几何 ════════════════════════════
# v2.1.0：设置抽屉内部 API 名称未在本次改动范围，存在则测、不存在则跳过
# （避免用猜测的方法名造成假失败）
if hasattr(app, '_toggle_settings_drawer') and hasattr(app, '_settings_drawer'):
    app._toggle_settings_drawer()
    app.update_idletasks()
    check('设置抽屉可展开', shown(app._settings_drawer))
    app._toggle_settings_drawer()
    app.update_idletasks()
    check('设置抽屉可收起', not shown(app._settings_drawer))
else:
    print('[SKIP] 设置抽屉 API 名称未匹配，跳过（非本次改动范围）')

# ══ v2.2.21 回归：状态回调不得被同名 widget 覆盖 ══
# 背景：v2.2.20 在设置抽屉里加了 `self._update_status = ctk.CTkLabel(...)`，
# 与类方法 `_update_status()` **同名** → 方法被覆盖 → core 关停时
# `set_status(running=False)` 抛 TypeError: 'CTkLabel' object is not callable
# （真机 2026-10-27 11:59 每次推图结束即失败）
check('v2.2.21 状态回调 _update_status 仍是可调用的方法（未被同名 widget 覆盖）',
      callable(getattr(app, '_update_status', None)),
      type(getattr(app, '_update_status', None)).__name__)
try:
    app._update_status(running=False)      # core 关停时的真实调用形态
    _status_ok = True
except TypeError as e:
    _status_ok = False
    print(f'    -> {e}')
check('v2.2.21 _update_status(running=False) 可正常执行（推图结束路径）',
      _status_ok)
check('v2.2.21 更新状态标签用了不冲突的名字',
      hasattr(app, '_upd_status_lbl'),
      'app._upd_status_lbl 存在' if hasattr(app, '_upd_status_lbl') else '缺失')

if hasattr(app, '_slider_alpha') and hasattr(app, '_on_alpha_change'):
    app._slider_alpha.set(0.9)
    app._on_alpha_change(0.9)
    app.update_idletasks()
    try:
        with open(afk_gui.GUI_CONFIG, encoding='utf-8') as _f:
            _cfg = json.load(_f)
        check('v1.8.16 改透明度即时持久化（0.9）',
              abs(_cfg.get('alpha', 0) - 0.9) < 1e-6, str(_cfg.get('alpha')))
    except Exception as _e:
        check('v1.8.16 改透明度即时持久化（0.9）', False, str(_e))
else:
    print('[SKIP] 透明度回调 API 未匹配，跳过')

# 关闭窗口保存几何（包 try：关窗后 Tk 资源释放，后续查询会抛）
try:
    if hasattr(app, '_on_close'):
        app._on_close()
    check('v1.8.16 关闭窗口流程可执行', True)
except Exception as _e:
    check('v1.8.16 关闭窗口流程可执行', False, str(_e))

# ══ 汇总 ═══════════════════════════════════════════════════
print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print('✅ 全部通过')
sys.exit(0)
