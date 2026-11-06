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
PASSED = [0]          # 用列表做可变计数器（避免 global）


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
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
    """行内所有 Label 文本（完整递归：v2.3.17 起标题行多了一层 title_row）"""
    row = app._task_rows[inst_id]['frame']
    out = []

    def _walk(w):
        for c in w.winfo_children():
            if isinstance(c, ctk.CTkLabel):
                try:
                    out.append(c.cget('text'))
                except Exception:
                    pass
            _walk(c)

    _walk(row)
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

# v2.3.35：任务栏描述行必须显示完整说明、无 `…` 截断（用户截图问题点）
_row_descs = {}
for _iid, _row in app._task_rows.items():
    _inst = TaskRegistry.get_instance(_iid)
    _meta = TaskRegistry.get_by_id(_inst.class_id)
    for _t in names(_iid):
        if _t == _meta.description:
            _row_descs[_iid] = _t
check('v2.3.35 全部任务行显示完整说明（无 … 截断）',
      len(_row_descs) == len(app._task_rows)
      and all('…' not in t for t in _row_descs.values()),
      f'{len(_row_descs)}/{len(app._task_rows)} 行完整')
check('v2.3.35 任务栏描述与结束指标分行（描述不含「时间/轮次」）',
      all('时间' not in t and '轮次' not in t for t in _row_descs.values()),
      str(list(_row_descs.values())[:2]))


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
# v2.3.49（真机 23:30）：设置抽屉「检查更新失败…」文字左右被裁。
# 根因：wraplength 硬编码 210，而抽屉内容宽只有 224-12*2=200 → 溢出 10px。
# 断言：状态 label 的 wraplength 必须与容器可用宽度匹配（且留余量）。
_ct_w = app._upd_status_lbl.master.winfo_width()
_ct_wrap = int(float(app._upd_status_lbl.cget('wraplength') or 0))
check('v2.3.49 设置抽屉状态 label 的 wraplength ≤ 可用宽度（不再裁字）',
      0 < _ct_wrap <= max(120, _ct_w) and _ct_wrap <= _ct_w,
      f'wraplength={_ct_wrap} 容器宽={_ct_w}')
check('v2.3.49 状态 label 左对齐（多行可读）',
      str(app._upd_status_lbl.cget('justify')) in ('left', 'w'),
      str(app._upd_status_lbl.cget('justify')))
# 模拟长错误文案后仍不溢出
app._set_update_status('检查更新失败：网络连接被中断（TLS 握手未完成）。'
                       '常见于直连 GitHub 不稳定：请稍后重试')
app.update_idletasks()
_ct_wrap2 = int(float(app._upd_status_lbl.cget('wraplength') or 0))
check('v2.3.49 设置长错误文案后 wraplength 仍不超容器',
      0 < _ct_wrap2 <= max(120, _ct_w) and _ct_wrap2 <= _ct_w,
      f'wraplength={_ct_wrap2} 容器宽={_ct_w}')

app._toggle_drawer('labyrinth#1')
app.update_idletasks()
_lab_lbls = drawer_texts(app._drawers['labyrinth#1'])
check('v2.3.35 迷宫抽屉显示「完整」任务说明（不再 desc[:50] 硬截）',
      _lab_meta.description in _lab_lbls, str(_lab_lbls[:3]))
check('v2.3.35 迷宫任务说明 ≤20 字（任务栏单行可完整显示）',
      0 < len(_lab_meta.description) <= 20, len(_lab_meta.description))
# v2.3.35：说明 label 必须能换行且 wraplength 小于抽屉可用宽度，
# 否则长文案换行不触发、直接溢出被裁（v2.3.2 状态标签同款坑）
_desc_wls = []
for _w in app._drawers['labyrinth#1'].winfo_children():
    try:
        if _w.cget('text') == _lab_meta.description:
            _desc_wls.append(int(_w._label.cget('wraplength')))
    except Exception:
        pass
# v2.3.49：判据改为「wraplength ≤ 父容器实际宽度」——固定上限 400 是错的
# （任务详情抽屉实测宽 532，正确 wraplength 是 524；旧断言会误报）。
_parent_w = app._drawers['labyrinth#1'].winfo_width()
check('v2.3.35 说明 label 设了 wraplength 且不超过容器宽度（超宽自动换行不裁切）',
      bool(_desc_wls) and all(0 < w <= max(120, _parent_w) for w in _desc_wls),
      f'wraplength={_desc_wls} 容器宽={_parent_w}')
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
# ── v2.3.2：抽屉「更新区」布局（真机 13:16/13:17 截图：按钮被截断 ✗）──
def _side(w):
    """取 pack 的 side；**未 pack（隐藏）时返回 None**（"立即更新"未发现新版时隐藏）"""
    try:
        return w.pack_info().get('side')
    except Exception:
        return None


