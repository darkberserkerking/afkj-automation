"""
特殊任务：启动游戏 / 关闭游戏（v1.9.68 实装，用户 18:01 需求）。

────────────────────────────────────────────
【启动游戏 StartGameTask】
  状态检测：Unity 游戏窗口存在（bot.find_hwnd / 进程兜底）
    ├─ 已在运行 → 不执行任何操作，日志「游戏已在运行」→ done
    └─ 未运行   → 解析启动器路径（环境变量 > game_paths.json >
                   常见目录探测 > 开始菜单快捷方式；**无硬编码**）
                → Popen(Launcher.exe, --envId=… --channel=…)
                → 轮询等待游戏窗口出现（≤120s，5s 间隔）
                → 出现 → done；超时 → failed（日志给出排查指引）

【关闭游戏 CloseGameTask】
  状态检测同上
    ├─ 未运行 → 不执行任何操作，日志「游戏未运行」→ done
    └─ 运行中 → WM_CLOSE 优雅关闭 → 等窗口消失（≤15s）
                → 仍存在 → taskkill /PID /F 兜底 → done

【关键设计】
  · 两任务的 preflight **不检查游戏窗口**（启动游戏时必然无窗口，
    若沿用通用 preflight 会被拒跑）——只校验 bot 实例存在。
  · nav_target='' —— 不参与全局导航（游戏未开时任何导航都无意义）。
  · oneshot=True —— 单次动作型任务。
────────────────────────────────────────────
"""

from typing import Tuple

from task_registry import TaskRegistry, TaskMeta
from tasks.base import BaseTask
from tasks.game_launcher import (  # noqa: E402
    ENV_LAUNCHER_PATH, close_game, config_path, get_game_hwnd,
    is_game_ready, is_game_running, launch_game, resolve_launch_args,
    resolve_launcher_path)

_GROUP = '游戏任务'
_WAIT_MAX_S: int = 120     # 启动后等待游戏窗口上限
_POLL_S: float = 5.0       # 轮询间隔


