# -*- coding: utf-8 -*-
"""ROI 命中统计报表（v2.4.22）。

读 `%LOCALAPPDATA%\\AFKJ自动化工具\\hit_stats.jsonl`（运行期自动累积），
聚合出「每个目标在每个窗口尺寸下的命中位置分布 + 建议 ROI」。

用法：
    python tools/roi_report.py            # 打印到屏幕
    python tools/roi_report.py --write    # 写入 docs/ROI-命中统计表.md
    python tools/roi_report.py --json     # 输出聚合后的原始数据

数据从哪来：`afk_bot/infra/input.py::template_match` 每次命中都会
`hit_stats.record_tpl(...)`，带上窗口尺寸、当前任务、目标名、置信度、
位置与相对比例。任务全部结束后由 scheduler 自动刷新一次表格。
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot.infra import hit_stats   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--write', action='store_true',
                    help='写入 docs/ROI-命中统计表.md')
    ap.add_argument('--json', action='store_true', help='输出聚合原始数据')
    ap.add_argument('--src', default=None, help='指定 jsonl 路径（默认数据目录）')
    args = ap.parse_args()

    rows = hit_stats.aggregate(args.src)

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0

    import time
    md = hit_stats.render_markdown(rows, source=time.strftime('%Y-%m-%d %H:%M:%S'))

    if args.write:
        dest = os.path.join(ROOT, 'docs', 'ROI-命中统计表.md')
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, 'w', encoding='utf-8') as f:
            f.write(md)
        print(f'已写入 {dest}（{len(rows)} 个目标）')
    else:
        print(md)
    return 0


if __name__ == '__main__':
    sys.exit(main())