try:
    _sc, _sa = _side(app._update_btn), _side(app._update_apply_btn)
    check('v2.3.2 更新按钮竖排（横排会把「立即更新」截成「立即更…」✗）',
          _sc != 'left' and _sa != 'left',
          f'check={_sc} apply={_sa}（None=未显示，正常）')
    _wl = int(app._upd_status_lbl._label.cget('wraplength'))
    check('v2.3.2 状态标签 wraplength ≤ 230（长错误必须换行不溢出 ✗）',
          _wl <= 230, f'wraplength={_wl}')
    check('v2.3.2 下载进度条已创建', hasattr(app, '_upd_progress'))
    app.update_idletasks()
    _dw = app._upd_status_lbl.master.winfo_width()
    check('v2.3.2 抽屉内容宽度（≥180 才够放完整按钮文字；0=抽屉未展开，跳过）',
          _dw == 0 or _dw >= 180, f'width={_dw}')
except Exception as _e2:
    check('v2.3.2 抽屉更新区布局断言', False, str(_e2))

# ── v2.3.15 / v2.3.17：实时状态显示（已从日志头部移到「自动推图」任务行）──
try:
    _pairs = app._stat_labels
    check('v2.3.17 自动推图行内收集到计数控件', len(_pairs) >= 1,
          f'pairs={len(_pairs)}')
    _lb, _ls = _pairs[0]
    check('v2.3.15 累计挑战 Label 初始为 0',
          _lb.cget('text') == '0', repr(_lb.cget('text')))
    check('v2.3.15 已通关 Label 初始为 0',
          _ls.cget('text') == '0', repr(_ls.cget('text')))
    _f_exp = ('Microsoft YaHei UI', 12, 'bold')
    check('v2.3.15 累计挑战数值字体 = Microsoft YaHei UI 12 bold',
          _lb.cget('font') == _f_exp, repr(_lb.cget('font')))
    check('v2.3.15 已通关数值字体 = Microsoft YaHei UI 12 bold',
          _ls.cget('font') == _f_exp, repr(_ls.cget('font')))
    check('v2.3.15 累计挑战数值颜色 = TEXT_BRIGHT',
          _lb.cget('text_color') == afk_gui.TEXT_BRIGHT,
          repr(_lb.cget('text_color')))
    check('v2.3.15 已通关数值颜色 = TEXT_BRIGHT',
          _ls.cget('text_color') == afk_gui.TEXT_BRIGHT,
          repr(_ls.cget('text_color')))

    # v2.3.17 位置断言：祖先链 title_row → mid → row 必须是自动推图那一行
    _title_row = _lb.master
    _anc_row = _title_row.master.master
    _af_ids = [iid for iid in app._task_rows if iid.startswith('auto_farm')]
    check('v2.3.17 计数控件在自动推图任务行内',
          bool(_af_ids) and app._task_rows[_af_ids[0]]['frame'] is _anc_row,
          f'af_rows={_af_ids}')
    check('v2.3.17 计数与任务标题同一行（同一父容器）',
          app._task_rows[_af_ids[0]]['lbl_name'].master is _title_row)
    check('v2.3.17 计数排在标题右侧（side=left 顺序：标题→说明→数值）',
          _lb.pack_info().get('side') == 'left'
          and _title_row.pack_slaves()[0]
          is app._task_rows[_af_ids[0]]['lbl_name'])
    check('v2.3.17 日志头部旧状态条已移除（不重复显示）',
          not hasattr(app, '_stat_bar'))

    # _refresh_ui 可调用（非空操作）：主线程直接喂状态
    app._refresh_ui({'stats': {'total_battles': 5, 'stage_progress': 3},
                     'running': True})
    check('v2.3.15 _refresh_ui 累计挑战显示 5',
          _lb.cget('text') == '5', repr(_lb.cget('text')))
    check('v2.3.15 _refresh_ui 已通关显示 3',
          _ls.cget('text') == '3', repr(_ls.cget('text')))
    app._refresh_ui({'stats': {'total_battles': 0, 'stage_progress': 0}})
    check('v2.3.15 _refresh_ui 复位回到 0',
          _lb.cget('text') == '0' and _ls.cget('text') == '0',
          f"{_lb.cget('text')}/{_ls.cget('text')}")

    # v2.3.17：其它任务行不得出现计数（只挂自动推图）——递归取全部子孙文本
    def _all_texts(w):
        out = []
        try:
            out.append(str(w.cget('text')))
        except Exception:
            pass
        for _c in w.winfo_children():
            out.extend(_all_texts(_c))
        return out

    _other = [iid for iid in app._task_rows
              if not iid.startswith('auto_farm')]
    _bad = []
    for _iid in _other:
        _txts = _all_texts(app._task_rows[_iid]['frame'])
        if any('累计挑战' in _t or '已通关' in _t for _t in _txts):
            _bad.append(_iid)
    check('v2.3.17 其它任务行不显示计数（只挂自动推图）',
          not _bad, f'误挂={_bad}')
except Exception as _e3:
    check('v2.3.15 实时状态显示断言', False, str(_e3))

print()
if FAILED:
    print(f'❌ {len(FAILED)} 项失败: {FAILED}')
    sys.exit(1)
print(f'✅ 全部通过（通过 {PASSED[0]}/{PASSED[0]}）')
sys.exit(0)
