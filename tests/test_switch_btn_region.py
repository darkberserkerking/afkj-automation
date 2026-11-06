# -*- coding: utf-8 -*-
"""> 按钮搜索区域回归（v2.3.47）。

真机 2026-11-06 22:05（竖屏 747x1334）日志：
  [22:05:08] 切换阵容（目标点1次">"）
  [22:05:10] [警告] 未找到>按钮，已是最后一套阵容
而截图里 > 按钮明明在右侧中部。

【最终区间 rx 0.65~1.00 的由来（双截图实测，不猜）】
  横屏 1284x1026：按钮 rx=0.686
    0.65~0.95 → 0.992 命中 ✓   0.65~1.00 → 0.992 命中 ✓
  竖屏 747x1334：按钮 rx=0.918
    0.65~0.95 → **0.506 未命中** ✗  ← 用户最初指定的区间
    0.65~0.97 → 0.865 命中（险过）
    0.65~1.00 → **0.911 命中** ✓
  原因：竖屏按钮中心距右边界仅 (0.95-0.918)*747≈24px，模板（scale1.2 宽 77、
  半宽 38）放不进去，只能降到 scale 0.7 → 分数不足阈值 0.6。
  数学要求：右边界 ≥ (686+38)/747 = **0.969**。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_switch_btn_region.py
"""
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


# 与 AFKFormationManager._switch_btn_region 同构（相对比例）
REGION = (0.65, 1.00, 0.25, 0.75)
REGION_USER_095 = (0.65, 0.95, 0.25, 0.75)   # 用户最初指定（竖屏会失败）
REGION_OLD = (0.60, 0.88, 0.25, 0.75)        # 修复前

TPL_HALF_W = 38   # 模板 64px @scale1.2 = 77px 宽 → 半宽 38


def covers(region, cx, cy, W, H, half=0):
    """模板能否完整放进区域（matchTemplate 要求整个模板在 ROI 内）。"""
    rx0, rx1, ry0, ry1 = region
    return (rx0 * W + half <= cx <= rx1 * W - half
            and ry0 * H <= cy <= ry1 * H)


PORT_W, PORT_H = 747, 1334
PORT_BTN = (686, 717)      # 竖屏实测：rx=0.918 ry=0.537 分数 0.911
LAND_W, LAND_H = 1284, 1026
LAND_BTN = (881, 551)      # 横屏实测：rx=0.686 ry=0.537 分数 0.992
PORT_ONEKEY = (444, 1113)  # 「一键采用」按钮，必须被 y 上限排除

print('── ① 竖屏：0.65~1.00 必须覆盖按钮（含模板半宽）──')
check('1a 新区域能完整放下模板', covers(REGION, *PORT_BTN, PORT_W, PORT_H,
                                        half=TPL_HALF_W),
      f'按钮 x={PORT_BTN[0]} 右缘={PORT_BTN[0]+TPL_HALF_W} '
      f'区域右缘={int(REGION[1]*PORT_W)}')
check('1b 用户指定的 0.65~0.95 **放不下**（分叉证明）',
      not covers(REGION_USER_095, *PORT_BTN, PORT_W, PORT_H, half=TPL_HALF_W),
      f'区域右缘={int(0.95*PORT_W)} < 按钮右缘={PORT_BTN[0]+TPL_HALF_W}')
check('1c 旧区域 0.60~0.88 也放不下（原始 bug）',
      not covers(REGION_OLD, *PORT_BTN, PORT_W, PORT_H, half=TPL_HALF_W))
check('1d 右边界理论下限 ≥0.969', (PORT_BTN[0] + TPL_HALF_W) / PORT_W >= 0.969,
      f'{(PORT_BTN[0]+TPL_HALF_W)/PORT_W:.3f}')
check('1e 新区域不覆盖底部「一键采用」（ry 上限 0.75）',
      not covers(REGION, *PORT_ONEKEY, PORT_W, PORT_H),
      f'一键采用 ry={PORT_ONEKEY[1]/PORT_H:.3f}')

print('\n── ② 横屏：0.65~1.00 同样命中且不误伤 ──')
check('2a 横屏按钮被新区域覆盖', covers(REGION, *LAND_BTN, LAND_W, LAND_H,
                                        half=TPL_HALF_W),
      f'按钮 rx={LAND_BTN[0]/LAND_W:.3f}')
check('2b 横屏 0.65~0.95 也能覆盖（用户区间对横屏是对的）',
      covers(REGION_USER_095, *LAND_BTN, LAND_W, LAND_H, half=TPL_HALF_W))
check('2c 横屏不覆盖底部「一键采用」（ry≈0.94）',
      not covers(REGION, 640, int(0.94 * LAND_H), LAND_W, LAND_H))

print('\n── ③ 源码静态断言（防回退）──')
src = open(os.path.join(ROOT, 'afk_bot/auto_farm/formation.py'),
           encoding='utf-8').read()
check('3a 使用左边界 0.65', 'w * 0.65' in src)
check('3b 宽度按 0.35（即右边界 1.00）', 'w * 0.35' in src)
check('3c 不再使用旧的 0.88 右边界',
      not ('w * 0.60' in src and 'w * 0.28' in src))
check('3d 注释记录了双截图实测结论（0.918 / 0.969）',
      '0.918' in src and '0.969' in src)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
