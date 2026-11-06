# -*- coding: utf-8 -*-
"""P0 审计回归：艺术字/大标题判据必须有副文本兜底（v2.3.43）。

背景（v2.3.42 真机对照实测）：
  同一结算页，艺术字「战斗胜利」
    横屏 1279x1029 conf 0.96（threshold 0.3/0.1/0.0 均命中）
    竖屏 757x1367 **三档阈值全部 0 命中**（放大/拉宽后超出 MaaOCR 能力）
  → 凡把艺术字当**必要条件**的判据，在竖屏会静默失效（识别不到结算页 /
    把胜利记成失败 / 假成功死循环）。

本测试两层：
  ① 静态：扫描 tasks/ + afk_bot/ 里所有艺术字**正向判据**，要求同一判定
     表达式内出现副文本兜底词；白名单只放「反转判别 / 排除条件」等本来就
     不该有兜底的场景，且按 (文件, 函数名, 关键词) 匹配 —— 行号漂移不会误报。
  ② 行为：arena 结算判据在「读不出艺术字」的真实竖屏 OCR 下仍命中；
     auto_farm 胜负判定在无艺术字时靠副标题判对。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_artistic_text_fallback.py
"""
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# 弱信号（艺术字/大标题）：竖屏可能完全读不出
WEAK = ('战斗胜利', '战斗失败', '挑战成功', '战败', '锻造成功')
# 可作兜底的副文本（按钮/提示语/副标题，OCR 稳）
FALLBACK = (
    '挂机收益提升', '赛季挂机进度', '点击空白处', '点击关闭',
    '再次挑战', '继续挑战', '推荐提升', '穿戴新装备', '一键装备',
    '当前无可替换', '挑战失败', '下一关',
)

# 白名单：(相对路径, 所在函数名, 关键词) —— 这些本来就无需兜底
WHITELIST = {
    # 反转判别：读不到「战斗失败」= 已退出失败画面（读不出是预期）
    ('tasks/labyrinth.py', '_leave_failure_screen', '战斗失败'),
    # 排除条件：`not h('锻造成功')` 用于区分弹窗/成功页，不是正向判据
    ('tasks/screen_registry.py', '', '锻造成功'),
    # 该 elif 位于 `if has_close`（结算页已确认）内，且外层已有
    # `has_defeat or has_retry`（「再次挑战」按钮）作失败兜底 ——
    # 失败判定整体不只依赖艺术字，故此处无需再补。
    ('afk_bot/auto_farm/battle.py', 'wait_for_manual_battle_end', '战败'),
}

SCAN_DIRS = ('tasks', 'afk_bot')


def enclosing_func(src_lines, lineno):
    """向上找最近的 def 名（比行号稳定，减漂移误报）。"""
    for i in range(lineno - 1, -1, -1):
        s = src_lines[i].strip()
        if s.startswith('def '):
            return s[4:].split('(')[0].strip()
    return ''


def scan():
    """返回 [(rel, func, kw, lineno, line)] —— 无兜底的正向判据点。"""
    bad = []
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [x for x in dirnames
                           if x not in ('__pycache__', 'backup')]
            for fn in filenames:
                if not fn.endswith('.py'):
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT).replace('\\', '/')
                try:
                    src = open(path, encoding='utf-8').read()
                    lines = src.splitlines()
                    tree = ast.parse(src)
                except (SyntaxError, OSError):
                    continue
                # 收集所有「正向」比较/调用节点里的艺术字常量
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Compare):
                        continue
                    # 只看 `'词' in x` 形态（正向包含）
                    if not any(isinstance(op, ast.In) for op in node.ops):
                        continue
                    kws = [c.value for c in [node.left] if
                           isinstance(c, ast.Constant)
                           and isinstance(c.value, str)
                           and any(w in c.value for w in WEAK)]
                    if not kws:
                        continue
                    line = lines[node.lineno - 1] if node.lineno <= len(
                        lines) else ''
                    # 排除条件（not in / 行内含 not）
                    if 'not in' in line or re_not_before(lines, node.lineno):
                        continue
                    # 同一 if 语句块内找兜底词（向上 6 行 / 向下 2 行）
                    ctx = '\n'.join(lines[max(0, node.lineno - 7):
                                          node.lineno + 2])
                    if any(fb in ctx for fb in FALLBACK):
                        continue
                    func = enclosing_func(lines, node.lineno)
                    for kw in kws:
                        if (rel, func, kw) in WHITELIST:
                            continue
                        bad.append((rel, func, kw, node.lineno, line.strip()))
    return bad


def re_not_before(lines, lineno):
    """同一行往前看 2 行，若紧邻 `not any(`/`not h(` 视为排除条件。"""
    seg = ' '.join(lines[max(0, lineno - 3):lineno]).replace(' ', '')
    return 'notany(' in seg or 'noth(' in seg


print('── ① 静态：艺术字正向判据必须有副文本兜底 ──')
bad_points = scan()
check('1a 不存在「只认艺术字」的正向判据', not bad_points,
      '\n      ' + '\n      '.join(
          f'{f}:{ln} [{fn}] 「{kw}」 {txt[:60]}'
          for f, fn, kw, ln, txt in bad_points[:8]))

print('\n── ② 行为：读不出艺术字时判据仍成立 ──')
# 真实竖屏结算页 OCR（MaaOCR 实测，无「战斗胜利」）
PORTRAIT_TEXTS = ['挂机收益提升', '赛季挂机进度', '1400', '1401',
                  '16740/时》>16741/时', '88076/天>>88164/天',
                  '最高等级800级', '64730/时>>64795/时',
                  '已达4天后极限速率', '幻灵挑战', '95亿']
LANDSCAPE_TEXTS = ['战斗胜利'] + PORTRAIT_TEXTS

RESULT_TEXT_WORDS = ('点击空白处关闭', '点击空白处继续', '点击关闭',
                     '再次挑战', '继续挑战', '挂机收益提升')


def arena_result_hit(texts):
    """与 tasks/arena.py 结算判据同构（三路兜底）。"""
    return (any(kw in t for kw in RESULT_TEXT_WORDS for t in texts)
            or any('战斗胜利' in t or '战斗失败' in t for t in texts))


check('2a arena 结算判据：竖屏（无艺术字）仍命中',
      arena_result_hit(PORTRAIT_TEXTS))
check('2b arena 结算判据：横屏仍命中', arena_result_hit(LANDSCAPE_TEXTS))

# 旧版（艺术字 AND 提示语）在竖屏必不命中 —— 证明用例对 bug 敏感
def arena_result_hit_old(texts):
    return (any('战斗胜利' in t or '战斗失败' in t for t in texts)
            and any('点击空白处关闭' in t for t in texts))


check('2c 旧判据在竖屏不命中（用例对 bug 敏感）',
      not arena_result_hit_old(PORTRAIT_TEXTS))


def won(texts):
    """与 afk_bot/auto_farm/loop.py 胜负判定同构（v2.3.43 三路）。"""
    return any(('战斗胜利' in s) or ('胜利' in s) or ('挂机收益提升' in s)
               for s in texts)


def won_old(texts):
    return any(('战斗胜利' in s) or ('胜利' in s) for s in texts)


check('2d 胜负判定：竖屏（无艺术字）靠副标题判为胜利', won(PORTRAIT_TEXTS))
check('2e 旧胜负判定在竖屏会误判成失败', not won_old(PORTRAIT_TEXTS))

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
