# -*- coding: utf-8 -*-
"""> 按钮搜索区域回归（v2.3.47 建立，v2.4.20 扩到三组实测）。

三次真机报障，全都是「截图里按钮明明在，日志却说未找到>按钮」：

① 2026-11-06 22:05 竖屏 747x1334
   按钮 rx=0.918。旧区间 0.65~0.95 → 0.506 未命中 ✗
   （右边界离按钮中心仅 24px，模板放不进 ROI，只能降 scale → 分数不足）
② 2026-11-06 22:09 修正为 0.65~1.00 → 0.911 命中 ✓
③ 2026-11-24 宽屏（用户报「识别不到切阵容的按钮」）
   按钮 **rx=0.480**。0.65~1.00 区域内最高仅 **0.440** ✗
   放宽到 0.30~1.00 → **0.838 命中**，且全屏该点仍是唯一最高分（无误点风险）

结论：按钮横向位置随窗口宽高比在 0.48~0.92 漂移，左边界必须 ≤0.30，
右边界必须到 1.00。本测试用几何断言把三组实测全部锁死。

运行：D:/MySoftware/Anaconda3/envs/cpu_default/python.exe tests/test_switch_btn_region.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILED = []
PASSED = [0]

TPL_HALF_W = 38   # 模板 64px @scale1.2 = 77px 宽 → 半宽 38


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)


# ── 从源码读常量（不 import：避免拉起 pyautogui/keyboard）──────────
SRC = open(os.path.join(ROOT, 'afk_bot/auto_farm/formation.py'),
           encoding='utf-8').read()


def _const(name):
    m = re.search(rf'^{name}:\s*float\s*=\s*([\d.]+)', SRC, re.M)
    return float(m.group(1)) if m else None


RX0 = _const('_SWITCH_BTN_RX0')
RY0 = _const('_SWITCH_BTN_RY0')
RY1 = _const('_SWITCH_BTN_RY1')
check('0a 三个区域常量都存在', None not in (RX0, RY0, RY1),
      f'RX0={RX0} RY0={RY0} RY1={RY1}')

REGION = (RX0, 1.00, RY0, RY1) if None not in (RX0, RY0, RY1) else (0.30, 1.00, 0.25, 0.75)
REGION_USER_095 = (0.65, 0.95, 0.25, 0.75)   # 用户最初指定（竖屏失败）
REGION_OLD = (0.60, 0.88, 0.25, 0.75)        # v2.3.47 之前
REGION_247 = (0.65, 1.00, 0.25, 0.75)        # v2.3.47 ~ v2.4.19


def covers(region, cx, cy, W, H, half=0):
    """模板能否完整放进区域（matchTemplate 要求整个模板在 ROI 内）。"""
    rx0, rx1, ry0, ry1 = region
    return (rx0 * W + half <= cx <= rx1 * W - half
            and ry0 * H <= cy <= ry1 * H)


# ── 三组真机实测 ───────────────────────────────────────────
PORT_W, PORT_H = 747, 1334
PORT_BTN = (686, 717)        # rx=0.918 ry=0.537  分数 0.911
LAND_W, LAND_H = 1284, 1026
LAND_BTN = (881, 551)        # rx=0.686 ry=0.537  分数 0.992
WIDE_W, WIDE_H = 1280, 617
WIDE_BTN = (614, 322)        # rx=0.480 ry=0.522  分数 0.838（v2.4.20 报障）
ONEKEY = (444, 1113)         # 「一键采用」，必须被 ry 上限排除

print('── ① 竖屏 747x1334：按钮 rx=0.918 ──')
check('1a 新区域能完整放下模板', covers(REGION, *PORT_BTN, PORT_W, PORT_H,
                                        half=TPL_HALF_W),
      f'按钮 x={PORT_BTN[0]} 右缘={PORT_BTN[0]+TPL_HALF_W} '
      f'区域右缘={int(REGION[1]*PORT_W)}')
check('1b 用户最初的 0.65~0.95 放不下（分叉证明）',
      not covers(REGION_USER_095, *PORT_BTN, PORT_W, PORT_H, half=TPL_HALF_W))
check('1c 旧区域 0.60~0.88 也放不下（原始 bug）',
      not covers(REGION_OLD, *PORT_BTN, PORT_W, PORT_H, half=TPL_HALF_W))
check('1d 右边界理论下限 ≥0.969', (PORT_BTN[0] + TPL_HALF_W) / PORT_W >= 0.969,
      f'{(PORT_BTN[0]+TPL_HALF_W)/PORT_W:.3f}')

print('\n── ② 横屏 1284x1026：按钮 rx=0.686 ──')
check('2a 新区域覆盖按钮', covers(REGION, *LAND_BTN, LAND_W, LAND_H,
                                  half=TPL_HALF_W))
check('2b 0.65~1.00 也能覆盖（v2.3.47 对横屏是对的）',
      covers(REGION_247, *LAND_BTN, LAND_W, LAND_H, half=TPL_HALF_W))

print('\n── ③ 宽屏 1280x617：按钮 rx=0.480（v2.4.20 报障场景）──')
check('3a v2.4.19 的 0.65~1.00 **放不下**（复现报障）',
      not covers(REGION_247, *WIDE_BTN, WIDE_W, WIDE_H, half=TPL_HALF_W),
      f'按钮 rx={WIDE_BTN[0]/WIDE_W:.3f} < 左边界 {REGION_247[0]}')
check('3b 新区域能完整放下模板', covers(REGION, *WIDE_BTN, WIDE_W, WIDE_H,
                                        half=TPL_HALF_W),
      f'区域左缘={int(REGION[0]*WIDE_W)} ≤ 按钮左缘={WIDE_BTN[0]-TPL_HALF_W}')

print('\n── ④ 不误伤底部「一键采用」──')
check('4a 竖屏「一键采用」被 ry 上限排除',
      not covers(REGION, *ONEKEY, PORT_W, PORT_H),
      f'一键采用 ry={ONEKEY[1]/PORT_H:.3f}')
check('4b 横屏底部（ry≈0.94）被排除',
      not covers(REGION, 640, int(0.94 * LAND_H), LAND_W, LAND_H))

print('\n── ⑤ 源码约束（防回退）──')
check('5a 左边界 ≤0.30（必须覆盖 rx=0.48）', RX0 is not None and RX0 <= 0.30,
      f'RX0={RX0}')
check('5b 右边界取到客户区右缘 1.00',
      'w * (1.0 - _SWITCH_BTN_RX0)' in SRC)
check('5c 旧写法 w * 0.65 / w * 0.35 已移除',
      'w * 0.65' not in SRC and 'w * 0.35' not in SRC)
check('5d y 上限 0.75（排除一键采用）', RY1 is not None and RY1 <= 0.75,
      f'RY1={RY1}')
check('5e 注释保留三组实测数据（0.918 / 0.480 / 0.838）',
      all(k in SRC for k in ('0.918', '0.480', '0.838')))
check('5f OCR 候选里不再有重复的 ">"', "'>', '＞', '›'" in SRC)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
