# -*- coding: utf-8 -*-
"""任务栏说明文案全量一致性测试（v2.3.35）。

用户 2026-11-06 要求：任务说明必须精准简洁、只说核心功能、完整保留
必要信息、不写实现/运行逻辑，且文字在任务栏内完整显示不截断。

上一轮只改了「天穹试炼」1 个任务，其余 11 个仍未同步（截图里 8 行带
`…`）。本测试**遍历 catalog 全部注册任务**做一致性校验，新增任务自动
纳入，防止再次「只改一个、漏掉其余」。

判据：
  1. 非空、≤20 字（任务栏描述行实测可用约 360px / 12 号字，20 字 ≈320px）
  2. 不含实现细节 / 操作步骤 / 按键 / 技术名词
  3. 不含省略号（自身不得自带截断）
  4. 覆盖 catalog 全量（≥12 个），且与源码中的定义一致
  5. GUI 渲染链路：_instance_summary 只回描述、指标独立成行
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import task_registry
from task_registry import TaskRegistry
from tasks import catalog  # noqa: F401  import 即注册全部任务类

_PASS, _FAIL = [], []


def check(name, cond, detail=''):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


MAX_LEN = 20
# 实现细节 / 运行逻辑 / 操作步骤 / 技术名词（出现即视为不合格）
IMPL_KEYWORDS = (
    '出发！', '挑战！', '→', '模板', 'OCR', 'F 开战', '/F', 'F）',
    '一键采用', '通关阵容', 'WM_CLOSE', '强杀', '路径三级', '跨环境',
    '收礼进度', '启动器', '检测游戏状态', '100 档', '（F', '、F',
    '选门战斗', '逐层选门战斗', '主菜单', '社交', '页签', '点击', '按钮',
)
# 每个任务至少应保留的「核心功能 / 必要结束条件」语义词
REQUIRED = {
    'auto_farm': ('推图', '通关'),
    'labyrinth': ('迷宫', '层'),
    'goddess_trial': ('挑战', '扫荡'),
    'home': ('愿望单',),
    'arena': ('好友', '公会', '对手', '票'),
    'daily_quest': ('每日任务', '活跃度'),
    'friend_gift': ('赠礼', '友情点'),
    'dream_realm': ('迷梦', '次数'),
    'guild_battle': ('扫荡', '挑战'),
    'tianqiong_trial': ('当日开放', '失败', '全部完成'),
    'start_game': ('拉起', '跳过'),
    'close_game': ('关闭', '跳过'),
    'season_equipment': ('锻造', '穿戴', '职业'),
}

metas = TaskRegistry.get_all()
by_id = {m.id: m for m in metas}

print(f'── catalog 共 {len(metas)} 个任务 ──')
check('catalog 覆盖 ≥13 个任务（防止漏注册/漏改）', len(metas) >= 13,
      f'实际 {len(metas)}')

for m in metas:
    d = (m.description or '').strip()
    check(f'[{m.id}] 说明非空', bool(d), repr(d))
    check(f'[{m.id}] ≤{MAX_LEN} 字（任务栏单行可完整显示）',
          len(d) <= MAX_LEN, f'{len(d)} 字: {d}')
    check(f'[{m.id}] 不含实现细节/操作步骤',
          not any(k in d for k in IMPL_KEYWORDS),
          str([k for k in IMPL_KEYWORDS if k in d]) or d)
    check(f'[{m.id}] 不含省略号（自带截断）', '…' not in d and '...' not in d, d)
    need = REQUIRED.get(m.id)
    if need:
        check(f'[{m.id}] 必要信息完整 {need}',
              all(k in d for k in need), d)

# ── 竞技场专项（用户 2026-11-06 点名要求注明）─────────────────
# 对应 tasks/arena.py:_pick_opponent() 的 blocked 规则：
# 含「公会成员」或「好友」标签的列会被排除，不参与对手选择。
print('\n── 竞技场排除规则说明 ──')
_arena_desc = by_id['arena'].description
check('竞技场注明排除好友', '好友' in _arena_desc, _arena_desc)
check('竞技场注明排除公会成员', '公会' in _arena_desc, _arena_desc)
check('竞技场注明「排除」语义（非仅罗列）',
      '排除' in _arena_desc or '不含' in _arena_desc or '避开' in _arena_desc,
      _arena_desc)
check('竞技场仍保留按名单选对手与票尽结束条件',
      '名单' in _arena_desc and '票' in _arena_desc, _arena_desc)
# 面向用户的文档口径必须与代码一致（避免界面写了、手册漏写）
for _doc in ('docs/客户使用说明.md', 'README.md'):
    with open(os.path.join(ROOT, _doc), encoding='utf-8') as _f:
        _arena_line = next((l for l in _f if '竞技场' in l and '|' in l), '')
    check(f'{_doc} 竞技场说明同步注明排除好友/公会',
          '好友' in _arena_line and '公会' in _arena_line,
          _arena_line.strip()[:80])

# ── 源码定义与 catalog 注册值一致（防止只改了注册处、源码另有一份）──
print('\n── 源码 ↔ catalog 一致性 ──')
src_files = sorted(
    os.path.join(ROOT, 'tasks', f) for f in os.listdir(os.path.join(ROOT, 'tasks'))
    if f.endswith('.py'))
src_desc = {}
for path in src_files:
    with open(path, encoding='utf-8') as fp:
        for line in fp:
            mo = re.match(r'\s*description\s*=\s*["\'](.+?)["\']\s*,?\s*$', line)
            if mo:
                src_desc.setdefault(os.path.basename(path), []).append(mo.group(1))
            else:
                mo2 = re.search(r'description\s*=\s*["\'](.+?)["\']\s*,', line)
                if mo2:
                    src_desc.setdefault(os.path.basename(path), []).append(mo2.group(1))
all_src = [d for ds in src_desc.values() for d in ds]
check('源码中 description 定义数 ≥13（覆盖全部任务）',
      len(all_src) >= 13, f'实际 {len(all_src)}')
for d in all_src:
    check('源码定义 ≤20 字且无实现细节',
          len(d) <= MAX_LEN and not any(k in d for k in IMPL_KEYWORDS),
          f'{len(d)}字: {d}')

# ── GUI 渲染链路（纯逻辑，不起 Tk）──
print('\n── GUI 渲染链路 ──')
import afk_gui  # noqa: E402

inst_stub = type('Inst', (), {'max_time': 600, 'max_turns': 30,
                              'class_id': 'arena'})()
summary = afk_gui.App._instance_summary(None, inst_stub)
metrics = afk_gui.App._instance_metrics(inst_stub)
check('_instance_summary 只回描述（不含指标）',
      summary == by_id['arena'].description and '时间' not in summary,
      summary)
check('_instance_metrics 独立返回完整指标',
      '时间 600s' in metrics and '轮次 30' in metrics, metrics)
check('指标行不再被拼进描述行（不会互相挤占宽度）',
      ' | ' not in summary and summary != metrics, f'{summary} / {metrics}')
check('_truncate(24) 对全部新文案不触发',
      all(len(afk_gui.App._truncate(m.description, 24)) == len(m.description)
          for m in metas),
      f'最长 {max(len(m.description) for m in metas)} 字')

print(f'\n{"=" * 60}')
if _FAIL:
    print(f'❌ 失败 {len(_FAIL)} 项：')
    for n in _FAIL:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{len(_PASS)}/{len(_PASS)}）')
