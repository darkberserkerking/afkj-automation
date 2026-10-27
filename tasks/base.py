"""
AFKJ 任务模块基类
定义所有任务模块的通用接口，所有具体任务继承此类。
"""

import os
import time
import logging
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional, Tuple
# v1.8.49：优先复用中性层单一真相源；但 afk_bot/__init__ 存在反向
# import tasks（见审查报告 P0-1），顶层直连会成环 → try 失败时本地兜底
try:
    from afk_bot.config import LEVEL_PREFIXES as CFG_LEVEL_PREFIXES  # noqa: E402
except Exception:  # ImportError: partially initialized module（循环导入保护）
    CFG_LEVEL_PREFIXES = {
        'info': '[信息]', 'ok': '[成功]', 'warn': '[警告]',
        'error': '[错误]', 'click': '[点击]', 'data': '[数据]',
        'config': '[配置]', 'debug': '[调试]',
    }

# ── v1.8.24 通用返回按钮模板（用户定案：ESC 不万能，须有返回按钮兜底）──
# 三款式 RGBA 透明背景（黑底灰箭头 / 暗紫底白箭头 / 纯白箭头），
# 与 labyrinth v1.8.2 采集的模板同源，统一提到基类供全任务共用。
_TPL_DIR: str = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'templates'
)

# v2.1.27（P0-1 日志降噪）：状态类日志的**全局最小间隔**（秒）。
# 距上次状态打印不足该间隔则丢弃 —— 既压掉轮询刷屏，又保留周期性心跳。
# 为何不用"与上一次 key 比较"：真机噪声是**振荡**的
# （unknown→door→battle_prep→unknown…，实测连续重复仅 2.2%），
# 每帧都与上一帧不同，"变化才打"完全无效。
_STATE_LOG_MIN_INTERVAL: float = 10.0
BACK_BUTTON_TPLS: List[str] = [
    os.path.join(_TPL_DIR, 'btn_back_dark.png'),
    os.path.join(_TPL_DIR, 'btn_back_purple.png'),
    os.path.join(_TPL_DIR, 'btn_back_arrow.png'),
]
BACK_THRESHOLD: float = 0.75


