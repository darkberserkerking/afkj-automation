# -*- coding: utf-8 -*-
"""识别命中统计（v2.4.22，dev 阶段 1.5 的数据来源）。

目的：让 ROI 铺开**有数据依据**，而不是凭印象猜坐标。

每次模板匹配成功就追加一行 jsonl：

    {"ts":"2026-11-25T07:20:11","ver":"v2.4.22","task":"auto_farm#1",
     "win":[1284,1026],"kind":"tpl","target":"btn_formation_next.png",
     "score":0.993,"pos":[878,550],"rel":[0.684,0.536]}

`rel` 是相对客户区左上角的比例，跨分辨率可聚合；`win` 保留原始尺寸，
便于区分不同窗口布局（同一个按钮在 1280x1024 与 1280x617 下 rx 差很多）。

设计约束（都为了保证"采集绝不影响主流程"）：
- **任何异常都吞掉**，不抛给调用方
- 线程安全（多任务/后台线程并发写）
- 单文件 4MB 上限，超了滚到 `.1`（覆盖旧档），避免无限增长

配套：
    python tools/roi_report.py           # 聚合成 ROI 建议表
    python tools/roi_report.py --write   # 同时写入 docs/ROI-命中统计表.md
"""
import json
import os
import threading
import time
from typing import Optional, Sequence

_LOCK = threading.Lock()
_TASK: Optional[str] = None
_MAX_BYTES: int = 4 * 1024 * 1024
_FILE_NAME: str = 'hit_stats.jsonl'
# 最近一次截图的窗口几何 (w, h, origin_l, origin_t)。
# OCR 的 find_text 是纯函数、拿不到窗口尺寸，靠这个缓存补上下文；
# 由 ocr_screen / template_match 在截图后写入。
_WIN: tuple = (0, 0, 0, 0)


def set_window(win_w: int, win_h: int,
               origin_l: int = 0, origin_t: int = 0) -> None:
    """更新最近窗口几何（供没有窗口信息的调用方使用）。"""
    global _WIN
    try:
        _WIN = (int(win_w), int(win_h), int(origin_l), int(origin_t))
    except Exception:
        pass


def _path() -> str:
    try:
        import app_paths
        return app_paths.data_path(_FILE_NAME)
    except Exception:
        return ''


def set_task(task_id: Optional[str]) -> None:
    """标记当前任务（由调度器在任务开始/结束时设置）。"""
    global _TASK
    try:
        _TASK = str(task_id) if task_id else None
    except Exception:
        _TASK = None


def get_task() -> Optional[str]:
    return _TASK


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.isfile(path) and os.path.getsize(path) > _MAX_BYTES:
            bak = path + '.1'
            if os.path.isfile(bak):
                os.remove(bak)
            os.replace(path, bak)
    except Exception:
        pass


def record_tpl(
    target: str,
    pos: Sequence[int],
    score: float,
    win_w: int,
    win_h: int,
    origin_l: int = 0,
    origin_t: int = 0,
    version: str = '',
    shape: Optional[Sequence[int]] = None,
) -> None:
    """记录一次模板匹配命中。

    Args:
        target: 模板名（只存 basename，不泄漏本机路径）
        pos: 命中的屏幕绝对坐标 (x, y)
        score: 置信度
        win_w/win_h: 客户区宽高
        origin_l/origin_t: 客户区左上角的屏幕坐标（用于换算 rel）
    """
    try:
        # 没有任务上下文 = 不是任务运行（干跑测试、GUI 手动操作、模块自检）
        # → 不记录。这条闸门让所有 dryrun 测试天然免疫于污染，
        # 也保证统计只反映「任务实际跑起来时」的命中分布。
        if not _TASK:
            return
        if not win_w or not win_h:
            return
        path = _path()
        if not path:
            return
        rel_x = (float(pos[0]) - float(origin_l)) / float(win_w)
        rel_y = (float(pos[1]) - float(origin_t)) / float(win_h)
        row = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'ver': version,
            'task': _TASK or '',
            'win': [int(win_w), int(win_h)],
            'kind': 'tpl',
            'target': os.path.basename(str(target)),
            'score': round(float(score), 4),
            'pos': [int(pos[0]), int(pos[1])],
            'rel': [round(rel_x, 4), round(rel_y, 4)],
            'shape': ([int(shape[0]), int(shape[1])] if shape else None),
        }
        line = json.dumps(row, ensure_ascii=False) + '\n'
        with _LOCK:
            _rotate_if_needed(path)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception:
        # 采集绝不打断业务
        pass


