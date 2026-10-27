"""
任务模块目录
自动扫描并注册所有继承 BaseTask 的子类。
"""

import os
import sys

# 脚本根目录加入 import path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tasks.base import BaseTask
from task_registry import TaskRegistry, TaskMeta

__all__ = ["BaseTask", "TaskRegistry", "TaskMeta"]
