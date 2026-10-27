"""
任务类目录：task_id → 任务类 映射

供 GUI（清单渲染/添加任务）与 TaskScheduler（实例化调度）共用。
import 本模块即触发所有任务类的注册（TaskRegistry.register）。
新增任务：写 tasks/xxx.py（继承 BaseTask）→ 本文件加一行。

历史：daily_quest / goddess_challenge 旧实现已废弃
（备份 backup/deprecated_tasks/），待后续重写后再注册。
"""

from tasks.auto_farm import AutoFarmTask
from tasks.labyrinth import LabyrinthTask
from tasks.goddess_trial import GoddessTrialTask
from tasks.home_task import HomeTask
from tasks.arena import ArenaTask
from tasks.daily_quest import DailyQuestTask
from tasks.friend_gift import FriendGiftTask
from tasks.dream_realm import DreamRealmTask
from tasks.game_control import CloseGameTask, StartGameTask

TASK_CLASSES = {
    AutoFarmTask.task_id: AutoFarmTask,
    LabyrinthTask.task_id: LabyrinthTask,
    GoddessTrialTask.task_id: GoddessTrialTask,
    HomeTask.task_id: HomeTask,
    ArenaTask.task_id: ArenaTask,
    DailyQuestTask.task_id: DailyQuestTask,
    FriendGiftTask.task_id: FriendGiftTask,
    DreamRealmTask.task_id: DreamRealmTask,
    StartGameTask.task_id: StartGameTask,
    CloseGameTask.task_id: CloseGameTask,
}
