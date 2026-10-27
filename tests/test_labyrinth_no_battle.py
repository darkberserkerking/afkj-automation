# -*- coding: utf-8 -*-
"""v2.1.5：迷宫「未开战」判定 + 全项目「self._xxx() 调用存在性」静态检查。

背景（用户 21:33 真机）：
  · 我上一版引用了不存在的方法 `self._settle_decision(...)`（实为
    `_end_exploration`）→ 真机运行到该分支时 AttributeError 崩溃。
  · 此类错误静态即可发现 → 本测试对 tasks/ 全部模块做统一扫描：
      源码中出现 `self._name(` 调用，但类中（含基类）无该属性 → 报错。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import fast_time  # noqa: F401,E402
import ast  # noqa: E402

_PASS, _FAIL = [], []


def check(name, cond, detail=""):
    (_PASS if cond else _FAIL).append(name)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}  {detail}".rstrip())


print('── 1. 迷宫未开战判定相关常量/方法 ──')
from tasks import labyrinth as LB  # noqa: E402

check('_BATTLE_WAIT = 30s（用户 21:33 定案）',
      LB._BATTLE_WAIT == 30.0, str(LB._BATTLE_WAIT))
check('_NO_BATTLE_GRACE 存在且 < _BATTLE_WAIT（提前判定未开战）',
      hasattr(LB, '_NO_BATTLE_GRACE') and 0 < LB._NO_BATTLE_GRACE < LB._BATTLE_WAIT,
      str(getattr(LB, '_NO_BATTLE_GRACE', None)))
check('LabyrinthTask 有 _end_exploration（结算收尾真名）',
      hasattr(LB.LabyrinthTask, '_end_exploration'))
check('LabyrinthTask 有 _battle_timeout_streak 初始化',
      '_battle_timeout_streak' in
      open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
           encoding='utf-8').read())

print('── 2. 源码中「未开战」分支引用的方法齐备 ──')
_src = open(os.path.join(ROOT, 'tasks', 'labyrinth.py'),
            encoding='utf-8').read()
check('未开战分支调用 _end_exploration',
      '_end_exploration(' in _src)
check('已无失效的 _settle_decision 引用',
      '_settle_decision' not in _src)

print('── 3. 全项目静态检查：self._xxx() 调用是否都存在 ──')
_MISSING = []
_SKIP = {'_ocr_screen'}   # bot 侧接口（非 self 类属性）


def _cls_attrs(node):
    names = set()
    for b in ast.walk(node):
        if isinstance(b, ast.FunctionDef):
            names.add(b.name)
        elif isinstance(b, ast.Assign):
            for t in b.targets:
                if isinstance(t, ast.Attribute):
                    names.add(t.attr)
                elif isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(b, ast.AnnAssign):
            t = b.target
            if isinstance(t, ast.Name):
                names.add(t.id)
            elif isinstance(t, ast.Attribute):
                # `self._log: Callable[...] = ...` 形式（带类型注解的属性）
                names.add(t.attr)
    return names


for fn in sorted(os.listdir(os.path.join(ROOT, 'tasks'))):
    if not fn.endswith('.py'):
        continue
    fp = os.path.join(ROOT, 'tasks', fn)
    try:
        tree = ast.parse(open(fp, encoding='utf-8').read())
    except SyntaxError:
        continue
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        local = _cls_attrs(node)
        # 收集 self._name( 调用（排除 self.bot 等链式）
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and isinstance(sub.func.value, ast.Name)
                    and sub.func.value.id == 'self'
                    and sub.func.attr.startswith('_')):
                nm = sub.func.attr
                if nm in _SKIP or nm in local:
                    continue
                # 基类/混入方法白名单：从 BaseTask 与其父类收集
                base_names = set()
                for base in node.bases:
                    bn = getattr(base, 'id', None) or getattr(base, 'attr', None)
                    if bn:
                        try:
                            base_names |= _cls_attrs(
                                ast.parse(
                                    open(os.path.join(ROOT, 'tasks',
                                                      'base.py'),
                                         encoding='utf-8').read()))
                        except Exception:
                            pass
                if nm in base_names:
                    continue
                _MISSING.append(f'{fn}:{sub.lineno} self.{nm}()')

# 去重 + 过滤（_stop_result/_texts 等 BaseTask 已提供）
_reg = set()
for m in _MISSING:
    _reg.add(m)
check('tasks/ 内 self._xxx() 调用均在类或基类中定义',
      not _reg, str(sorted(_reg)[:8]) if _reg else '')

print()
print(f'══ 迷宫未开战 + 方法存在性检查：通过 {len(_PASS)}/{len(_PASS) + len(_FAIL)} ══')
if _FAIL:
    print('失败项：')
    for f_ in _FAIL:
        print(' ', f_)
sys.exit(0 if not _FAIL else 1)