class StartGameTask(BaseTask):
    """启动游戏（已在运行时跳过）。"""

    task_id = 'start_game'
    label = '启动游戏'
    options_schema = {}

    def preflight(self) -> Tuple[bool, str]:
        """仅校验 bot 实例——**不检查游戏窗口**（此时必然未启动）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        return True, ""

    def _stop_result(self) -> str:
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def _still_loading(self) -> bool:
        """画面是否仍未进入可交互状态（v2.3.18 起改正向判据）。

        v2.3.16 只判「命中加载文案」，读不到字就当"不是加载页"放行 —
        真机 2026-11-05 16:39 证明不够：窗口出现 6 秒时画面还是黑屏/
        logo（OCR 读不到任何文字），尺寸已有效 → 15s 就宣告 done，
        导航随即连判「未识别画面」。

        现在改为**正向判据**：只有 OCR 明确识别出某个已注册界面
        （登录页 / 世界·家园 / 挂机关卡页 …）才算就绪；黑屏、logo、
        初始化加载页、任何 unknown 一律继续等（仍受 _WAIT_MAX_S 上限）。

        测试桩（无 _ocr_screen）返回 False，交还原尺寸判定，保证裸
        object() 桩的干跑测试不崩。
        """
        if not hasattr(self.bot, '_ocr_screen'):
            return False
        try:
            items = self.bot._ocr_screen(no_cache=True) or []
        except Exception:
            # 截图失败（窗口刚出现/被遮挡）→ 保守当未就绪，由 120s 上限兜底
            return True
        from tasks.screen_registry import GLOBAL_SCREEN_REGISTRY
        return GLOBAL_SCREEN_REGISTRY.detect(items) in (
            'unknown', 'nav.loading')

    def summary(self) -> str:
        return "检测并启动游戏客户端（已运行则跳过）"

    def _activate_game_window(self) -> bool:
        """还原并把游戏窗口抢到前台（v2.2.5）。

        为什么要单独做：`is_game_ready` 只验**尺寸**，而最小化窗口的客户区
        仍报还原尺寸 → 会在窗口实际不可截图时报"就绪"。收尾显式激活可让
        后续任务直接开工（真机 17:47：未激活导致迷宫 75s 截图 0×0 超时）。

        Returns:
            bool: True = 已确认游戏窗口为前台且非最小化
        """
        try:
            hwnd = self.bot.find_and_focus()
        except Exception as e:
            self.log(f"[警告] 激活游戏窗口异常：{e}")
            hwnd = None
        if hwnd:
            self.log(f"[成功] 已激活游戏窗口 hwnd={hwnd}"
                     "（已还原并置前台）")
            return True
        self.log("[警告] 未能把游戏窗口置前台（可能被其它窗口占用前台）"
                 "——若后续任务截图为空，请手动点一下游戏窗口再重试")
        return False

    def run(self) -> str:
        self.log("启动（启动游戏）")
        # ① 状态检测
        if is_game_running(self.bot):
            self.log("[成功] 游戏已在运行，不执行任何操作")
            return 'done'
        # ② 解析启动器路径（跨环境适配）
        path = resolve_launcher_path()
        if not path:
            self.log("[错误] 未找到游戏启动器路径——请任选一种方式配置：")
            self.log(f"  ① 环境变量 {ENV_LAUNCHER_PATH}=<Launcher.exe 完整路径>")
            self.log(f"  ② 编辑配置文件 {config_path()}"
                     f"（字段 launcher_path）")
            self.log("  ③ 将 Farlight Launcher 安装到常见目录（自动探测）")
            return 'failed'
        args = resolve_launch_args()
        self.log(f"[信息] 启动器路径：{path}")
        self.log(f"[信息] 启动参数：{' '.join(args)}")
        # ③ 启动 + 等待【窗口出现】→【画面就绪】（v2.0.2：仅窗口
        # 存在不够——加载期截图 0 尺寸会让后续任务 OCR 全废）
        if not launch_game(path, args):
            self.log("[错误] 启动器进程创建失败（路径不可执行？）")
            return 'failed'
        waited = 0.0
        _win_logged = False
        _ready_streak = 0
        while waited < _WAIT_MAX_S:
            if self.check_stop():
                return self._stop_result()
            self._sleep(_POLL_S)
            waited += _POLL_S
            if not _win_logged and is_game_running(self.bot):
                _win_logged = True
                self.log(f"[信息] 游戏窗口已出现（{int(waited)}s），"
                         "等待画面就绪…")
            if is_game_ready(self.bot):
                # v2.3.16：尺寸有效但可能仍在初始化加载页（OCR 实测
                # 「正在初始化游戏（5/14）」conf≥0.96）→ 清零 streak 继续等，
                # 绝不把加载页当成「已进游戏」放行给下一任务的全局导航。
                if self._still_loading():
                    _ready_streak = 0
                    self.log(f"[信息] 仍在加载界面，继续等待画面就绪…"
                             f"（{int(waited)}/{_WAIT_MAX_S} 秒）")
                    continue
                # 连续 2 次尺寸有效且已越过加载页才算稳定
                _ready_streak += 1
                if _ready_streak >= 2:
                    self.log(f"[成功] 游戏已就绪（画面尺寸有效，"
                             f"等待 {int(waited)} 秒）")
                    # v2.2.5（用户 17:47「启动游戏后未激活窗口」）：
                    # 就绪 ≠ 可截图 —— 窗口可能仍在后台/最小化，导致后续
                    # 任务连续 75s「截图尺寸 0」、导航空转到超时（真机实证）。
                    # 故启动收尾**显式还原并置前台**（bot.find_and_focus
                    # 内含 SW_RESTORE + 双 attach 抢前台 + 前台验证）。
                    self._activate_game_window()
                    # v2.3.18：标记「刚启动完」——登录页之前的过渡画面
                    # （黑屏/logo/初始化）导航会判 unknown，需按加载界面
                    # 等待而非 ESC。识别到明确界面后由导航自动清除。
                    try:
                        self.bot._startup_pending = True
                    except Exception:
                        pass
                    return 'done'
            else:
                _ready_streak = 0
                if _win_logged:
                    self.log(f"[信息] 等待画面就绪…（{int(waited)}"
                             f"/{_WAIT_MAX_S} 秒）")
        if _win_logged:
            self.log(f"[错误] 游戏窗口已出现但 {_WAIT_MAX_S} 秒内画面未就绪"
                     "（仍在加载？可稍后手动重试任务）")
        else:
            self.log(f"[错误] 启动后 {_WAIT_MAX_S} 秒内未检测到游戏窗口")
        return 'failed'


class CloseGameTask(BaseTask):
    """关闭游戏（未运行时跳过）。"""

    task_id = 'close_game'
    label = '关闭游戏'
    options_schema = {}

    def preflight(self) -> Tuple[bool, str]:
        """仅校验 bot 实例——**不检查游戏窗口**（游戏可能未开）。"""
        if self.bot is None:
            return False, "bot 实例缺失"
        return True, ""

    def _stop_result(self) -> str:
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def summary(self) -> str:
        return "检测并关闭游戏客户端（未运行则跳过）"

    def run(self) -> str:
        self.log("启动（关闭游戏）")
        # ① 状态检测
        if not is_game_running(self.bot):
            self.log("[成功] 游戏未运行，不执行任何操作")
            return 'done'
        # ② 关闭（WM_CLOSE → 等待 → taskkill 兜底）
        hwnd = get_game_hwnd(self.bot)
        self.log(f"[信息] 关闭游戏窗口 hwnd={hwnd}（优雅关闭 → 兜底强杀）")
        ok = close_game(hwnd, should_stop=self.check_stop)
        if self.check_stop():
            return self._stop_result()
        # v2.0.9（真机 20:12 误报）：**只看窗口**验证——进程兜底会被
        # 常驻的 Farlight Launcher 命中，导致游戏已关却报失败
        if ok and not is_game_running(self.bot, ignore_process=True):
            self.log("[成功] 游戏已关闭（窗口已消失）")
            return 'done'
        self.log("[警告] 关闭动作已执行，但游戏窗口仍在（可手动确认）")
        return 'failed'


TaskRegistry.register(TaskMeta(
    task_id=StartGameTask.task_id,
    label=StartGameTask.label,
    description="未启动则拉起游戏，已启动则跳过",
    default_enabled=False,
    icon="🚀",
    group=_GROUP,
    status="ready",
    options_schema=StartGameTask.options_schema,
    nav_target='',
    oneshot=True,
))
TaskRegistry.register(TaskMeta(
    task_id=CloseGameTask.task_id,
    label=CloseGameTask.label,
    description="运行中则关闭游戏，未启动则跳过",
    default_enabled=False,
    icon="🛑",
    group=_GROUP,
    status="ready",
    options_schema=CloseGameTask.options_schema,
    nav_target='',
    oneshot=True,
))
