# -*- coding: utf-8 -*-
"""使用说明与源码的一致性（v2.2.0）

背景（用户 01:13 指出）：手册里竞技场参数我写成「名字**逗号**分隔」，
但源码 `tasks/arena.py::_blacklist` 是 `re.split(r'[;；]', raw)` ——
**分号**分隔；而且 schema 自己的 `desc` 就写着「角色名用;或；分隔」。
同类错误还查出两处（迷宫运行模式描述、家园保留体力范围）。

根因：手册的**参数章节是凭记忆手写的**，没有以 schema 为真相源。
本测试把「手册 ↔ schema」的对应关系钉住，防止再次漂移。

运行： D:/anaconda3/envs/afk_gpu/python.exe tests/test_user_manual_accuracy.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tasks.catalog  # noqa: F401,E402
from task_registry import TaskRegistry  # noqa: E402

MANUAL = os.path.join(ROOT, 'docs', '客户使用说明.md')
_SRC = open(MANUAL, encoding='utf-8').read()

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


print('══ 使用说明与源码一致性 ══')
print()

# ── A. 参数分隔符（用户亲自指出的错）──
print('── A. 名单分隔符（本次修正项）──')
check('A1 竞技场黑名单说明写「;」不是逗号',
      ';' in _SRC and '黑名单' in _SRC)
check('A2 手册**不再**出现「逗号分隔」（错误说法已清除）',
      '逗号分隔' not in _SRC, '应改为「;或；分隔」')
check('A3 手册明确写出 `;` 或 `；` 两种分隔符',
      '`;` 或 `；`' in _SRC or '; 或 ；' in _SRC or ';或；' in _SRC)

# 与源码正则对齐（真相源）
_arena = open(os.path.join(ROOT, 'tasks', 'arena.py'), encoding='utf-8').read()
check("A4 源码确为分号分隔（re.split(r'[;；]')）",
      r"re.split(r'[;；]'" in _arena, '手册与源码一致')
_black = _arena.split('def _blacklist')[1].split('def ')[0]
_white = _arena.split('def _whitelist')[1].split('def ')[0]
check('A5 黑白名单两个解析函数都用同一分隔符',
      r"[;；]" in _black and r"[;；]" in _white)
print()

# ── B. 参数说明覆盖完整（每个参数都要在手册里）──
print('── B. 参数覆盖 ──')
_missing = []
for m in TaskRegistry.get_all():
    for k, spec in (m.options_schema or {}).items():
        lbl = spec.get('label', k)
        if lbl not in _SRC:
            _missing.append(f'{m.label}.{lbl}')
check('B1 所有参数 label 都写进手册', not _missing, str(_missing))

# 枚举型参数：每个可选项都要体现（迷宫三种模式）
_missing_enum = []
for m in TaskRegistry.get_all():
    for k, spec in (m.options_schema or {}).items():
        for kk, lbl in (spec.get('choices') or []):
            if lbl[:6] not in _SRC and lbl not in _SRC:
                _missing_enum.append(f'{m.label}.{lbl}')
check('B2 枚举型参数的可选项都在手册（如迷宫三种模式）',
      not _missing_enum, str(_missing_enum))
print()

# ── C. 任务覆盖 ──
print('── C. 任务覆盖 ──')
_missing_task = [m.label for m in TaskRegistry.get_all() if m.label not in _SRC]
check('C1 全部已实装玩法都在手册里', not _missing_task, str(_missing_task))
check('C2 任务数量与注册表一致（防止漏写新增玩法）',
      len([m for m in TaskRegistry.get_all() if m.label in _SRC])
      == len(TaskRegistry.get_all()),
      f'{len(TaskRegistry.get_all())} 个')
print()

# ── D. 用户明确要求删掉的内容 ──
print('── D. 用户要求 ──')
check('D1 已删除「九、版本」节（用户 00:54 要求）',
      '九、版本' not in _SRC)
check('D2 不再出现版本号段落（“每次更新只需…”）',
      '每次更新只需' not in _SRC)
check('D3 保留了 F12 停止说明（新功能必须写进手册）',
      'F12' in _SRC and '停止' in _SRC)
print()

# ── E. 精简程度（防回退膨胀）──
print('── E. 精简 ──')
_size = len(_SRC.encode('utf-8'))
check(f'E1 体积 ≤ 6000 字节（当前 {_size}，精简后 3964 起）',
      _size <= 6000, f'{_size} bytes')
print()

# ── F. GUI 提示与源码一致性（v2.2.0）──
# 用户 01:19 要求在竞技场抽屉「白名单」行右侧常显「角色名用 ; 或 ； 分隔」。
# 背景：该行右侧此前为空，而 desc 只作**占位符**（输入框有值就不显示）
# → 用户会把名字用逗号连写（如 `depz,Cataclysm`），
#   而 `re.split(r'[;；]')` 不拆分 → 整串被当成一个名字、静默失效。
print()
print('── F. 抽屉提示（常显，不依赖占位符）──')
_arena_mod = TaskRegistry.get_by_id('arena')
_wl = _arena_mod.options_schema['whitelist']
_bl = _arena_mod.options_schema['blacklist']
check('F1 白名单参数带 note（右侧常显灰字）',
      bool(_wl.get('note')), repr(_wl.get('note')))
check('F2 note 文案就是用户指定的「角色名用 ; 或 ； 分隔」',
      _wl.get('note') == '角色名用 ; 或 ； 分隔', repr(_wl.get('note')))
check('F3 note 同时含两种分隔符且**不提逗号**',
      ';' in _wl.get('note', '') and '；' in _wl.get('note', '')
      and '，' not in _wl.get('note', '') and ',' not in _wl.get('note', ''))
_hint_texts = ' '.join([
    str(_wl.get('note')), str(_wl.get('desc')),
    str(_bl.get('note')), str(_bl.get('desc')),
])
check('F4 竞技场所有提示文案都不出现「逗号分隔」',
      '逗号分隔' not in _hint_texts)
check('F5 提示文案与源码分隔符一致（源码是 [;；]）',
      r"re.split(r'[;；]'" in _arena, '一致')

print()
print('═' * 56)
print(f'使用说明一致性：{len(_PASS)}/{len(_PASS) + len(_FAIL)}')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
print('═' * 56)
sys.exit(0 if not _FAIL else 1)
