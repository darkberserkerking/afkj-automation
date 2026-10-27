"""
任务调度器

按清单顺序依次执行任务实例（TaskInstance），负责：
  - 任务实例化与上下文注入（bot / logger / 停止信号 / 参数 / 结束指标）
  - 前置检查（preflight）与跳过（planned 任务在此被跳过）
  - 停止 / 暂停信号传递
  - 任务间状态隔离（回到游戏主界面）
  - 结果汇总

设计要点：
    v1.5.3 起按「实例」调度：同种任务可多次添加（auto_farm#1 / #2），
    每个实例独立配置 options / max_time / max_turns。
    停止信号默认桥接到 bot 自身的 _stop_event / _pause，
    这样「GUI 停止按钮 → bot.stop()」与「调度器 → 任务 check_stop()」
    走的是同一个 Event，不会出现「点了停止但任务还在跑」。
"""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from tasks.base import BaseTask
from tasks.catalog import TASK_CLASSES
from task_registry import TaskInstance, TaskRegistry, zh_result, zh_results


class TaskScheduler:
    """任务调度器：按清单顺序执行任务实例。"""

    def __init__(self, bot,
                 logger: Optional[Callable[[str], None]] = None,
                 status_cb: Optional[Callable[..., None]] = None):
        """
        Args:
            bot: AFKBot 实例（任务通过 ctx 拿到它）
            logger: 日志回调 logger(msg)
            status_cb: 状态回调（可选），用于 GUI 更新进度
        """
        self.bot = bot
        self._logger = logger or print
        self._status_cb = status_cb
        self._queue: List[TaskInstance] = []
        self.results: Dict[str, str] = {}

    # ── 构建执行清单 ──────────────────────────────────────

    def add(self, inst: TaskInstance):
        """添加一个任务实例到执行清单。

        Args:
            inst: TaskInstance（含 class_id / enabled / options / 结束指标）
        """
        self._queue.append(inst)

    def clear(self):
        """清空执行清单。"""
        self._queue.clear()
        self.results.clear()

    def log(self, msg: str):
        # v1.8.49：带 [调度] 前缀，多任务串行时可归属（规范 2.7）
        self._logger(f'[调度] {msg}')

    # ── 执行 ─────────────────────────────────────────────

    # ── v2.0.9（用户 20:15）边界任务强制顺序 ────────────────
    _FIRST_CLASS: str = 'start_game'   # 启动游戏：强制第一个
    _LAST_CLASS: str = 'close_game'    # 关闭游戏：强制最后一个

    def _enforce_boundary_order(self) -> None:
        """启动游戏强制排第一、关闭游戏强制排最后（与 GUI 顺序无关）。

        用户定案（20:15）：这两个是流程性任务——启动必须在所有业务
        任务之前、关闭在所有业务任务之后；用户在 GUI 中拖动位置
        不影响实际调度顺序。
        """
        firsts = [i for i in self._queue
                  if i.class_id == self._FIRST_CLASS]
        lasts = [i for i in self._queue
                 if i.class_id == self._LAST_CLASS]
        if not firsts and not lasts:
            return
        middle = [i for i in self._queue
                  if i.class_id not in (self._FIRST_CLASS,
                                        self._LAST_CLASS)]
        ordered = firsts + middle + lasts
        if ordered != self._queue:
            self.log("[信息] 调度顺序已强制：启动游戏第一 / 关闭游戏最后")
            self._queue = ordered

    def run_all(self, stop_event=None, pause_event=None) -> Dict[str, str]:
        """依次执行清单中的任务实例。

        Args:
            stop_event: threading.Event，置位表示中止；
                        默认桥接 bot._stop_event
            pause_event: threading.Event，清空表示暂停；
                        默认桥接 bot._pause

        Returns:
            {inst_id: 'done' | 'failed' | 'stopped' | 'skipped'}
        """
        # 停止信号默认桥接到 bot 自身事件，保证停止贯穿调度器与 bot 内部
        if stop_event is None:
            stop_event = getattr(self.bot, "_stop_event", None)
        self._stop_event = stop_event   # v1.8.36：供任务间全局导航响应停止
        if pause_event is None:
            pause_event = getattr(self.bot, "_pause", None)

        # v2.0.9：边界任务强制顺序（启动游戏第一 / 关闭游戏最后）
        self._enforce_boundary_order()

        results: Dict[str, str] = {}
        total = sum(1 for inst in self._queue if inst.enabled)
        done_count = 0

        for inst in self._queue:
            if not inst.enabled:
                continue

            # 调度器层面的停止检查
            if stop_event is not None and stop_event.is_set():
                self.log("调度中止：收到停止信号")
                break

            cls = TASK_CLASSES.get(inst.class_id)
            if cls is None:
                self.log(f"[跳过] 未知任务类型: {inst.class_id}")
                results[inst.inst_id] = "skipped"
                done_count += 1
                self._notify(done_count, total, inst, "skipped")
                continue

            bot_stats = getattr(self.bot, "stats", None)
            start_turns = bot_stats.get("total_battles", 0) \
                if isinstance(bot_stats, dict) else 0

            ctx = {
                "bot": self.bot,
                "logger": self._logger,
                "stats": {},
                "stop_event": stop_event,
                "pause_event": pause_event,
                "options": inst.options,
                "max_time": inst.max_time,
                "max_turns": inst.max_turns,
                "start_turns": start_turns,
                "inst_id": inst.inst_id,
            }

            try:
                task = cls(ctx)
            except Exception as e:
                self.log(f"[异常] 任务实例化失败 {cls.__name__}: {e}")
                results[inst.inst_id] = "failed"
                continue

            # v2.0.4（用户 18:38）：任务启动前先清「退出确认弹窗」（ESC 陷阱）

            # ——最高优先级，优先于任何任务动作

            try:

                task.dismiss_blink_popup()

            except Exception as _e:
                self.log(f"[调试] run_all() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据

                pass


            # 前置检查
            ok, reason = task.preflight()
            if not ok:
                self.log(f"[跳过] {task.label} ({inst.inst_id}) —— {reason}")
                results[inst.inst_id] = "skipped"
                done_count += 1
                self._notify(done_count, total, inst, "skipped")
                continue

            self.log(f"\n{'=' * 40}\n  任务 {done_count + 1}/{total}："
                     f"{task.label} ({inst.inst_id})\n{'=' * 40}")
            self._notify(done_count, total, inst, "running")

            task.on_start()
            started = time.time()
            try:
                result = task.run()
            except Exception as e:
                # v1.9.36：异常带完整 traceback（此前只打 message，
                # numpy 空索引这类崩点无法定位）
                import traceback
                self.log(f"[异常] {task.label} —— {e}")
                for _ln in traceback.format_exc().splitlines():
                    self.log(f"[异常]   {_ln}")
                result = "failed"
            elapsed = int(time.time() - started)

            task.on_end(result)
            results[inst.inst_id] = result
            done_count += 1
            self._notify(done_count, total, inst, result)

            self.log(f"任务结束：{task.label} ({inst.inst_id}) → {zh_result(result)}"
                     f"（耗时 {elapsed} 秒）")

            if result == "stopped":
                self.log("任务被停止，调度结束")
                break
            if result == "failed" and not task.continue_on_failure:
                self.log(f"任务失败且 continue_on_failure=False，调度中止："
                         f"{task.label}")
                break

            # v1.8.31 全局导航系统：按**下一个任务入口所在层**走最短路径
            # （入口卡片在玩法目录 → 只回玩法目录；挂机推图入口 → 回挂机关卡页）；
            # 最后一个任务结束后不做任何导航
            if done_count < total:
                self._navigate_to_next(inst)

        self.results = results
        self.log(f"\n{'=' * 40}\n  全部任务结束：{zh_results(results)}\n{'=' * 40}")
        return results

    # ── 内部辅助 ──────────────────────────────────────────

    def _to_next_ready(self, nxt, meta) -> str:
        """下一任务当前界面可否就地进入（v2.1.9 三态判定）。

        Returns:
            'ready' → 已在循环内 → 跳过任务间导航，就地续跑
            'near'  → 家门口（差一步）→ 跳过任务间导航，由任务自己「进入」
            ''      → 需常规导航到 nav_target

        判定优先级（用户 22:35 规范）：
          ① **声明优先**（可审计）：识别当前画面 → 查任务类的
             `loop_ready(screen)`：'in'→ready / 'near'→near /
             'strict'→强制从头（返回 ''）
          ② 声明未命中 → 回退任务自身的 `startup_probe()`（保留既有行为）
        任何异常/不支持 → ''（按常规导航，绝不因探测失败卡住）。
        """
        try:
            cls = TASK_CLASSES.get(nxt.class_id)
            if cls is None:
                return ''
            label = getattr(meta, 'label', nxt.class_id) if meta else nxt.class_id

            # ── ① 声明优先（v2.1.9）──────────────────────────────
            _screen = ''
            try:
                from tasks.screen_registry import (GLOBAL_SCREEN_REGISTRY,
                                                   ZH_SCREEN)
                _items = self.bot._ocr_screen()
                _screen = GLOBAL_SCREEN_REGISTRY.detect(_items) or ''
                _zh = ZH_SCREEN.get(_screen, _screen)
            except Exception as _e:
                self.log(f"[调试] _to_next_ready() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
                _screen, _zh = '', ''
            if _screen and hasattr(cls, 'loop_ready'):
                _verdict = cls.loop_ready(_screen)
                if _verdict == 'in':
                    self.log(f"[探测] 当前「{_zh}」在{label}的循环内 → 就地续跑")
                    return 'ready'
                if _verdict == 'near':
                    self.log(f"[探测] 当前「{_zh}」是{label}的家门口 → 就地进入")
                    return 'near'
                if _verdict == 'strict':
                    self.log(f"[探测] {label}要求严格顺序 → 回起始步骤")
                    return ''

            # ── ② 回退：任务自身 probe（保留既有行为）──────────────
            if not hasattr(cls, 'startup_probe'):
                return ''
            ctx = {
                "bot": self.bot,
                "logger": self._logger,
                "stats": {},
                "stop_event": getattr(self, '_stop_event', None),
                "pause_event": None,
                "options": nxt.options,
                "max_time": nxt.max_time,
                "max_turns": nxt.max_turns,
                "start_turns": 0,
                "inst_id": nxt.inst_id,
            }
            probe_task = cls(ctx)
            return 'ready' if probe_task.startup_probe() == 'ready' else ''
        except Exception as e:
            self.log(f"[探测] 直通探测异常（{e}），按常规导航")
            return ''

    def _navigate_to_next(self, cur_inst):
        """v1.8.31：任务间切换 → 全局导航到下一个任务的入口层（最短路径）。

        下一个任务入口在玩法目录（异界迷宫/女神试炼卡片）→ 只回到玩法目录，
        不再一律退回挂机关卡页（用户定案，省 10~20s）。
        v1.8.43：recover_to_idle 兜底已彻底移除——导航失败交由下一任务
        启动导航自行收敛（全部任务已接入全局导航系统）。
        """
        nxt = self._next_instance(cur_inst)
        if nxt is None:
            return                     # 已是最后一个任务 → 不做导航
        meta = TaskRegistry.get_by_id(nxt.class_id)
        target = getattr(meta, 'nav_target', '') if meta else ''

        # v2.1.6（用户 21:43）：**先问下一任务自己**——当前界面是否已可直通
        # （startup_probe == 'ready'）。迷宫等任务支持「内部界面直通续跑」，
        # 若已在执行域内就不该把它拉回入口层：旧逻辑会把画面从「迷宫选门
        # 地图（3 层）」退回「玩法目录（1 层）」，下一任务再点卡片回到
        # 迷宫内部——白绕一圈（真机 21:41 实测 20s）。
        # v2.1.9（用户 22:35）：改三态 —— 'ready'（循环内）与 'near'
        # （家门口，如迷宫主页）**都跳过任务间导航**：前者就地续跑，
        # 后者由任务自己点「进入」（省掉"退回入口层再点卡片"的往返，
        # 真机 22:29 实测 19s）。
        _verdict = self._to_next_ready(nxt, meta)
        if _verdict in ('ready', 'near'):
            label = getattr(meta, 'label', nxt.class_id) if meta else nxt.class_id
            _why = "已在可执行界面" if _verdict == 'ready' else "已在家门口"
            self.log(f"[信息] 下一任务（{label}）{_why}，跳过任务间导航")
            return

        if not target:
            # v2.1.3（用户 21:00）：下一任务 nav_target **显式为空 = 不需要
            # 前置导航**——如「关闭游戏」（直接关窗口）与主菜单类任务
            # （每日任务/收赠友情点/一键家园，自带入口导航能力）。
            # 旧逻辑兜底成 'playbook' 会做一次无谓导航（真机 20:55 实测
            # 关闭游戏前多花 25s 绕路）。
            self.log("[信息] 下一任务无需前置导航，直接执行")
            return
        try:
            from tasks.global_nav import GlobalNavigator
            nav = GlobalNavigator(
                self.bot,
                logger=self._logger,
                # v1.8.36：bot._stop 是 bool（无 is_set）→ 曾炸
                # 'bool' object has no attribute 'is_set'；改用调度器 Event
                stop_check=(lambda: getattr(self, '_stop_event', None)
                            is not None and self._stop_event.is_set()),
            )
            if nav.navigate_to(target):
                return
            # v1.8.43：recover_to_idle 已移除——下一任务启动导航可自行收敛
            self.log("[警告] 全局导航未达目标，交由下一任务启动导航自行收敛")
        except Exception as e:
            self.log(f"[警告] 全局导航异常（{e}），交由下一任务启动导航收敛")

    def _next_instance(self, cur_inst):
        """清单中当前实例之后的下一个启用实例（None = 已是最后一个）。"""
        try:
            idx = self._queue.index(cur_inst)
        except ValueError as _e:
            self.log(f"[调试] _next_instance() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            return None
        for inst in self._queue[idx + 1:]:
            if inst.enabled:
                return inst
        return None

    def _notify(self, done: int, total: int, inst: TaskInstance, state: str):
        """通知 GUI 更新进度（可选）。"""
        if self._status_cb is None:
            return
        try:
            meta = TASK_CLASSES.get(inst.class_id)
            label = getattr(meta, "label", inst.class_id) if meta \
                else inst.class_id
            self._status_cb(
                task_label=label,
                task_index=done,
                task_total=total,
                task_state=state,
                inst_id=inst.inst_id,
            )
        except Exception as _e:
            self.log(f"[调试] _notify() 异常已忽略: {_e}")   # v2.1.27 P2-7：留证据
            # 状态回调失败不应中断任务执行
            pass
