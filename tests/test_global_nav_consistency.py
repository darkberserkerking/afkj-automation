# -*- coding: utf-8 -*-
"""全局导航一致性测试（v1.9.11 导航统一化审查）。

校验四件事：
  1. NAV_TARGETS 每个目标都能被全局导航判层（层映射/精确画面存在）
  2. screen_registry 已注册画面全部纳入 _SCREEN_LAYER（无"导航不认识"）
  3. 非法目标 / 非法步进目标 → 明确错误日志 + 返回 False（不静默失败）
  4. guild 等"同层不同页"目标有精确到达判定（防假到达）
运行：afk_gpu python tests/test_global_nav_consistency.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY, ZH_SCREEN   # noqa: E402
from tasks.global_nav import (                                        # noqa: E402
    NAV_TARGETS, _SCREEN_LAYER, _TARGET_SCREEN, GlobalNavigator,
)

PASS: list = []
FAIL: list = []


def check(name: str, cond: bool, detail: str = '') -> None:
    print(f"{'✅' if cond else '❌'} {name}" + (f" — {detail}" if detail else ''))
    (PASS if cond else FAIL).append(name)


# ── 1. 目标可判层 ─────────────────────────────────────────
print('── 1. 导航目标完整性 ──')
for tgt, (layer, card) in NAV_TARGETS.items():
    if card:      # 需点玩法目录卡片 → 靠 _TARGET_SCREEN 精确到达
        scr = _TARGET_SCREEN.get(tgt, '')
        ok = scr in _SCREEN_LAYER and _SCREEN_LAYER[scr] == layer
        check(f'目标 {tgt} 有可达画面({scr})且层={layer}', ok, scr)
    else:
        ok = any(v == layer for v in _SCREEN_LAYER.values())
        check(f'目标 {tgt} 目标层 {layer} 存在画面映射', ok)

# ── 2. 注册画面全覆盖 ────────────────────────────────────
print('── 2. 已注册画面全覆盖 ──')
registered = set(GLOBAL_SCREEN_REGISTRY._screens.keys()) if hasattr(
    GLOBAL_SCREEN_REGISTRY, '_screens') else set(ZH_SCREEN.keys())
# 'unknown' 是注册表兜底项（非真实页面），不参与判层
missing = sorted(s for s in registered
                 if s not in _SCREEN_LAYER and s != 'unknown')
check('所有已注册画面均已纳入 _SCREEN_LAYER', not missing, str(missing))
for scr in registered:
    if scr in _SCREEN_LAYER:
        check(f'画面 {scr} 中文名已登记', scr in ZH_SCREEN,
              ZH_SCREEN.get(scr, '<缺失>'))

# ── 3. 非法目标不静默失败 ────────────────────────────────
print('── 3. 非法目标校验与兜底 ──')


class _LogBot:
    """极小 bot 桩：记录日志，其余返回空。"""

    def __init__(self):
        self.logs: list = []

    def _ocr_screen(self, img=None, no_cache=False):
        return []

    def find_text(self, items, kw, min_conf=0.6, exact=False):
        return None

    def click_at(self, x, y, label=''):
        pass

    def click_ratio(self, rx, ry, label=''):
        pass

    def press_esc(self, label=''):
        pass

    def click_back_button(self, tpls, threshold=0.75):
        return False

    def scroll_wheel(self, clicks, rx=None, ry=None, label=''):
        return False


bot = _LogBot()
nav = GlobalNavigator(bot, logger=lambda m: bot.logs.append(m),
                      stop_check=lambda: False)
r = nav.navigate_to('no_such_target')
check('非法目标 → 返回 False', r is False)
check('非法目标 → 有明确错误日志（含可用目标清单）',
      any('未知导航目标' in m and '可用目标' in m for m in bot.logs),
      str(bot.logs[:1]))

logs2: list = []
nav2 = GlobalNavigator(_LogBot(), logger=lambda m: logs2.append(m),
                       stop_check=lambda: False)
r2 = nav2.step('bad_target', [], None)
check('非法步进目标 → 返回 False', r2 is False)
check('非法步进目标 → 有明确错误日志',
      any('未登记目标' in m for m in logs2), str(logs2[:1]))

# ── 4. 同层不同页的精确到达 ──────────────────────────────
print('── 4. 精确到达判定（防假到达）──')
exact = GlobalNavigator(_LogBot(), logger=lambda m: None)._LAYER_EXACT_SCREEN
for tgt in ('world_home', 'playbook', 'guild'):
    check(f'同层目标 {tgt} 配了精确到达画面', tgt in exact,
          exact.get(tgt, '<无>'))

print(f'══ 全局导航一致性：通过 {len(PASS)}/{len(PASS) + len(FAIL)} ══')
if FAIL:
    for f in FAIL:
        print('   ❌', f)
sys.exit(0 if not FAIL else 1)
