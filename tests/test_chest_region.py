# -*- coding: utf-8 -*-
"""挂机宝箱区域回归（v2.3.46 竖屏修复）。

真机 2026-11-06 21:33（竖屏 750x1334）日志：
  [信息] 未找到挂机收益宝箱 → 跳过领取｜宝箱区候选[7]=[...('+20',387,868)...]
候选里**没有倒计时**；实际截图 MaaOCR 实测「00:30:11」在 (376,1044) conf .97。

根因：判据分母写死 1280/1024 →
  x/1280 = 376/1280 = 0.294（要求 0.35~0.65）❌
  y/1024 = 1044/1024 = 1.019（要求 0.65~0.95）❌
→ 宝箱漏检，且候选日志的区域过滤同样失效。

关键实测（归一化后横竖屏一致）：
  横屏 1278x1021：倒计时 (639,800) rx=0.500 ry=0.784
  竖屏 754x1337 ：倒计时 (376,1044) rx=0.499 ry=0.781
⇒ 区域阈值本身正确，错的是分母 → 改用真实 w/h 即可同时覆盖横竖屏。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_chest_region.py
"""
import os
import re
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


def _time_like(t: str) -> bool:
    t = t.strip().replace("O", "0").replace("o", "0")
    return bool(re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", t)
                or re.fullmatch(r"\d{1,2}:\d{2}", t))


# ── 真实截图 MaaOCR 结果（x, y, text, conf）────────────────
LAND = [  # 横屏 1278x1021「挂机关卡主界面」
    (137, 513, '1407', 0.99), (248, 513, '1408', 0.98),
    (358, 513, '1409', 0.99), (468, 513, '1410', 0.99),
    (639, 800, '00:01:44', 0.96), (230, 1010, '幻灵挑战', 0.99),
]
PORTRAIT = [  # 竖屏 754x1337 用户 21:33 截图
    (137, 558, '1407', 0.99), (248, 558, '1408', 0.98),
    (358, 558, '1409', 0.99), (468, 558, '1410', 0.99),
    (391, 872, '+20', 0.97),         # 宝箱加成数字（不是倒计时）
    (376, 1044, '00:30:11', 0.97),   # ← 真正的倒计时
    (230, 1112, '幻灵挑战', 0.99),
]


def find_chest(items, W, H):
    """与 tasks/auto_farm.py::_claim_idle_rewards 同构（v2.3.46 版）。"""
    return next(((x, y) for x, y, t, c in items
                 if c >= 0.8 and _time_like(t)
                 and 0.35 < x / W < 0.65
                 and 0.65 < y / H < 0.95), None)


def find_chest_legacy(items):
    """修复前：分母写死 1280/1024。"""
    return next(((x, y) for x, y, t, c in items
                 if c >= 0.8 and _time_like(t)
                 and 0.35 < x / 1280.0 < 0.65
                 and 0.65 < y / 1024.0 < 0.95), None)


print('── ① 横竖屏都能找到宝箱 ──')
check('1a 横屏 1278x1021 命中倒计时 (639,800)',
      find_chest(LAND, 1278, 1021) == (639, 800),
      str(find_chest(LAND, 1278, 1021)))
check('1b 竖屏 754x1337 命中倒计时 (376,1044)',
      find_chest(PORTRAIT, 754, 1337) == (376, 1044),
      str(find_chest(PORTRAIT, 754, 1337)))
check('1c 不会误抓宝箱加成「+20」',
      find_chest(PORTRAIT, 754, 1337) != (391, 872))

print('\n── ② 分叉证明：旧判据在竖屏必失败（用例对 bug 敏感）──')
check('2a 旧判据横屏仍命中（说明问题只在竖屏）',
      find_chest_legacy(LAND) == (639, 800), str(find_chest_legacy(LAND)))
check('2b 旧判据竖屏找不到宝箱', find_chest_legacy(PORTRAIT) is None,
      str(find_chest_legacy(PORTRAIT)))

print('\n── ③ 归一化位置横竖屏一致（证明「换分母」而非「改阈值」）──')
_lx, _ly = find_chest(LAND, 1278, 1021)
_px, _py = find_chest(PORTRAIT, 754, 1337)
check('3a 横屏 rx/ry 与竖屏差异 < 0.01',
      abs(_lx / 1278 - _px / 754) < 0.01 and abs(_ly / 1021 - _py / 1337) < 0.01,
      f'横({_lx/1278:.3f},{_ly/1021:.3f}) 竖({_px/754:.3f},{_py/1337:.3f})')

print('\n── ④ 源码静态断言（防回退）──')
src = open(os.path.join(ROOT, 'tasks/auto_farm.py'), encoding='utf-8').read()
check('4a 宝箱判据已用 _img_w/_img_h 归一',
      '_img_w' in src and '_img_h' in src
      and '/ _img_w' in src and '/ _img_h' in src)
check('4b 不再有 it[1]/1280 或 it[0]/1024 的宝箱判据',
      'it[1] / 1280.0' not in src.split('_claim_idle_rewards')[1][:4000]
      if '_claim_idle_rewards' in src else True)
check('4c 按比例偏移改用真实高度（不再写死 1024）',
      '0.060 * _img_h' in src, '0.060 * _img_h')
check('4d 拿不到窗口尺寸时有 1280x1024 兜底',
      '_img_w: int = 1280' in src)


print('\n── ⑤ 沙漏读数区域（同一领取流程，同样按真实尺寸归一）──')
# 常量（tasks/auto_farm.py）：rx > 0.70、0.55 < ry < 0.99
HG_RX_MIN, HG_RY_MIN, HG_RY_MAX = 0.70, 0.55, 0.99

def hg_hit(items, W, H):
    return [(x, y) for x, y, t, c in items
            if c >= 0.45 and x / W > HG_RX_MIN and HG_RY_MIN < y / H < HG_RY_MAX]

def hg_hit_legacy(items):
    return [(x, y) for x, y, t, c in items
            if c >= 0.45 and x / 1280.0 > HG_RX_MIN
            and HG_RY_MIN < y / 1024.0 < HG_RY_MAX]

# 竖屏截图右下「50」实测 (668,1028)；横屏主界面 x2 @ry0.769（项目注释记载）
HG_LAND = [(1150, 786, 'x2', 0.91)]        # 1278x1021 → rx.900 ry.770
HG_PORT = [(668, 1028, '50', 0.98)]        # 754x1337  → rx.886 ry.769

check('5a 横屏沙漏仍在读取窗内', len(hg_hit(HG_LAND, 1278, 1021)) == 1,
      str(hg_hit(HG_LAND, 1278, 1021)))
check('5b 竖屏沙漏在读取窗内（新分母）', len(hg_hit(HG_PORT, 754, 1337)) == 1,
      str(hg_hit(HG_PORT, 754, 1337)))
check('5c 旧分母在竖屏把沙漏挤出窗外（分叉证明）',
      len(hg_hit_legacy(HG_PORT)) == 0, str(hg_hit_legacy(HG_PORT)))
check('5d 横竖屏沙漏归一化位置一致（差异<0.02）',
      abs(1150 / 1278 - 668 / 754) < 0.02
      and abs(786 / 1021 - 1028 / 1337) < 0.02,
      f'横({1150/1278:.3f},{786/1021:.3f}) 竖({668/754:.3f},{1028/1337:.3f})')

check('5e 源码沙漏判据已用 _img_w/_img_h',
      '_HG_REGION_RX_MIN < it[1] / _img_w' in src
      and '_HG_REGION_RY_MIN < it[0] / _img_h' in src)

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
