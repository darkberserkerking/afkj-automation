"""
任务注册表
提供任务元数据（类级）存储、任务实例（实例级）管理与持久化。
配置文件保存在 afk_gui.py 同目录 tasks_enabled.json。

v1.5.3 起支持任务多实例：同种任务可多次添加（如 auto_farm#1 / #2），
每个实例独立配置勾选状态与结束指标（max_time / max_turns）。
旧格式 {id: bool} / {enabled, order} 自动迁移为单实例。
"""

import json
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
_CONFIG = os.path.join(_ROOT, "tasks_enabled.json")


class TaskMeta:
    """任务元数据（类级），用于 GUI 渲染与实例化"""

    def __init__(self, task_id: str, label: str, description: str = "",
                 default_enabled: bool = True, icon: str = "📦",
                 group: str = "其他", status: str = "ready",
                 options_schema: dict = None, oneshot: bool = False,
                 nav_target: str = '', show_metrics: bool = False):
        self.id = task_id
        self.label = label
        self.description = description
        self.default_enabled = default_enabled
        self.icon = icon            # emoji 图标（任务行展示）
        self.group = group          # 分组名
        self.status = status        # 'ready' 可调度 | 'planned' 占位（run 未实现）
        # v1.8.15：任务可调参数 schema（GUI 抽屉自动渲染表单；空=无参数）
        self.options_schema = options_schema or {}
        # v1.8.22：单次任务（如女神试炼，追平上限+扫荡后即完成）——
        # GUI 抽屉隐藏 时间/轮次 输入，实例指标强制 None（跑一次自动 done）
        self.oneshot = oneshot
        # v1.8.31 全局导航系统：本任务入口所在目标层（'playbook'/'idle_stage'/
        # 'labyrinth'/'goddess_trial'/'world_home'）——调度器据此在任务间走
        # 最短路径（入口在玩法目录 → 只回到玩法目录，不再一律回挂机关卡）
        self.nav_target = nav_target
        # v1.9.67（用户 17:56）：时间/轮次 输入仅「自动推图」保留——
        # 该功能属于「长跑循环任务的停止条件」；其他任务有内建终止
        # 条件（次数耗尽/单次通关/一次性领取），不再设置此项。
        # 新增任务一律保持默认 False（不得引入该功能）。
        self.show_metrics = show_metrics

    def __repr__(self):
        return f"TaskMeta({self.id!r}, {self.label!r}, {self.status})"