def aggregate(path: Optional[str] = None) -> list:
    """把 jsonl 聚合成「每个（类型, 目标, 窗口）一行」的统计。

    返回 list[dict]，字段：
        kind/target/win/count/rx_min/rx_max/ry_min/ry_max/
        score_med/tasks/roi_ratio/area
    roi_ratio 为建议搜索区（比例形式 x0,y0,x1,y1），已含模板半宽与余量。
    """
    p = path or _path()
    if not p or not os.path.isfile(p):
        return []
    groups = {}
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                rel = row.get('rel')
                win = row.get('win')
                if not rel or not win or len(win) != 2:
                    continue
                key = (row.get('kind') or '?', row.get('target') or '?',
                       int(win[0]), int(win[1]))
                g = groups.setdefault(key, {'rel': [], 'score': [],
                                            'tasks': set(), 'shape': [0, 0]})
                g['rel'].append((float(rel[0]), float(rel[1])))
                g['score'].append(float(row.get('score') or 0))
                if row.get('task'):
                    g['tasks'].add(row['task'])
                sh = row.get('shape')
                if sh and len(sh) == 2:
                    g['shape'][0] = max(g['shape'][0], int(sh[0]))
                    g['shape'][1] = max(g['shape'][1], int(sh[1]))
    except Exception:
        return []

    out = []
    PAD = 0.04          # 中心点外的安全余量
    for (kind, target, w, h), g in groups.items():
        xs = [r[0] for r in g['rel']]
        ys = [r[1] for r in g['rel']]
        half_w = (g['shape'][0] / w / 2) if w else 0.0
        half_h = (g['shape'][1] / h / 2) if h else 0.0
        x0 = max(0.0, min(xs) - half_w - PAD)
        x1 = min(1.0, max(xs) + half_w + PAD)
        y0 = max(0.0, min(ys) - half_h - PAD)
        y1 = min(1.0, max(ys) + half_h + PAD)
        sc = sorted(g['score'])
        out.append({
            'kind': kind, 'target': target, 'win': (w, h),
            'count': len(xs),
            'rx_min': min(xs), 'rx_max': max(xs),
            'ry_min': min(ys), 'ry_max': max(ys),
            'score_med': sc[len(sc) // 2] if sc else 0.0,
            'tasks': sorted(g['tasks'])[:6],
            'roi_ratio': (round(x0, 3), round(y0, 3),
                          round(x1, 3), round(y1, 3)),
            'area': round((x1 - x0) * (y1 - y0), 4),
        })
    out.sort(key=lambda r: (-r['count'], r['target']))
    return out


def render_markdown(rows: list, source: str = '') -> str:
    """把 aggregate() 的结果渲染成 Markdown 表格。"""
    lines = []
    lines.append('# 识别命中统计表（ROI 依据）')
    lines.append('')
    lines.append('> 由 `afk_bot/infra/hit_stats.py` 自动生成，'
                 '每次任务全部结束后更新。')
    lines.append('> 数据来源：`%LOCALAPPDATA%\\AFKJ自动化工具\\hit_stats.jsonl`')
    if source:
        lines.append(f'> 生成时间：{source}')
    lines.append('')
    if not rows:
        lines.append('_暂无数据。跑一轮任务后会自动产生。_')
        lines.append('')
        lines.append('## 采集怎么开始')
        lines.append('')
        lines.append('1. 正常跑一轮任务（GUI 点「运行」，让任务实际执行）')
        lines.append('2. 每次模板匹配命中都会追加一行到 '
                     '`hit_stats.jsonl`；OCR 的 `find_text` 命中同理')
        lines.append('3. **全部任务结束后自动刷新本表**'
                     '（同时写入数据目录的 `ROI-命中统计表.md`）')
        lines.append('4. 开发侧想看仓库内的版本：'
                     '`python tools/roi_report.py --write`')
        lines.append('')
        lines.append('## 采集什么、不采集什么')
        lines.append('')
        lines.append('| 采 | 不采 |')
        lines.append('|---|---|')
        lines.append('| 模板名（仅 basename）、命中坐标、置信度、'
                     '模板尺寸、窗口尺寸、任务名、时间 | '
                     '截图画面内容、任何账号/角色名、绝对路径 |')
        return '\n'.join(lines) + '\n'

    wins = sorted({(r['win'][0], r['win'][1]) for r in rows})
    lines.append(f'- 样本目标数：**{len(rows)}**')
    lines.append(f'- 窗口尺寸：**{len(wins)}** 种 '
                 + '、'.join(f'{w}x{h}' for w, h in wins))
    lines.append(f'- 样本总数：**{sum(r["count"] for r in rows)}**')
    lines.append('')
    lines.append('## 命中位置与建议 ROI')
    lines.append('')
    lines.append('| 类型 | 目标 | 窗口 | 样本 | rx 范围 | ry 范围 | '
                 '建议 ROI（比例） | 面积占比 | 置信度中位 |')
    lines.append('|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        w, h = r['win']
        x0, y0, x1, y1 = r['roi_ratio']
        lines.append(
            f'| {r["kind"]} | `{r["target"]}` | {w}x{h} | {r["count"]} | '
            f'{r["rx_min"]:.3f}~{r["rx_max"]:.3f} | '
            f'{r["ry_min"]:.3f}~{r["ry_max"]:.3f} | '
            f'[{x0:.3f}, {y0:.3f}, {x1:.3f}, {y1:.3f}] | '
            f'{r["area"]*100:.1f}% | {r["score_med"]:.3f} |')
    lines.append('')
    lines.append('**怎么用**：')
    lines.append('')
    lines.append('1. 面积占比小的目标，可以按其 ROI 收窄搜索区（改代码里的 '
                 '`region=`）')
    lines.append('2. **先看 `rx 范围` 的跨度**：跨度大的目标（如 '
                 '`btn_formation_next` 在 0.48~0.92）说明位置随窗口比例漂移，'
                 '**不要收窄**，否则换分辨率会频繁走兜底，反而更慢')
    lines.append('3. 只有一个窗口尺寸的样本不足以定论，至少两种分辨率都出现过'
                 '再收窄')
    lines.append('4. 收窄后仍有 `fallback_full` 兜底，不会丢目标')
    lines.append('')
    tasks_involved = sorted({t for r in rows for t in r['tasks']})
    if tasks_involved:
        lines.append(f'**涉及任务**：{", ".join(tasks_involved)}')
    return '\n'.join(lines) + '\n'


def write_report(dest: Optional[str] = None) -> str:
    """生成并写入表格，返回目标路径（失败返回空串）。"""
    try:
        rows = aggregate()
        md = render_markdown(rows, source=time.strftime('%Y-%m-%d %H:%M:%S'))
        if dest is None:
            import app_paths
            dest = os.path.join(app_paths.data_dir(), 'ROI-命中统计表.md')
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(md)
        return dest
    except Exception:
        return ''


def record_ocr(
    target: str,
    pos: Sequence[int],
    score: float = 1.0,
    win_w: int = 0,
    win_h: int = 0,
    origin_l: Optional[int] = None,
    origin_t: Optional[int] = None,
    version: str = '',
) -> None:
    """记录一次 OCR 文本命中（与 record_tpl 同构，kind=ocr）。

    窗口几何可省略 —— 省略时用最近一次 `set_window()` 的值。
    """
    try:
        if not _TASK:
            return
        _w, _h, _l, _t = _WIN
        win_w = int(win_w or _w)
        win_h = int(win_h or _h)
        origin_l = _l if origin_l is None else origin_l
        origin_t = _t if origin_t is None else origin_t
        if not win_w or not win_h:
            return
        path = _path()
        if not path:
            return
        rel_x = (float(pos[0]) - float(origin_l)) / float(win_w)
        rel_y = (float(pos[1]) - float(origin_t)) / float(win_h)
        row = {
            'ts': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'ver': version,
            'task': _TASK or '',
            'win': [int(win_w), int(win_h)],
            'kind': 'ocr',
            'target': str(target)[:24],
            'score': round(float(score), 4),
            'pos': [int(pos[0]), int(pos[1])],
            'rel': [round(rel_x, 4), round(rel_y, 4)],
        }
        line = json.dumps(row, ensure_ascii=False) + '\n'
        with _LOCK:
            _rotate_if_needed(path)
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line)
    except Exception:
        pass