class BaseTask(ABC):
    """任务基类，所有具体任务必须实现以下属性和方法"""

    # ── 静态属性（子类必须定义）──────────────────────────────
    task_id: str = ""       # 唯一ID，如 "auto_farm"

    # ── v2.1.9（用户 22:35 规范）循环语义显式声明 ──────────────
    # 全局导航要求：「当前已处于任务的可循环界面序列内 → 可就地开始执行该
    # 循环；除非任务对步骤有严格先后要求，则必须从起始步骤重来」。
    # 此前该判断散落在各任务 startup_probe() 内部（无法统一审计），现提取
    # 为类属性声明。判定优先级（见 tasks/global_nav.py + docs/导航能力规范.md）：
    #   ① loop_entry_strict=True → 一律回 nav_target 从起始步骤重来
    #   ② 当前画面 ∈ loop_screens → 跳过任务间导航，就地续跑
    #   ③ 当前画面 ∈ near_screens → 只做「进入」一步（不退回入口层）
    #   ④ 其余 → 常规 BFS 导航到 nav_target

    #: 循环内**可直接进入**的画面集合（就地续跑，无需任何导航）
    loop_screens: frozenset = frozenset()

    #: 「家门口」画面：差一步即可进循环（例：异界迷宫主页 → 点「进入」）
    near_screens: frozenset = frozenset()

    #: 步骤是否**严格有序**（True → 无论当前在循环何处都必须从头开始）
    loop_entry_strict: bool = False

    @classmethod
    def loop_ready(cls, screen_id: str) -> str:
        """按声明判定当前画面与任务循环的关系（v2.1.9）。

        Returns:
            'strict' → 任务要求严格顺序，必须从头开始（优先级最高）
            'in'     → 已在循环内，可就地续跑
            'near'   → 家门口，只需「进入」一步
            ''       → 需常规导航
        """
        if cls.loop_entry_strict:
            return 'strict'
        if screen_id and screen_id in cls.loop_screens:
            return 'in'
        if screen_id and screen_id in cls.near_screens:
            return 'near'
        return ''

    label: str = ""         # 显示名称，如 "自动推图"
    description: str = ""   # 简短描述

    # ── 参数声明（子类可选覆盖）─────────────────────────────
    # GUI 据此自动生成参数表单，新增任务无需编写任何 UI 代码。
    # 格式：
    #   {
    #     "key": {
    #       "type": "int" | "enum" | "bool" | "str",
    #       "label": "显示名",
    #       "default": 默认值,
    #       "choices": [(值, 显示名), ...],   # enum 类型专用
    #       "desc": "说明文字",
    #     }
    #   }
    options_schema: Dict[str, Any] = {}

    # 本任务失败后，调度器是否继续执行后续任务
    continue_on_failure: bool = False

    # ── 实例属性 ───────────────────────────────────────────
    def __init__(self, ctx: dict):
        """
        ctx: 任务上下文，由主程序传入，包含：
          - bot: AFKBot 实例（拥有所有核心方法）
          - logger: 日志函数，调用 logger(msg) 输出日志
          - stats: dict，任务可自由读写统计计数
          - stop_event: threading.Event，收到停止信号时置位
          - pause_event: threading.Event，收到暂停信号时清空
          - options: dict，任务参数
          - max_time: int|None，任务最大执行秒数（None=无限）
          - max_turns: int|None，任务最大战斗轮次（None=无限）
          - start_turns: int，实例启动时的轮次基线
          - inst_id: str，实例唯一标识（同种任务多实例）
        """
        self.bot = ctx.get("bot")
        self._logger = ctx.get("logger", print)
        self.stats = ctx.get("stats", {})
        self._stop = ctx.get("stop_event")
        self._pause = ctx.get("pause_event")
        self._started = time.time()
        self.inst_id = ctx.get("inst_id", "")
        self.max_time = ctx.get("max_time")          # 秒，None=无限
        self.max_turns = ctx.get("max_turns")        # 轮，None=无限
        self._start_turns = ctx.get("start_turns", 0)

        # 任务参数：外部传入值优先，未传的用 options_schema 默认值补齐
        self.options: Dict[str, Any] = dict(ctx.get("options", {}))
        for key, spec in type(self).options_schema.items():
            self.options.setdefault(key, spec.get("default"))

    # ── v1.8.26 统一日志规范（用户定案：全工具统一风格，新任务强制）──
    # 级别 → 标志 → GUI 颜色 对照（完整规范见 docs/开发指导手册.md §日志）：
    #   info  [信息] 白/灰（GUI 按 关键词 细分 dim/data）——过程与状态
    #   ok    [成功] 绿 #16a34a —— 阶段性成功节点
    #   warn  [警告] 橙 #b45309 —— 异常但可恢复（将走兜底/重试）
    #   error [错误] 红 #dc2626 —— 失败/任务终止
    #   click [点击] 青 #0891b2 —— 关键点击动作（门卡/按钮，可选细粒度）
    #   data  [数据] 紫 #7c3aed —— 坐标/统计/进度数据
    #   config[配置] 蓝 #2563eb —— 配置加载/参数
    #   debug [调试] 浅灰 #9ca3af —— 仅 verbose 模式
    # v1.8.49：单一真相源下沉 afk_bot/config.py（规范 2.2）
    LEVEL_PREFIXES: Dict[str, str] = CFG_LEVEL_PREFIXES

    # v2.1.18：`_sleep` 分段可中断的步长（秒）——决定停止的最大响应延迟
    _SLEEP_STEP: float = 0.1

    def log(self, msg: str, level: str = 'info'):
        """统一日志出口（v1.8.26 规范化）。

        规则：
          1. 消息若已自带级别标志（[信息]/[成功]/… 开头）→ 原样保留
             （兼容存量 200+ 处 `self.log('[信息] xxx')` 调用，渲染不变）
          2. 否则按 level 参数补级别标志（缺省 info）
          3. 最终格式：`  [任务名] [级别] 内容`（GUI 按括号标志分词着色）

        Args:
            msg: 消息内容（可自带级别标志）
            level: 级别 key，见 LEVEL_PREFIXES
        """
        if not any(
            msg.startswith(p) for p in self.LEVEL_PREFIXES.values()
        ):
            msg = f'{self.LEVEL_PREFIXES.get(level, "[信息]")} {msg}'
        self._logger(f'  [{self.label}] {msg}')

    def log_state(self, key: str, msg: str) -> None:
        """状态类日志：**全局时间限流**（同状态/异状态一视同仁）。

        v2.1.27（P0-1）：真机日志实测「玩法循环 | <状态>」家族占全量
        **22.7%**（7,029 行 / 仅 26 种模式），把点击/成功/警告等关键事件
        淹没了。

        **为什么不是"状态变化才打印"**：真机噪声是**振荡**的
        —— unknown ↔ door ↔ battle_prep 交替出现（转场期每轮 OCR 一个状态），
        实测**连续重复仅占 2.2%**，即"与上一帧比较"几乎每帧都算变化，
        完全压不掉噪声。故改为**全局最小间隔**：距上次状态打印不足
        `_STATE_LOG_MIN_INTERVAL` 秒则直接丢弃。

        效果：轮询约 1.4s/帧时，该家族行数降到约 1/7，而**每 N 秒仍有一条**
        心跳（用户不会误判卡死）；状态切换本身伴随的动作日志
        （`[点击]` / `备战画面 → 按 F` / `[成功]`）不受影响，仍逐条输出。

        Args:
            key: 状态标识（当前仅用于调试定位，不影响限流判定）
            msg: 展示文案
        """
        now: float = time.time()
        last_at: float = getattr(self, '_state_log_at', 0.0)
        if last_at and (now - last_at) < _STATE_LOG_MIN_INTERVAL:
            return
        self._state_log_at = now
        self._state_key_prev = key
        self.log(msg)

    def count_iter(self) -> int:
        """主循环迭代计数 +1（返回累计值）。

        v2.1.27（P0-1 配套）：此前测试用「玩法循环 |」日志条数当迭代数，
        日志一旦降噪该方法即失效（**日志被当成 API**）。改为显式计数器，
        语义明确且不受日志策略影响。
        """
        self._loop_iters: int = getattr(self, '_loop_iters', 0) + 1
        return self._loop_iters

    def _sleep(self, sec: float) -> None:
        """统一休眠钩子（v1.9.2）：默认真实 sleep，测试可注入 no-op。

        生产侧所有等待统一走本方法；干跑测试只需 monkeypatch 本方法即可
        跳过真实等待。

        v2.1.18（用户定案「点击停止后立即停止运行」）：改为**分段可中断**——
        停止信号置位后最迟 `_SLEEP_STEP`(0.1s) 返回，不再把 1.5~180s 的长
        等待一次性睡完。未收到停止信号时总时长与 time.sleep(sec) 等价。

        注意：内部**只直接查 `self._stop`，不调 check_stop()**——后者的暂停
        分支会回调 `_sleep(0.5)`，经此调用链会无限递归。
        """
        if not sec or sec <= 0:
            return
        if self._stop is None:
            time.sleep(sec)          # 无调度上下文（单测/独立调用）
            return
        if self._stop.is_set():
            return                   # 已停止 → 一秒都不等
        if sec <= self._SLEEP_STEP:
            time.sleep(sec)
            return
        waited: float = 0.0
        while waited < sec:
            chunk: float = min(self._SLEEP_STEP, sec - waited)
            time.sleep(chunk)
            waited += chunk
            if self._stop.is_set():
                return

    @property
    def elapsed(self) -> int:
        return int(time.time() - self._started)

    @property
    def turns(self) -> int:
        """当前战斗轮次（相对实例启动的增量）"""
        base = self.stats.get("total_battles", 0)
        return max(0, base - self._start_turns)

    def check_completion(self) -> Tuple[bool, str]:
        """检查是否达到任务结束指标（时间/轮次，OR 逻辑）。

        任一满足即结束；两者未设定则永不因指标结束
        （由 bot 侧"全部关卡完成"兜底）。

        Returns:
            (是否应结束, 结束原因描述)
        """
        if self.max_time is not None and self.elapsed >= self.max_time:
            return True, f"已达时间上限 {self.max_time}s"
        if self.max_turns is not None and self.turns >= self.max_turns:
            return True, f"已达轮次上限 {self.max_turns} 轮"
        return False, ""

    # ── 必须实现的方法 ─────────────────────────────────────

    @abstractmethod
    def run(self) -> str:
        """
        执行任务主体逻辑。
        返回字符串状态码：
          "done"    — 任务正常完成
          "failed"  — 任务执行失败
          "stopped" — 用户手动停止
        """
        ...

    # ── 可选覆盖的方法 ─────────────────────────────────────

    def on_start(self):
        """任务开始前调用，可用于初始化"""
        pass

    def on_end(self, result: str):
        """任务结束后调用，可用于清理或后处理"""
        pass

    def check_stop(self) -> bool:
        """检查是否收到停止信号，返回 True 表示应该停止"""
        if self._stop and self._stop.is_set():
            return True
        if self._pause and not self._pause.is_set():
            # 暂停时等待恢复，超时返回 stopped
            timeout = 30
            waited = 0
            while not self._pause.is_set():
                self._sleep(0.5)
                waited += 0.5
                if waited >= timeout:
                    self.log("暂停超时，停止任务")
                    return True
        return False

    # ── v1.9.69 公共默认实现（收敛各任务逐类重复的样板）──────────

    def _stop_result(self) -> str:
        """手动停止的统一返回值（此前 10 个任务类各写一份）。"""
        self.log("[信息] 任务被手动停止")
        return 'stopped'

    def _texts(self, items: List[Tuple],
               min_conf: float = 0.5) -> List[str]:
        """从 OCR items 提取文本列表（此前 5 个任务类各写一份）。"""
        return [it[2] for it in items
                if len(it) >= 4 and it[3] >= min_conf]

    def log_nav_failed(self, label: str, reached: str = '') -> None:
        """导航失败的统一文案（v2.1.27 P2-6）：结果 + 现场 + 建议。

        此前 7 处都是「导航失败：未能进入X」——只有结果，用户无法判断是
        游戏没开、停在了别的界面、还是识别失败，只能来问一遍。

        Args:
            label: 目标界面中文名（如「异界迷宫」）
            reached: 调用方若持有导航器，传 `nav.last_screen`；没有则
                退化读任务自身的 `_last_screen` / `_nav_last_screen`
        """
        last: str = (reached
                     or getattr(self, '_last_screen', '')
                     or getattr(self, '_nav_last_screen', ''))
        if last:
            try:
                from tasks.screen_registry import zh_screen
                ctx: str = f"｜最后画面={zh_screen(last)}"
            except Exception as _e:
                self.log(f"[调试] log_nav_failed() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
                ctx = f"｜最后画面={last}"
        else:
            ctx = "｜最后画面=未识别"
        self.log(
            f"[错误] 导航失败：未能进入{label}{ctx}"
            "｜建议：确认游戏已打开、窗口未被遮挡；重试一次仍失败请保留日志"
        )

    def _dismiss_reward_popup(self, items: List[Tuple],
                              label: str = '弹层数字') -> bool:
        """关闭奖励弹层：优先「点击空白处」，否则点中央奖励数字。

        v1.9.69 从 daily_quest/friend_gift 收敛到基类（两处同构）。
        中央数字限定 rx 0.35~0.65 / ry 0.25~0.65，避开界面其他数字。
        """
        import re
        if self.dismiss_blink_popup(items):
            return True
        num_pos = next(
            ((it[1], it[0]) for it in items
             if len(it) >= 4 and it[3] >= 0.8
             and re.fullmatch(r"\d{2,5}", it[2].strip())
             and 0.35 < it[1] / 1280.0 < 0.65
             and 0.25 < it[0] / 1024.0 < 0.65), None)
        if num_pos:
            self.log(f"[点击] 弹层数字 | 图像={num_pos}")
            self.bot.click_at(num_pos[0], num_pos[1], label)
            self._sleep(1.2)
            return True
        return False
    def preflight(self) -> Tuple[bool, str]:
        """执行前检查，由调度器调用。

        子类可覆盖以加入自定义前置条件（如登录态、特定界面等）。

        Returns:
            (是否可以执行, 不可执行时的原因)
        """
        if self.bot is None:
            return False, "bot 实例缺失"
        return True, ""

    def startup_probe(self) -> str:
        """启动探测（v1.8.7 框架化）——run() 的第一个动作。

        识别「当前画面是否已在任务执行域内」，让工具支持**任意界面启动**：
        用户在游戏任何界面开始任务时，直接找到最近的可执行点收敛到主循环，
        不做无谓的"退回 0 层再重新导航"。界面按层级组织（0层 world/home →
        1级 playbook → 2级 任务玩法页 → 任务私有内部态），本探测只关心
        **任务执行域**（2级玩法页内部态等任务可执行点）。

        Returns:
            str 三态：
              'ready'  — 已在任务可执行界面 → run() 跳过「导航+进入」，
                         直接进入任务主循环（最优路径）
              'clear'  — 画面不在任务执行域 → 走正常导航流程（默认行为）
              'failed' — 环境异常（如残留弹窗关不掉）→ run() 直接失败返回

        覆写契约（后续所有任务强制，见 docs/开发指导手册.md「任务启动
        定位契约」SOP）：
          1. 执行域判据用**任务自己的**判定能力，不要依赖 detector 判
             任务内部态——detector 只负责 0层/1级/2级 通用界面（多轮真机
             验证：迷宫内部态等对通用 phase 判定有盲区）。labyrinth 的
             startup_probe 是样板（自 OCR + 玩法状态机判 imprint/shop/
             battle_prep/victory/failure/boss_cleared/door）。
          2. 返回 'ready' 后 run() 必须能直接续跑该界面（各状态分支自带
             对应动作），不得假定画面会自己回到入口。
          3. 探测应尽量复用 run() 原本就要做的第一帧 OCR（labyrinth 把
             弹窗清理并入探测，零额外 OCR 消耗）。
          4. 未覆写时默认返回 'clear'（保持传统「从 0 层导航」行为），
             旧任务不因框架升级而改变语义。
        """
        return 'clear'

    # ── v1.8.12 全局闪烁弹窗清理（用户规则：全任务通用，禁止判 unknown）──
    # 子串判定覆盖「点击空白处关闭」「点击空白处继续」「点击空白处继续(N秒)」
    _BLINK_POPUP_KW: str = '点击空白处'

    # ── v2.0.4（用户 18:38）退出确认弹窗（ESC 陷阱）：最高优先级 ──
    _QUIT_DLG_KW: str = '确定要退出游戏'
    _QUIT_CANCEL_TPL: str = 'btn_confirm_cancel.png'

    def _window_present(self) -> bool:
        """游戏窗口是否存在（v2.0.5：无窗口→跳过全部弹窗清理/OCR）。"""
        try:
            return bool(self.bot.find_hwnd())
        except Exception as _e:
            self.log(f"[调试] _window_present() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return True   # 查询失败时保持旧行为（不阻断）

    def _dismiss_quit_dialog(self, items: List[Tuple] = None) -> bool:
        """退出确认弹窗处理（点 X 取消）——**最高优先级**。

        识别：OCR 含「确定要退出游戏」（真机实测 conf 0.99）。
        点击：① 模板 btn_confirm_cancel.png（maskedNCC，用户裁图）
              ② 兜底 = 以弹窗文字为锚点推算 X 位置（X 在文字左下方：
                 dx = -0.264×屏宽、dy = +171px——用户裁图实测比例）
        绝不点确定（避免真退出游戏）。
        """
        # v2.0.5：无窗口（游戏未启动）→ 直接返回，不做 OCR
        if not self._window_present():
            return False
        if items is None:
            if not hasattr(self.bot, "_ocr_screen"):
                return False
            items = self.bot._ocr_screen()
        hit = any(len(it) >= 4 and it[3] >= 0.8
                  and self._QUIT_DLG_KW in it[2] for it in items)
        if not hit:
            return False
        # ① 模板匹配 X 按钮
        pos = self.bot.template_match(
            self._QUIT_CANCEL_TPL, threshold=0.75, silent=True,
            scale_range=(0.8, 1.3, 0.1))
        if pos:
            self.log(f"[点击] 退出确认弹窗「X」取消按钮 | 屏幕={pos}")
            self.bot._click_abs(*pos)
            self._sleep(1.2)
            return True
        # ② 锚点兜底（弹窗文字左下方）
        anchor = next(((it[1], it[0]) for it in items
                       if len(it) >= 4 and it[3] >= 0.8
                       and self._QUIT_DLG_KW in it[2]), None)
        if anchor:
            x = int(anchor[0] - 0.264 * 1280)
            y = int(anchor[1] + 171)
            self.log(f"[点击] 退出确认弹窗「X」（锚点兜底） | 图像={(x, y)}")
            self.bot.click_at(x, y, '退出确认_X')
            self._sleep(1.2)
            return True
        return False

    def dismiss_blink_popup(
        self, items: List[Tuple] = None, max_ocr: int = 2, interval: float = 1.0
    ) -> bool:
        """清理「点击空白处关闭/继续」类闪烁弹窗（文字 1 秒周期闪烁）。

        全局规则（用户 2026-09-04）：此类弹窗（附加挑战/挑战奖励等）必须
        点掉，**不允许被判为 unknown 走 ESC**。判定依据 = OCR 含「点击
        空白处」子串（覆盖 关闭/继续/(N秒) 变体），命中即点该文字位置。

        Args:
            items: 调用方已持有的 OCR 帧。传入时**只查当前帧**（不额外
                OCR——调用方主循环的轮询本身就是多次 OCR，天然覆盖闪烁
                相位）；None 时自行 OCR max_ocr 次（间隔 interval 秒），
                用于无循环轮询的调用点。
            max_ocr: items=None 时的自扫描次数
            interval: 自扫描间隔秒（对齐闪烁周期 1s）

        Returns:
            bool: 点掉弹窗返回 True；未命中返回 False（不睡眠、零副作用，
                  调用方可安全地在每个 unknown 帧调用）。

        新增任务必须在 unknown/找按钮失败 的处理分支调用本方法（手册 §3）。
        """
        # v2.0.5（用户 18:48）：无窗口（游戏未启动）→ 静默跳过，不 OCR
        # （此前调度器任务前调用会在没开游戏时产生 0 尺寸截图警告）
        if items is None and not self._window_present():
            return False
        # v2.0.4：最高优先级——任何弹窗清理点先处理「退出确认」（ESC 陷阱），
        # 保证它优先于其他任务/画面被处理（零侵入覆盖全部任务主循环）
        if self._dismiss_quit_dialog(items):
            return True
        if items is not None:
            pos = self.bot.find_text(items, self._BLINK_POPUP_KW, min_conf=0.5)
            if pos:
                self.log(
                    f"[信息] 闪烁弹窗命中「点击空白处」，点击 ({pos[0]},{pos[1]})"
                )
                self.bot.click_at(pos[0], pos[1], '闪烁弹窗_点空白')
                self._sleep(1.0)
                return True
            return False
        for _ in range(max(1, max_ocr)):
            pos = self.bot.find_text(
                self.bot._ocr_screen(), self._BLINK_POPUP_KW, min_conf=0.5
            )
            if pos:
                self.log(
                    f"[信息] 闪烁弹窗命中「点击空白处」，点击 ({pos[0]},{pos[1]})"
                )
                self.bot.click_at(pos[0], pos[1], '闪烁弹窗_点空白')
                self._sleep(1.0)
                return True
            self._sleep(interval)
        return False

    def escape_alternating(
        self, streak: int, label: str = '未知画面', wait: float = 1.5
    ) -> bool:
        """unknown 脱离：ESC 与「返回按钮」模板**交替**尝试（v1.8.24）。

        用户定案（2026-09-06）：「走 unknown→ESC 并不全能，有时需要点击
        返回按钮」——弹窗/特殊二级页 ESC 无响应，必须点左上返回按钮。
        故第 1/3/5… 次尝试 ESC，第 2/4/6… 次改用模板返回键；模板未命中
        时退回 ESC（保证每次尝试都有动作，不会空转）。

        Args:
            streak: 当前连续未知计数（1 起）
            label: 日志/点击标签
            wait: 动作后等待秒数

        Returns:
            bool: True = 已发起脱离动作（两者必居其一）
        """
        if streak % 2 == 1:
            self.bot.press_esc(label=f'{label}_ESC')
            self._sleep(wait)
            return True
        self.log(f"[信息] {label}：ESC 已试，改用返回按钮模板兜底")
        if self.bot.click_back_button(
            BACK_BUTTON_TPLS, threshold=BACK_THRESHOLD
        ):
            self._sleep(wait)
            return True
        self.log("[警告] 返回按钮模板未命中，退回 ESC")
        self.bot.press_esc(label=f'{label}_ESC兜底')
        self._sleep(wait)
        return True

    def report(self, progress: float, detail: str = ""):
        """向 GUI 回报进度。

        Args:
            progress: 0.0 ~ 1.0
            detail: 进度描述文字
        """
        # 默认实现仅写日志；GUI 接入后可覆盖为更新进度条
        if detail:
            self.log(f"进度 {progress:.0%} | {detail}")
        else:
            self.log(f"进度 {progress:.0%}")

    def summary(self) -> str:
        """任务参数摘要，用于 GUI 任务行折叠态展示。

        默认取 options_schema 中各项的当前值，拼成 "值1 / 值2" 形式。
        子类可覆盖以定制摘要文案。
        """
        parts = []
        for key, spec in type(self).options_schema.items():
            val = self.options.get(key, spec.get("default"))
            label = spec.get("label", key)
            # enum 类型把值翻译成显示名
            if spec.get("type") == "enum":
                for choice_val, choice_label in spec.get("choices", []):
                    if choice_val == val:
                        val = choice_label
                        break
            parts.append(f"{label} {val}")
        return " / ".join(parts) if parts else (self.description or "")