class TaskInstance:
    """任务实例（实例级）：同种任务可创建多个，各自独立配置指标"""

    def __init__(self, inst_id: str, class_id: str, enabled: bool = True,
                 options: dict = None, max_time=None, max_turns=None):
        self.inst_id = inst_id
        self.class_id = class_id
        self.enabled = enabled
        self.options = options or {}
        self.max_time = max_time        # 秒，None=无限
        self.max_turns = max_turns      # 轮，None=无限

    def to_dict(self) -> dict:
        return {
            'inst_id': self.inst_id,
            'class_id': self.class_id,
            'enabled': self.enabled,
            'options': self.options,
            'max_time': self.max_time,
            'max_turns': self.max_turns,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "TaskInstance":
        return cls(
            inst_id=d.get('inst_id'),
            class_id=d.get('class_id'),
            enabled=d.get('enabled', True),
            options=d.get('options') or {},
            max_time=d.get('max_time'),
            max_turns=d.get('max_turns'),
        )

    def __repr__(self):
        return f"TaskInstance({self.inst_id!r}, {self.class_id!r})"


# ── v2.0.2 日志中文化：状态值显示映射（内部契约值不变）──────────
RESULT_ZH: dict = {
    'done': '完成',
    'failed': '失败',
    'stopped': '已停止',
    'skipped': '已跳过',
    'unknown': '未知',
}


def zh_result(result: str) -> str:
    """任务结果 → 中文显示（仅用于日志/UI，内部仍用 done/failed/stopped）。"""
    return RESULT_ZH.get(result, result)


def zh_results(results: dict) -> dict:
    """结果字典 → 中文显示版（调度汇总用）。"""
    return {k: zh_result(v) for k, v in results.items()}

class TaskRegistry:
    """任务注册表：类元数据 + 实例列表"""
    _tasks: list = []
    _instances: list = []
    _order: list = []
    _CONFIG: str = _CONFIG   # 配置文件路径（类属性，允许测试覆写）

    # ── 类注册 ──────────────────────────────────────────────

    @classmethod
    def register(cls, meta: TaskMeta):
        """注册一个任务类元数据"""
        if not isinstance(meta, TaskMeta):
            raise TypeError("必须传入 TaskMeta 实例")
        cls._tasks.append(meta)

    @classmethod
    def get_all(cls) -> list:
        """全部注册任务类"""
        return list(cls._tasks)

    @classmethod
    def get_by_id(cls, task_id: str):
        for t in cls._tasks:
            if t.id == task_id:
                return t
        return None

    @classmethod
    def get_ready_tasks(cls) -> list:
        """可调度任务（status == 'ready'）"""
        return [t for t in cls._tasks if t.status == 'ready']

    # ── 实例管理 ────────────────────────────────────────────

    @classmethod
    def get_instances(cls) -> list:
        """按 _order 返回实例列表。

        v2.1.1：读取前先归一化边界任务顺序（幂等、开销可忽略）——
        保证 GUI 首次打开 / 配置加载后，启动游戏与关闭游戏即在首尾。
        """
        cls.normalize_boundary_order()
        rest = [i for i in cls._instances if i.inst_id not in cls._order]
        ordered = [i for i in cls._instances if i.inst_id in cls._order]
        ordered.sort(key=lambda i: cls._order.index(i.inst_id))
        return ordered + rest

    @classmethod
    def get_enabled_instances(cls) -> list:
        return [i for i in cls.get_instances() if i.enabled]

    @classmethod
    def get_instance(cls, inst_id: str):
        for i in cls._instances:
            if i.inst_id == inst_id:
                return i
        return None

    @classmethod
    def add_instance(cls, class_id: str, enabled: bool = True,
                     options: dict = None, max_time=None,
                     max_turns=None) -> TaskInstance:
        """新增实例，inst_id 自动编号 class#N"""
        n = 1
        while True:
            inst_id = f"{class_id}#{n}"
            if not cls.get_instance(inst_id):
                break
            n += 1
        inst = TaskInstance(inst_id, class_id, enabled, options,
                            max_time, max_turns)
        cls._instances.append(inst)
        cls._order.append(inst_id)
        return inst

    @classmethod
    def remove_instance(cls, inst_id: str) -> bool:
        cls._instances = [i for i in cls._instances if i.inst_id != inst_id]
        if inst_id in cls._order:
            cls._order.remove(inst_id)
        return True

    @classmethod
    def set_instance_enabled(cls, inst_id: str, enabled: bool):
        inst = cls.get_instance(inst_id)
        if inst:
            inst.enabled = enabled

    @classmethod
    def set_instance_metrics(cls, inst_id: str, max_time=None, max_turns=None):
        """设置实例结束指标（None = 无限）"""
        inst = cls.get_instance(inst_id)
        if inst:
            inst.max_time = max_time
            inst.max_turns = max_turns

    @classmethod
    def set_instance_options(cls, inst_id: str, options: dict):
        """v1.8.15：设置实例可调参数（GUI 抽屉表单提交入口）"""
        inst = cls.get_instance(inst_id)
        if inst:
            inst.options = dict(options or {})

    @classmethod
    def normalize_boundary_order(cls) -> None:
        """把边界任务（启动/关闭游戏）归位到 _order 首尾（v2.1.1，幂等）。

        用户 20:45 反馈「其他任务的拖动也受阻」——根因：_order 里
        start_game/close_game 可能停留在中间（历史拖拽 / 旧配置加载顺序），
        普通任务一旦与它们相邻，move_instance 的边界拒绝就会把整次拖动
        挡掉。归一化后边界任务只在首尾，普通任务之间可自由换位。
        """
        _has = any(i.startswith(('start_game', 'close_game'))
                   for i in cls._order)
        if not _has:
            return
        _first = [i for i in cls._order if i.startswith('start_game')]
        _last = [i for i in cls._order if i.startswith('close_game')]
        _mid = [i for i in cls._order
                if not i.startswith(('start_game', 'close_game'))]
        _new = _first + _mid + _last
        if _new != cls._order:
            cls._order = _new

    @classmethod
    def move_instance(cls, inst_id: str, delta: int) -> bool:
        """在 _order 中移动实例（delta=-1 上移 / +1 下移）。

        v2.1.0：**边界任务**（start_game/close_game）位置固定——自身不可
        移动，且其它任务不可跨越它们（GUI 中体现为拖拽受阻）。
        v2.1.1：移动前先 normalize_boundary_order()——避免边界任务残留
        在中间位置，使普通任务被误挡（用户 20:45 反馈）。
        """
        cls.normalize_boundary_order()
        if inst_id not in cls._order:
            return False
        _boundary = ('start_game', 'close_game')
        _cls_of = {i.inst_id: i.class_id for i in cls._instances}

        def _is_boundary(_iid: str) -> bool:
            # v2.1.1：前缀 + 类型双判（实例表可能尚未加载，前缀始终可靠）
            return (_iid.startswith(_boundary)
                    or _cls_of.get(_iid) in _boundary)

        if _is_boundary(inst_id):
            return False   # 边界任务不可移动
        i = cls._order.index(inst_id)
        j = i + delta
        if j < 0 or j >= len(cls._order):
            return False
        if _is_boundary(cls._order[j]):
            return False   # 不可跨越边界任务
        cls._order[i], cls._order[j] = cls._order[j], cls._order[i]
        return True

    # ── 持久化 ──────────────────────────────────────────────

    @classmethod
    def save_config(cls):
        data = {
            "instances": [i.to_dict() for i in cls.get_instances()],
            "order": cls._order,
        }
        with open(cls._CONFIG, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    @classmethod
    def load_config(cls):
        """加载配置；无文件时兜底建默认实例；兼容旧格式自动迁移"""
        if not os.path.exists(cls._CONFIG):
            cls._ensure_default_instances()
            return
        with open(cls._CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "instances" in data \
                and isinstance(data["instances"], list):
            # 新格式 {instances: [...], order: [...]}
            cls._instances = [TaskInstance.from_dict(d)
                              for d in data["instances"]]
            registered = {t.id for t in cls._tasks}
            cls._instances = [i for i in cls._instances
                              if i.class_id in registered]
            cls._order = [oid for oid in data.get("order", [])
                          if cls.get_instance(oid)]
        elif isinstance(data, dict) and "enabled" in data:
            # 旧格式 v1.5.2 {enabled: {id: bool}, order: [...]}
            enabled = data["enabled"]
            order = data.get("order", [])
            ids = order + [k for k in enabled if k not in order]
            for tid in ids:
                if tid in enabled and cls.get_by_id(tid):
                    cls.add_instance(tid, enabled=enabled[tid])
        elif isinstance(data, dict):
            # 旧格式 v1.5.0 {id: bool}
            for tid, en in data.items():
                if cls.get_by_id(tid):
                    cls.add_instance(tid, enabled=en)
        cls._ensure_default_instances()
        # v1.8.22：单次任务强制清除指标（兼容旧配置残留的时间/轮次值——
        # GUI 已不展示该输入，残留值会让任务被无意义的时间上限打断）
        oneshot_ids = {t.id for t in cls._tasks
                       if getattr(t, 'oneshot', False)}
        if oneshot_ids:
            for i in cls._instances:
                if i.class_id in oneshot_ids:
                    i.max_time = None
                    i.max_turns = None

    @classmethod
    def _ensure_default_instances(cls):
        """确保每个 ready 任务至少有一个实例"""
        registered_ids = {i.class_id for i in cls._instances}
        for t in cls._tasks:
            if t.status == 'ready' and t.id not in registered_ids:
                cls.add_instance(t.id, enabled=t.default_enabled)

    @classmethod
    def reset_config(cls):
        cls._instances = []
        cls._order = []
        cls._ensure_default_instances()
