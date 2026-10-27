"""
afk_bot — AFK Journey 自动化工具核心包

三层架构（v1.5.6 重构）：
  infra/      默认开启：OCR 引擎、截图、输入、坐标、统计、配置
  services/   全局可用：导航、阶段检测
  auto_farm/  仅自动推图：主循环编排、阵容、战斗监测
  facade.py   跨层门面 AFKBot（对外 API 保持向后兼容）
"""

from afk_bot.config import (
    VERSION, _SNAP_TRIM, _BTN_POS, _BTN_POS_PORTRAIT, default_config,
)
# ① 默认开启（infra）
from afk_bot.infra.coordinate import CoordSystem
from afk_bot.infra.screen import ScreenCapture
from afk_bot.infra.input import InputController
from afk_bot.infra.ocr_memory import OCRMemory, OCRStrategy
from afk_bot.infra.ocr_engine import TextRecognizer
from afk_bot.infra.stats import StatsTracker
# ② 全局可用（services）
from afk_bot.services.detector import PhaseDetector
from afk_bot.services.navigator import Navigator
# ③ 仅自动推图（auto_farm）
from afk_bot.auto_farm.battle import BattleMonitor
from afk_bot.auto_farm.formation import FormationManager
from afk_bot.auto_farm.loop import AFKBotCore
# 门面
from afk_bot.facade import AFKBot

# ── 全局 pyautogui 设置 ─────────────────────────────────────
import pyautogui
pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.05

# ── 任务模块系统（副作用导入）───────────────────────────────
# 必须在包加载时导入以触发任务注册
import task_registry  # noqa: E402
import tasks  # noqa: E402
import tasks.auto_farm  # noqa: E402, F401

__all__ = [
    'VERSION',
    'AFKBot',
    'CoordSystem',
    'ScreenCapture',
    'InputController',
    'OCRMemory',
    'OCRStrategy',
    'TextRecognizer',
    'PhaseDetector',
    'BattleMonitor',
    'FormationManager',
    'StatsTracker',
    'Navigator',
    'AFKBotCore',
    '_SNAP_TRIM',
    '_BTN_POS',
    '_BTN_POS_PORTRAIT',
    'default_config',
]
