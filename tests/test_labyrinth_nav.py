"""
异界迷宫 · 导航真机验证脚本（v1.7.2）

用途：验证「0 层 → 玩法目录 → 滚动找卡片 → 点进异界迷宫」这条导航链。
**不走 TaskScheduler**，直接实例化任务调用 run()，绕过 planned 状态被 skipped 的限制，
便于在 status 转 'ready' 之前先在真机上把导航跑通。

前置条件：
  1. 以**管理员权限**运行（Unity 窗口输入需要）
  2. 游戏已启动，停在任意界面（脚本会自己 ESC / 点玩法目录）
  3. 鼠标不要挡住玩法目录中间区域

用法：
    conda run -n afk_gpu python tests/test_labyrinth_nav.py

退出码：0 = 导航成功，1 = 失败/停止
"""

import sys
import os
import threading
import time

# 保证从任意目录运行都能 import 项目根模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from afk_bot import AFKBot                      # noqa: E402
from tasks.labyrinth import LabyrinthTask       # noqa: E402


def _log(msg: str) -> None:
    ts: str = time.strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)


def main() -> int:
    _log('=' * 54)
    _log('异界迷宫 · 导航真机验证（v1.7.2）')
    _log('=' * 54)

    # 1. 构造 bot（与 GUI 一致的 config 键）
    cfg: dict = {
        'log_callback': _log,
        'status_callback': lambda s: None,
    }
    _log('正在初始化 AFKBot（加载 OCR 模型，约数秒）...')
    bot = AFKBot(cfg)

    # 2. 构造任务上下文
    ctx: dict = {
        'bot': bot,
        'logger': _log,
        'stats': {},
        'stop_event': threading.Event(),
        'pause_event': threading.Event(),
        'pause_event_set': True,
        'options': {},
        'max_time': None,
        'inst_id': 'labyrinth#nav-test',
    }
    # pause_event 需默认置位（未暂停），否则 check_stop() 会判定为暂停态
    ctx['pause_event'].set()

    task = LabyrinthTask(ctx)

    # 3. 前置检查
    ok, reason = task.preflight()
    if not ok:
        _log(f'[失败] preflight 不通过 | {reason}')
        return 1
    _log(f'preflight 通过 | {reason}')

    # 4. 跑导航
    _log('-' * 54)
    _log('开始导航（进入异界迷宫界面即停）...')
    _log('-' * 54)
    started: float = time.time()
    result: str = task.run()
    cost: float = time.time() - started

    # 5. 结论
    _log('-' * 54)
    _log(f'结果 = {result}  耗时 = {cost:.1f}s')
    if result == 'done':
        _log('✅ 导航成功：已停在异界迷宫界面')
        _log('   下一步：请截图异界迷宫界面，用于实现玩法主体')
        return 0
    if result == 'stopped':
        _log('⏹ 被停止')
        return 1
    _log('❌ 导航失败：请检查上面的日志（重点看"导航中 | phase"序列）')
    _log('   常见原因：')
    _log('   - 未用管理员权限运行 → 点击/滚轮不生效')
    _log('   - 玩法目录默认停在「活动」tab → 需先切到「常规玩法」')
    _log('   - 可滚动区比例坐标与实际不符 → 调整 tasks/labyrinth.py 的 _SCROLL_REGION')
    _log('   - 滚轮格数不够/过头 → 调整 _SCROLL_CLICKS / _SCROLL_MAX')
    return 1


if __name__ == '__main__':
    sys.exit(main())
