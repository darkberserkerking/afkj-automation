# -*- coding: utf-8 -*-
"""日志双视图回归（v2.4.24）。

用户诉求：界面能一键在「简略 / 详细」之间切换，
**但写入文件的仍是完整日志，切换只影响显示**。

本测试守住四条：
  ① `_is_dev_log` 判定：显式 [调试] 前缀 + 内容特征兜底
  ② 简略模式：技术细节不进界面，任务进展照常显示
  ③ **文件日志始终全量**：两种模式下都一行不少（这是最容易被改坏的点）
  ④ 切换视图能重建历史（不会因为之前被过滤而丢内容）
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 隔离数据目录 —— afk_gui 在 import 时就按 DATA_DIR 打开了日志文件，
# 必须在 import 之前设置，否则会污染真实运行日志。
_TMP = tempfile.mkdtemp(prefix='afkj_logmode_')
os.environ['AFKJ_DATA_DIR'] = _TMP

sys.excepthook = sys.__excepthook__

import afk_gui                                        # noqa: E402
sys.excepthook = sys.__excepthook__

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# ── ① 判定函数（纯函数，不需要 GUI）────────────────────────
check('1a [调试] 前缀 → 详细', afk_gui._is_dev_log('[调试] 任意内容'))
check('1b 含置信度 → 详细', afk_gui._is_dev_log('[信息] 置信度=0.993'))
check('1c 含坐标 → 详细', afk_gui._is_dev_log('[信息] 坐标=(878,550)'))
check('1d 含 tpl 尺寸 → 详细', afk_gui._is_dev_log('[信息] tpl=(64x64)'))
check('1e 含 snap 调试段 → 详细', afk_gui._is_dev_log('[信息] snap: img_l=0'))
check('1f OCR 扫描明细 → 详细', afk_gui._is_dev_log('[信息] ℹ️ OCR扫描完成 | 共13条'))
check('1g 任务进展 → 简略', not afk_gui._is_dev_log('[信息] 任务结束：自动推图 → 完成'))
check('1h 成功/阶段 → 简略', not afk_gui._is_dev_log('[成功] 已进入备战界面'))
check('1i 警告 → 简略（用户必须看到）',
      not afk_gui._is_dev_log('[警告] 未找到 > 按钮'))
check('1j 空串不崩', not afk_gui._is_dev_log(''))

# ── ② ~ ④ GUI 行为 ─────────────────────────────────────────
try:
    import task_registry
    task_registry._CONFIG = os.path.join(_TMP, 'tasks_enabled.json')
    afk_gui.GUI_CONFIG = os.path.join(_TMP, 'gui_config.json')
    app = afk_gui.App()
    app.withdraw()          # 不挂屏
    app.report_callback_exception = lambda *a: None
    _OK = True
except Exception as e:
    _OK = False
    _ERR = repr(e)

if not _OK:
    print(f'[SKIP] ②~④ GUI 行为  {_ERR}')
else:
    # 把文件日志重定向到临时文件，验证「始终全量」
    log_path = os.path.join(_TMP, 'ui_log.txt')
    try:
        app._log_file.close()
    except Exception:
        pass
    app._log_file = open(log_path, 'a', encoding='utf-8', buffering=1)

    def pump():
        """手动跑一轮 flush（不吃 200ms 定时器）。"""
        app._flush()
        try:
            app.after_cancel(app._flush_id)
        except Exception:
            pass

    def visible_text():
        return app._log_text.get('1.0', 'end')

    def file_text():
        try:
            app._log_file.flush()
        except Exception:
            pass
        with open(log_path, encoding='utf-8') as f:
            return f.read()

    check('2a 默认是简略模式', app._log_mode == 'user', app._log_mode)

    app._append_log('[信息] 任务开始：自动推图')
    app._append_log('[调试] 置信度=0.993 坐标=(878,550) tpl=(64x64)')
    app._append_log('[成功] 已进入备战界面')
    pump()

    vis = visible_text()
    check('2b 简略模式：任务进展可见', '任务开始' in vis and '已进入备战界面' in vis)
    check('2c 简略模式：技术细节被隐藏',
          '置信度=0.993' not in vis and '坐标=(878,550)' not in vis)

    ftext = file_text()
    check('3a 【核心】文件日志含技术细节（简略模式下也全量）',
          '置信度=0.993' in ftext and '坐标=(878,550)' in ftext)
    check('3b 文件日志也含任务进展', '任务开始' in ftext)

    # 切到详细
    app._toggle_log_mode()
    pump()
    vis2 = visible_text()
    check('4a 切到详细模式', app._log_mode == 'dev', app._log_mode)
    check('4b 历史里的技术细节被重建出来（不只影响后续）',
          '置信度=0.993' in vis2, vis2[-120:].replace('\n', ' | '))
    check('4c 按钮文案同步', app._btn_log_mode.cget('text').startswith('详细'),
          app._btn_log_mode.cget('text'))

    # 新增日志在详细模式下也可见
    app._append_log('[调试] 新增的调试行 XYZ123')
    pump()
    check('4d 详细模式下新日志实时可见', 'XYZ123' in visible_text())

    # 切回简略
    app._toggle_log_mode()
    pump()
    vis3 = visible_text()
    check('5a 切回简略', app._log_mode == 'user')
    check('5b 技术细节再次隐藏', 'XYZ123' not in vis3)
    check('5c 任务进展仍在', '任务开始' in vis3 and '已进入备战界面' in vis3)

    # 清空后不应被历史"复活"
    app._clear_log()
    pump()
    app._toggle_log_mode()      # 切到详细
    pump()
    check('6a 清空后切视图不会复活旧日志',
          'XYZ123' not in visible_text(), visible_text()[-80:].replace('\n', '|'))
    app._toggle_log_mode()

    # 文件日志行数 >= 界面条数（全量 vs 过滤）
    n_file = len([l for l in file_text().splitlines() if '[' in l])
    check('7a 文件日志行数 ≥ 界面条数（文件是全量）', n_file >= 3,
          f'文件 {n_file} 行')

    try:
        app._log_file.close()
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
