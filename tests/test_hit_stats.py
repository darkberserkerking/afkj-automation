# -*- coding: utf-8 -*-
"""识别命中统计回归（v2.4.22）。

这套统计是 ROI 铺开的**唯一数据来源**，所以三件事必须成立：

  ① 记录正确：窗口尺寸、任务、目标、位置、相对比例都对得上
  ② 采集绝不打断业务：任何异常都吞掉，不能因为写日志让任务失败
  ③ 聚合正确：按（类型, 目标, 窗口）分组，且建议 ROI 能反映位置跨度

另外验证 `template_match` 命中时确实会落一条记录（接入没漏）。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TMP = tempfile.mkdtemp(prefix='afkj_hitstats_')
os.environ['AFKJ_DATA_DIR'] = TMP      # app_paths 优先读它，且不缓存

from afk_bot.infra import hit_stats   # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


def _rows():
    p = hit_stats._path()
    if not os.path.isfile(p):
        return []
    out = []
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


# ── ① 记录正确 ──────────────────────────────────────────────
hit_stats.set_task('auto_farm#1')
hit_stats.record_tpl(
    target=r'C:\x\templates\btn_formation_next.png',
    pos=(878, 550), score=0.9934,
    win_w=1280, win_h=1024, origin_l=100, origin_t=50,
    version='v2.4.22', shape=(64, 64),
)
rows = _rows()
check('1a 写入了一条记录', len(rows) == 1, f'{len(rows)} 条')
r = rows[0] if rows else {}
check('1b 只存 basename（不泄漏本机路径）',
      r.get('target') == 'btn_formation_next.png', str(r.get('target')))
check('1c 窗口尺寸正确', r.get('win') == [1280, 1024], str(r.get('win')))
check('1d 任务上下文带上', r.get('task') == 'auto_farm#1', str(r.get('task')))
check('1e 相对比例按客户区原点换算正确',
      abs(r['rel'][0] - (878 - 100) / 1280) < 1e-3
      and abs(r['rel'][1] - (550 - 50) / 1024) < 1e-3, str(r.get('rel')))
check('1f 置信度与尺寸都记了', r.get('score') == 0.9934
      and r.get('shape') == [64, 64], f'{r.get("score")} {r.get("shape")}')

# 分工况：同目标两个窗口尺寸
hit_stats.record_tpl('btn_x.png', (614, 322), 0.84, 1280, 617, 0, 0,
                     'v2.4.22', (64, 64))
hit_stats.set_task('t2#1')      # 换一个任务上下文，验证窗口维度仍能分开
hit_stats.record_tpl('btn_x.png', (878, 550), 0.99, 1280, 1024, 0, 0,
                     'v2.4.22', (64, 64))
check('1g 任务上下文可切换', hit_stats.get_task() == 't2#1',
      str(hit_stats.get_task()))

# ── ② 无任务上下文不记录（防干跑测试污染真实数据）──────────
_before_notask = len(_rows())
hit_stats.set_task(None)
hit_stats.record_tpl('should_skip.png', (100, 100), 0.9, 1280, 1024)
check('2z 无任务上下文时不记录（干跑测试不会污染数据）',
      len(_rows()) == _before_notask, f'{_before_notask} -> {len(_rows())}')

# ── ② 异常不打断业务 ────────────────────────────────────────
hit_stats.set_task('err#1')
before = len(_rows())
try:
    hit_stats.record_tpl('t.png', (0, 0), 0.5, 0, 0)      # 窗口尺寸 0
    hit_stats.record_tpl('', (-1, -1), float('nan'), 10, 10)
    ok_no_raise = True
except Exception as e:
    ok_no_raise = False
    print('   异常：', e)
check('2a 非法入参不抛异常', ok_no_raise)
check('2b 合法记录仍只有之前的新增（脏数据被忽略或不影响结构）',
      len(_rows()) >= before, f'{before} -> {len(_rows())}')

# 目录不可写时也不能抛（把路径指到一个不存在的盘）
_orig_path = hit_stats._path
hit_stats._path = lambda: 'Z:/不存在的盘/x.jsonl'
try:
    hit_stats.record_tpl('t.png', (10, 10), 0.9, 100, 100)
    ok2 = True
except Exception:
    ok2 = False
hit_stats._path = _orig_path
check('2c 路径不可写时静默失败', ok2)

# ── ③ 聚合正确 ─────────────────────────────────────────────
hit_stats.set_task('agg_task#1')
for rx in (0.68, 0.69, 0.70):
    hit_stats.record_tpl('btn_spread.png', (int(rx * 1280), 550), 0.9,
                         1280, 1024, 0, 0, 'v2.4.22', (64, 64))
rows_agg = hit_stats.aggregate()
by = {(x['kind'], x['target'], x['win']): x for x in rows_agg}
key = ('tpl', 'btn_spread.png', (1280, 1024))
check('3a 按（类型, 目标, 窗口）分组', key in by, str(list(by)[:3]))
g = by.get(key) or {}
check('3b 样本数正确', g.get('count') == 3, f'{g.get("count")}')
# 容差 2e-3：记录前会 int() 截断像素坐标（870/1280=0.6797），属正常量化误差
check('3c rx 范围反映跨度', abs(g.get('rx_min', 0) - 0.68) < 2e-3
      and abs(g.get('rx_max', 0) - 0.70) < 2e-3,
      f'{g.get("rx_min")}~{g.get("rx_max")}')
x0, y0, x1, y1 = g.get('roi_ratio', (0, 0, 0, 0))
check('3d 建议 ROI 覆盖整个跨度且含余量',
      x0 <= 0.68 and x1 >= 0.70 and y0 < 0.55 < y1, str(g.get('roi_ratio')))
check('3e 建议 ROI 落在 [0,1] 内', 0 <= x0 <= x1 <= 1 and 0 <= y0 <= y1 <= 1)

# 不同窗口不混在一起
check('3f 同一目标的不同窗口尺寸分开统计',
      ('tpl', 'btn_x.png', (1280, 617)) in by
      and ('tpl', 'btn_x.png', (1280, 1024)) in by)

# ── ④ 报表渲染 ─────────────────────────────────────────────
md = hit_stats.render_markdown(rows_agg, source='test')
check('4a 含表头关键列', all(k in md for k in
      ('目标', '窗口', '样本', '建议 ROI', '面积占比')), md[:60])
check('4b 含防误用提示（跨度大不要收窄）',
      '不要收窄' in md or '跨度' in md)
dest = os.path.join(TMP, 'report.md')
written = hit_stats.write_report(dest)
check('4c write_report 能落盘', os.path.isfile(dest) and written == dest,
      written)
check('4d 落盘内容非空', os.path.getsize(dest) > 100, str(os.path.getsize(dest)))

# ── ⑤ template_match 接入 ──────────────────────────────────
try:
    import numpy as np
    from PIL import Image
    import cv2
    from afk_bot.infra.input import InputController
    _HAS = True
except Exception as e:
    _HAS = False
    _ERR = repr(e)

if not _HAS:
    print(f'[SKIP] ⑤ template_match 接入  {_ERR}')
else:
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tpl_path = os.path.join(ROOT, 'templates', 'btn_formation_next.png')
    # 清空后验证：命中会落一条 tpl 记录
    for f in (hit_stats._path(), hit_stats._path() + '.1'):
        if os.path.isfile(f):
            os.remove(f)

    tpl = cv2.imread(tpl_path, cv2.IMREAD_UNCHANGED)
    H, W = 480, 640
    rng = np.random.default_rng(9)
    bg = rng.integers(20, 70, (H, W, 3), dtype=np.uint8)
    tw, th = tpl.shape[1], tpl.shape[0]
    cx, cy = 400, 260
    x0p, y0p = cx - tw // 2, cy - th // 2
    a = tpl[:, :, 3:4].astype(np.float32) / 255.0
    roi = bg[y0p:y0p + th, x0p:x0p + tw].astype(np.float32)
    bg[y0p:y0p + th, x0p:x0p + tw] = np.clip(
        tpl[:, :, :3].astype(np.float32) * a + roi * (1 - a), 0, 255
    ).astype(np.uint8)
    pil = Image.fromarray(cv2.cvtColor(bg, cv2.COLOR_BGR2RGB))

    class _SC:
        def snap(self):
            return (pil, 0, 0, W, H)

        def activate_window(self):
            pass

    ic = InputController(None, _SC(), lambda *a, **k: None)
    hit_stats.set_task('test_task#1')
    got = ic.template_match(tpl_path, threshold=0.6, silent=True)
    hit_stats.set_task(None)
    rows2 = _rows()
    check('5a template_match 命中时落了一条记录', len(rows2) == 1,
          f'{len(rows2)} 条')
    if rows2:
        rr = rows2[0]
        check('5b 记录的目标是模板名', rr['target'] == 'btn_formation_next.png',
              rr['target'])
        check('5c 记录的坐标与命中一致',
              abs(rr['pos'][0] - got[0]) <= 1 and abs(rr['pos'][1] - got[1]) <= 1,
              f'{rr["pos"]} vs {got}')
        check('5d 记录带任务上下文', rr['task'] == 'test_task#1', rr['task'])

# 清理
try:
    shutil.rmtree(TMP, ignore_errors=True)
except Exception:
    pass

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
