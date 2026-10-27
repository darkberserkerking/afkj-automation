"""
afk_bot 全局配置模块

包含版本号、截图裁剪、按钮比例坐标字典、默认运行配置等模块级常量。
从原 afk_bot.py 中抽取，保持零回归。

注意：pyautogui 全局设置（FAILSAFE=False, PAUSE=0.05）在 afk_bot 包的
__init__.py 中延迟执行，避免 config 模块直接依赖 pyautogui。
"""

import json
import os
from typing import Dict
import sys

# ── 版本号 ──────────────────────────────────────────────────────
VERSION: str = 'v2.2.31'

# v1.8.49：日志级别标志表——下沉到中性层，供 tasks 与 afk_bot 共用
# （此前 tasks/base.py 与 auto_farm/loop.py 各存一份，属重复真相源）
LEVEL_PREFIXES: Dict[str, str] = {
    'info': '[信息]', 'ok': '[成功]', 'warn': '[警告]',
    'error': '[错误]', 'click': '[点击]', 'data': '[数据]',
    'config': '[配置]', 'debug': '[调试]',
}

# ── 截图优化 ───────────────────────────────────────────────────
# 截图优化：裁剪掉窗口装饰区域（标题栏+边框），只截游戏内容
# 设为 0 则截全窗口（包含装饰），设为 >0 则裁掉对应像素
_SNAP_TRIM: int = 0  # 实测 Unity 窗口没有装饰边框，设为 0 即可

# ── 按钮比例坐标 fallback（横屏 16:9）─────────────────────────
# 格式: {模板名: (rx, ry)}  rx/ry 是相对于窗口的比例 0.0~1.0
# 当 afkj_config.json 不可用或无 "button_positions" 节时使用
_BTN_POS_FALLBACK: dict = {
    # 挂机关卡页（2级）
    'challenge': (0.647, 0.191),   # 挑战 - 挂机关卡页右上
    'huanling':  (0.648, 0.207),   # 幻灵挑战 - 挂机关卡页右上（略低）
    'options':   (0.967, 0.947),   # ≡选项 - 0 层（世界/家园）右下角
    # 玩法目录（playbook state）
    'wanfa':     (0.297, 0.759),   # 玩法目录 - 底部
    'normal':    (0.500, 0.300),   # 常规玩法 - 玩法目录中部
    'activity':  (0.498, 0.116),   # 活动 - 玩法目录顶部中央
    # 选项菜单
    'gear':      (0.717, 0.944),   # ⚙设置齿轮 - 选项菜单右下
    # 备战/阵容界面
    'auto':      (0.500, 0.380),   # 自动挑战 - 备战界面
}

# ── 按钮比例坐标 fallback（竖屏 9:16）─────────────────────────
# 根据截图估算
_BTN_POS_PORTRAIT_FALLBACK: dict = {
    # 挂机关卡页（2级）
    'challenge': (0.75, 0.88),     # 挑战 - 挂机关卡页底部右侧
    'huanling':  (0.25, 0.88),     # 幻灵挑战 - 挂机关卡页底部左侧
    'options':   (0.90, 0.95),     # ≡选项 - 0 层（世界/家园）右下角
    # 玩法目录
    'wanfa':     (0.50, 0.85),     # 玩法目录 - 底部
    'normal':    (0.50, 0.40),     # 常规玩法 - 玩法目录中部
    'activity':  (0.50, 0.15),     # 活动 - 玩法目录顶部
    # 选项菜单
    'gear':      (0.85, 0.95),     # ⚙设置齿轮 - 选项菜单右下
    # 备战/阵容界面
    'auto':      (0.42, 0.92),     # 自动挑战 - 备战界面底部中央偏左
}


def _load_button_positions():
    """从 afkj_config.json 加载按钮比例坐标。

    查找策略（按优先级）：
    1. 可执行文件所在目录（sys.argv[0]）
    2. afk_bot 包的上级目录（__file__ 向上两级）

    若 JSON 不存在、损坏或缺少 button_positions 节，则返回 fallback 值。
    JSON 中的坐标以 [rx, ry] 数组形式存储，加载时转换为 (rx, ry) 元组。

    Returns:
        (landscape_dict, portrait_dict): 横屏和竖屏的按钮坐标字典
    """
    # 构建候选查找路径
    candidates: list[str] = []
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        candidates.append(os.path.join(exe_dir, 'afkj_config.json'))
    except Exception:
        pass
    try:
        pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates.append(os.path.join(pkg_parent, 'afkj_config.json'))
    except Exception:
        pass

    # 去重
    seen: set[str] = set()
    unique_candidates: list[str] = []
    for p in candidates:
        if p not in seen:
            seen.add(p)
            unique_candidates.append(p)

    for config_path in unique_candidates:
        try:
            if not os.path.exists(config_path):
                continue
            with open(config_path, 'r', encoding='utf-8') as f:
                data: dict = json.load(f)
            bp = data.get('button_positions')
            if bp is None:
                continue
            landscape_raw = bp.get('landscape', {})
            portrait_raw = bp.get('portrait', {})
            if not landscape_raw and not portrait_raw:
                continue
            # 将 JSON 中的 [rx, ry] 列表转换为 (rx, ry) 元组
            landscape = {k: tuple(v) for k, v in landscape_raw.items()}
            portrait = {k: tuple(v) for k, v in portrait_raw.items()}
            return landscape, portrait
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue

    # 所有路径都失败，返回 fallback
    return _BTN_POS_FALLBACK.copy(), _BTN_POS_PORTRAIT_FALLBACK.copy()


# ── 模块级按钮坐标（从 JSON 加载，fallback 为硬编码值）───────
_BTN_POS, _BTN_POS_PORTRAIT = _load_button_positions()

# ── 默认运行配置 ───────────────────────────────────────────────
# 与 afkj_config.json 结构一致，提供完整的默认值
default_config: dict = {
    'stuck': {
        'sample_interval': 0.3,
        'threshold': 10,
        'max_recovery_attempts': 3,
    },
    'battle': {
        'miss_threshold': 5,
        'min_duration': 60,
        'ocr_interval_initial': 1,
        'ocr_interval_stable': 3,
        'max_diff': 10,
        'max_iter_navigate': 40,
        'max_attempts_close_result': 10,
    },
}
