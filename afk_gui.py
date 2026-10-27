# ═══════════════════════════════════════════════════════════════════
#  全局崩溃防护（必须放在文件最顶部）
# ═══════════════════════════════════════════════════════════════════
import sys
import os
import traceback
import datetime
import collections

# 1. 确定项目根目录（打包 exe = exe 所在目录，配置随 exe 持久化；源码运行 = 项目路径）
if getattr(sys, 'frozen', False):
    PROJECT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    PROJECT_DIR = r"C:\Users\x9502\WorkBuddy\20260320211537"
    if not os.path.exists(PROJECT_DIR):
        PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

# 1b. GUI 自身设置持久化文件（v1.8.16）
GUI_CONFIG = os.path.join(PROJECT_DIR, 'gui_config.json')

# 2. 崩溃日志 / stderr 落盘 / 崩溃现场快照
#    v2.1.27（P0-2）：**stderr 与崩溃日志拆成两个文件**——此前 sys.stderr
#    整体重定向进 CRASH_REPORT.log，实测 5,865 行里真正的崩溃只有 34 次
#    （98% 是 "Exception in thread Thread-N" 之类普通线程噪声），
#    导致该文件作为"排障入口"已失效。
CRASH_LOG = os.path.join(PROJECT_DIR, "CRASH_REPORT.log")
STDERR_LOG = os.path.join(PROJECT_DIR, "stderr.log")

# 崩溃现场快照：环形缓冲保留最近 N 行运行日志（由 _append_log 写入），
# 崩溃时随堆栈一起落盘——一份崩溃报告自带现场，不用再翻整份日志
_RECENT_LOG_MAX: int = 20
_recent_logs: 'collections.deque' = collections.deque(maxlen=_RECENT_LOG_MAX)

# 3. 重定向标准错误输出（捕获Python底层错误）→ 独立文件，不污染崩溃报告
try:
    sys.stderr = open(STDERR_LOG, 'a', encoding='utf-8')
except Exception:
    pass

# 4. 全局异常钩子
def global_crash_handler(exctype, value, tb):
    """捕获所有未处理的异常，写入日志并弹窗（v2.1.27：附带版本+现场快照）"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        from afk_bot.config import VERSION as _ver
    except Exception:
        _ver = '未知'
    _snap = ''.join(f'  | {m}\n' for m in list(_recent_logs)) or '  | （无）\n'

    # 格式化错误信息
    log_content = f"""
{'='*60}
[!] 程序崩溃时间: {timestamp}
[!] 版本: {_ver}
{'='*60}
错误类型: {exctype.__name__}
错误信息: {value}

--- 崩溃前最后 {len(_recent_logs)} 行运行日志 ---
{_snap}
堆栈追踪 (Traceback):
{''.join(traceback.format_exception(exctype, value, tb))}
{'='*60}\n\n"""

    # 写入日志文件
    try:
        with open(CRASH_LOG, 'a', encoding='utf-8') as f:
            f.write(log_content)
    except Exception:
        pass

    # 不再丢弃：同时尝试写一份到 stderr 文件（非交互环境下唯一可见处）
    try:
        if sys.stderr is not None:
            sys.stderr.write(log_content)
    except Exception:
        pass

    # Windows弹窗提示（如果可用）
    try:
        import ctypes
        msg = (f"程序发生未处理异常！\n\n错误: {value}\n\n"
               f"崩溃详情（含现场快照）已写入:\n{CRASH_LOG}")
        ctypes.windll.user32.MessageBoxW(0, msg, "AFK 脚本崩溃", 0x10 | 0x1)
    except Exception:
        pass

# 5. 安装钩子
sys.excepthook = global_crash_handler

# ── 日志文件滚动（v2.1.27 P2-8）─────────────────────────────────
_LOG_MAX_BYTES: int = 5 * 1024 * 1024      # 单文件上限 5MB
_LOG_BACKUPS: int = 3                      # 保留 3 个历史文件


def _rotate_log_file(path: str) -> None:
    """日志超过上限则滚动：log.txt → .1 → .2 → .3（最旧丢弃）。

    用 os.replace（同目录内原子重命名）；任何异常一律放行 ——
    日志滚动失败**绝不能影响程序启动**（与崩溃防护同级的可用性要求）。
    """
    try:
        if not os.path.exists(path) or os.path.getsize(path) <= _LOG_MAX_BYTES:
            return
        for i in range(_LOG_BACKUPS - 1, 0, -1):
            src: str = f'{path}.{i}'
            if os.path.exists(src):
                os.replace(src, f'{path}.{i + 1}')
        os.replace(path, f'{path}.1')
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════
#  原有代码从这里开始（import 其他库）
# ═══════════════════════════════════════════════════════════════════

import os
import sys
import json
import time
import math
import threading
import queue
import ctypes
import customtkinter as ctk
import tkinter as tk

from afk_bot import VERSION, AFKBot
from afk_bot.infra.ocr_memory import OCRMemory
from task_registry import TaskRegistry, zh_results
from tasks.scheduler import TaskScheduler
from tasks.catalog import TASK_CLASSES   # import 即注册全部任务类

# 打包 exe 下 task_registry.__file__ 在临时解压目录 → 配置重定向到 exe 目录（v1.8.16）
if getattr(sys, 'frozen', False):
    import task_registry as _tr
    _tr._CONFIG = os.path.join(PROJECT_DIR, 'tasks_enabled.json')
    TaskRegistry._CONFIG = _tr._CONFIG

# ═════════════════════════════════════════════════════════════════
#  GUI 界面 — MaaEnd 风格极简布局
# ═════════════════════════════════════════════════════════════════
# ═══════════════════════════════════════════════════════════════════
#  GUI 界面 — MaaEnd 风格极简布局
# ═══════════════════════════════════════════════════════════════════

# 配色 — 浅色（MaaEnd 风格）
BG = '#f5f7fa'          # 主背景 浅灰蓝
BG_HEADER = '#ffffff'   # 顶栏 白
BG_CARD = '#ffffff'     # 卡片 白
BG_DRAWER = '#ffffff'   # 抽屉 白
BG_INPUT = '#e2e8f0'     # 输入/边框 slate-200
BG_BTN = '#eef1f5'      # 按钮 浅灰
BG_BTN_HOVER = '#dfe4ea' # 按钮悬停 中灰
BG_LOG = '#f8fafc'      # 日志背景 更浅
ACCENT = '#3b82f6'       # 主色 蓝-500
ACCENT_DIM = '#2563eb'   # 主色暗 蓝-600
TEXT = '#475569'         # 正文 slate-600
TEXT_DIM = '#94a3b8'     # 次要 slate-400
TEXT_BRIGHT = '#0f172a'  # 高亮 slate-900（深色文字）
SUCCESS = '#10b981'      # 绿 emerald-500
ORANGE = '#f59e0b'       # 橙 amber-500
PURPLE = '#8b5cf6'       # 紫 violet-500
DANGER = '#ef4444'       # 红 red-500
WARN = '#f59e0b'         # 黄 amber-500

# 间距系统（4px 基准，参考 MaaEnd）
SPACE_XS = 4   # 极小间距
SPACE_SM = 8   # 小间距
SPACE_MD = 12   # 中间距
SPACE_LG = 16   # 大间距
SPACE_XL = 20   # 极大间距
SPACE_XXL = 24  # 超大间距


def _sanitize_traceback(tb: str) -> str:
    """v1.8.49：堆栈脱敏——去掉本机绝对路径，只保留 文件:行号 + 函数名（规范 2.5）。"""
    import os
    import re as _re
    lines = [ln.strip() for ln in tb.strip().splitlines() if ln.strip()]
    out = []
    for ln in lines[-3:]:
        # File "C://Users//...//x.py", line 12, in foo
        m = _re.search(r'File "(.+)", line (\d+), in (.+)', ln)
        if m:
            out.append(f'{os.path.basename(m.group(1))}:{m.group(2)} '
                       f'({m.group(3)})')
        else:
            out.append(ln)
    return ' | '.join(out)


class App(ctk.CTk):
    f"""MaaEnd 风格极简界面 {VERSION}"""

    def __init__(self):
        super().__init__()

        # 窗口
        self.title('AFKJ自动化工具')
        self.geometry('1024x800')
        self.minsize(800, 600)
        self.configure(fg_color=BG)
        # 窗口置顶：初始不置顶，等配置加载后再决定
        self._topmost = False
        self.attributes('-topmost', self._topmost)
        self.bind('<Escape>', lambda e: self._close_all_drawers())
        self.bind('<F11>', lambda e: self._on_start())
        # v2.2.0：窗口内有焦点时的 F12（真正的停止热键见 _bind_global_hotkeys）
        self.bind('<F12>', lambda e: self._on_hotkey_stop())

        # 文件日志（追加模式，UTF-8）
        _log_dir = PROJECT_DIR
        _log_path = os.path.join(_log_dir, '_afk_gui_log.txt')
        # v2.1.27（P2-8）：打开前先按大小滚动——此前永久追加、无上限
        # （实测 7 天 2.8MB；长期使用会无限增长）
        _rotate_log_file(_log_path)
        # 保留逐行 flush：实测日志速率仅约 3 条/分钟，line-buffered 的
        # 额外开销可忽略；而改成块缓冲会在崩溃时丢掉最后几 KB ——
        # 恰是关键现场。故**不改缓冲策略**。
        self._log_file = open(_log_path, 'a', encoding='utf-8', buffering=1)

        # 字体
        self._f_title = ('Microsoft YaHei UI', 16, 'bold')
        self._f_sub = ('Microsoft YaHei UI', 14)
        self._f_small = ('Microsoft YaHei UI', 12)
        self._f_mono = ('Consolas', 12)
        self._f_stats = ('Consolas', 18, 'bold')
        self._f_stats_label = ('Microsoft YaHei UI', 10)

        # 状态
        self._bot = None
        self._bot_thread = None
        self._log_queue = queue.Queue()
        # v2.1.17：后台线程 → 主线程 UI 任务通道（禁止跨线程调 after/控件）
        self._ui_queue = queue.Queue()
        self._closing = False   # 窗口关闭中 → 丢弃新投递的 UI 任务
        self._running = False
        self._settings_open = False
        self._bot_ready = False  # bot 是否已完成初始化（待机）

        self._status = {
            'round': 0, 'mode': '', 'phase': '', 'battle_time': 0,
            'stats': {
                'huanling_wins': 0, 'normal_wins': 0,
                'huanling_times': [], 'normal_times': [],
                'total_battles': 0, 'recovery_count': 0,
                'stage_progress': 0,      # 总推进关卡数
                '本轮进度': 0,       # 本轮推进关卡数
                'current_stage': 0,        # 当前关卡
                'huanling_stage': 0,       # 赛季幻灵关卡
                'normal_stage': 0,         # 赛季挂机关卡
            }
        }

        self._cfg = {
            'log_callback': None,
            'status_callback': None,
            # P1 重构：提取的硬编码常量为配置项
            'max_formation_index': 10,
            'navigate_timeout': 40,
            'battle_timeout': 90,
            'ocr_cache_ttl': 60,
        }

        # 任务清单状态（v1.5.1）
        self._task_vars = {}   # inst_id -> tk.BooleanVar
        self._task_rows = {}   # inst_id -> 行组件 dict
        TaskRegistry.load_config()   # 启动时读 tasks_enabled.json

        # 运行实例状态（v1.5.3：任务行实时 HH:MM:SS || XX轮）
        self._running_inst_id = None
        self._running_start_time = 0.0
        # v1.8.88：轮次统计 _run_start_turns 已随轮次概念移除

        # 抽屉状态（v1.5.4：行内指标抽屉 + 底部添加抽屉，零弹窗）
        self._drawers = {}      # inst_id -> 任务行抽屉 frame
        self._add_drawer = None  # 底部「添加任务」抽屉

        # 检查管理员权限（在_build_ui之前，此时日志系统已初始化）
        if not _check_admin():
            self._append_log('[警告] 未以管理员权限运行，游戏控制可能失效！建议：右键此脚本 → 以管理员身份运行')

        self._build_ui()
        self._load_gui_settings()   # v1.8.16：恢复透明度/置顶/窗口几何
        self._schedule_startup_update_check()   # v2.2.20：启动后静默查更新

        self._flush_id = None
        self._timer_id = None
        self._start_flush_timer()
        self._refresh_timer()  # 立即显示时钟，不等启动

        # ── 后台初始化 Bot（MaaOCR ~5s，不阻塞 GUI） ──
        self._append_log('[信息] 初始化 OCR 引擎...')
        threading.Thread(target=self._init_bot_background, daemon=True).start()

        # v2.2.0：注册系统级停止快捷键（F12）。
        # ⚠️ 必须放在 __init__ **末尾** —— `_bind_global_hotkeys` 会写日志，
        # 而 `_log_queue`(237) / UI / flush 定时器都在本行之前才就绪；
        # 早放会 AttributeError 启动即崩（测试构造真实 App 时抓到过）。
        self._bind_global_hotkeys()

    def _init_bot_background(self):
        """后台初始化 AFKBot（MaaOCR ~5s，不阻塞 GUI）。初始化完即待机。"""
        try:
            from afk_bot.facade import AFKBot

            # 初始配置（必须注入 log/status callback）
            cfg = dict(self._cfg)
            cfg['log_callback'] = self._append_log
            cfg['status_callback'] = self._update_status
            self._bot = AFKBot(cfg)
            self._bot_ready = True
            # v2.1.17：UI 操作投递主线程（后台线程调 after 会抛
            # RuntimeError: main thread is not in main loop）；
            # 日志本身线程安全（走 _log_queue）→ 直接写
            self._post_ui(lambda: self._btn_main.configure(
                text='▶ 运行', fg_color=DANGER,
                hover_color='#f87171', text_color='white',
                state='normal',
                command=self._on_start,
            ))
            self._append_log('[信息] OCR 引擎就绪，等待启动')
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            _err = str(e)   # v1.8.78：except 块结束后 Python 会 del e，
                            # 延迟执行的 lambda 再引用会 NameError
            self._bot_ready = False
            # v2.1.17：直接写日志（线程安全）。旧实现在 except 里**又调 after**，
            # 二次抛错直接炸掉线程（真机 2026-09-11 13:08 崩溃根因）
            self._append_log(f'[错误] Bot 初始化失败: {_err}')
            self._append_log('[错误] 堆栈：' + _sanitize_traceback(tb))

    # ────────────────────────────────────────────────────────────────
    #  UI 构建

    def _schedule_startup_update_check(self) -> None:
        """启动后静默检查更新（3 秒后，失败不打扰用户）。"""
        try:
            self.after(3000, lambda: self._on_check_update(silent=True))
        except Exception:
            pass

    def _build_ui(self):
        self._build_header()

        # 主体：左任务清单（≥50%，分隔条可调比例）/ 右日志
        self._main_row = ctk.CTkFrame(self, fg_color='transparent')
        self._main_row.pack(fill='both', expand=True,
                            padx=SPACE_LG, pady=(SPACE_SM, 0))

        self._left_col = ctk.CTkFrame(self._main_row, fg_color='transparent',
                                     width=560, border_width=0, corner_radius=0)
        self._left_col.pack(side='left', fill='both')
        self._left_col.pack_propagate(False)

        self._build_splitter()   # 可拖分隔条（调左右比例）

        self._right_col = ctk.CTkFrame(self._main_row, fg_color='transparent')
        self._right_col.pack(side='right', fill='both', expand=True,
                             padx=(SPACE_MD, 0))

        # 左侧：任务清单
        self._build_task_list()

        # 右侧：运行日志（STATS 已删，信息由日志承载）
        self._build_log_area(self._right_col)

        self._build_add_drawer()   # 底部「添加任务」抽屉（v1.5.4，隐藏待展开）
        self._build_control_bar()
        self._build_settings_drawer()

    def _build_splitter(self):
        """左右栏可拖分隔条（4px 宽，拖拽调整比例）"""
        self._splitter = ctk.CTkFrame(self._main_row, fg_color=BG_BTN_HOVER,
                                      width=4, corner_radius=2)
        self._splitter.pack(side='left', fill='y')
        self._splitter_dragging = False
        self._splitter_x = 0
        self._splitter.bind('<ButtonPress-1>', self._on_splitter_press)
        self._splitter.bind('<B1-Motion>', self._on_splitter_drag)
        self._splitter.bind('<ButtonRelease-1>', self._on_splitter_release)

    def _on_splitter_press(self, event):
        self._splitter_dragging = True
        self._splitter_x = event.x_root
        # 冻结任务行（_task_scroll 内）和日志卡（_log_container 内）——
        # 只 forget 内容 widget，保留滚动区域/容器本身的占位与轮廓
        self._task_frozen = [
            (w, w.pack_info()) for w in self._task_scroll.pack_slaves()
        ]
        for w, _ in self._task_frozen:
            w.pack_forget()
        if hasattr(self, '_log_container'):
            self._log_frozen = [
                (w, w.pack_info()) for w in self._log_container.pack_slaves()
            ]
            for w, _ in self._log_frozen:
                w.pack_forget()
        # 中央实时比例预览
        self._ratio_label = ctk.CTkLabel(
            self._main_row, text='50% / 50%',
            font=('Microsoft YaHei UI', 16, 'bold'),
            fg_color=BG_CARD, corner_radius=10,
            text_color=TEXT_BRIGHT, padx=SPACE_LG, pady=SPACE_MD,
        )
        self._ratio_label.place(relx=0.5, rely=0.5, anchor='center')

    def _on_splitter_drag(self, event):
        if not self._splitter_dragging:
            return
        dx = event.x_root - self._splitter_x
        # 节流：不足 5px 不处理，避免每像素触发 layout pass 产生拖影
        if abs(dx) < 5:
            return
        total = self._main_row.winfo_width()
        if total <= 0:
            return
        new_w = self._left_col.winfo_width() + dx
        min_w = int(total * 0.4)   # 左栏下限 40%
        max_w = int(total * 0.7)   # 上限 70%
        new_w = max(min_w, min(max_w, new_w))
        # 右栏最小宽度保护（日志至少 240px，分隔条 4px + 右栏 padx 12px）
        new_w = min(new_w, total - 240 - 4 - SPACE_MD)
        if new_w != self._left_col.winfo_width():
            self._left_col.configure(width=new_w)
            self._splitter_x = event.x_root
            # 更新比例预览
            if hasattr(self, '_ratio_label'):
                total = self._main_row.winfo_width()
                if total > 0:
                    pct = round(new_w / total * 100)
                    self._ratio_label.configure(text=f'左 {pct}%   右 {100 - pct}%')

    def _on_splitter_release(self, event):
        self._splitter_dragging = False
        # 移除预览 Label
        if hasattr(self, '_ratio_label'):
            try:
                self._ratio_label.destroy()
            except Exception:
                pass
            del self._ratio_label
        # 恢复任务行 + 日志（按原 pack options）
        for w, info in getattr(self, '_task_frozen', []) or []:
            try:
                w.pack(**info)
            except Exception:
                pass
        for w, info in getattr(self, '_log_frozen', []) or []:
            try:
                w.pack(**info)
            except Exception:
                pass
        self._task_frozen = None
        self._log_frozen = None
        # 强制一次干净重绘
        self.update_idletasks()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG_HEADER, height=56, corner_radius=0)
        hdr.pack(fill='x', padx=0, pady=0)
        hdr.pack_propagate(False)

        left = ctk.CTkFrame(hdr, fg_color='transparent')
        left.pack(side='left', padx=SPACE_LG, pady=0, fill='both', expand=True)

        # Logo 图标（圆角方块背景）
        icon_box = ctk.CTkFrame(left, fg_color=ACCENT, width=36, height=36, corner_radius=10)
        icon_box.pack(side='left', pady=SPACE_MD, padx=(0, SPACE_MD))
        icon_box.pack_propagate(False)
        ctk.CTkLabel(icon_box, text='⚔', font=('Segoe UI Symbol', 16),
                     text_color='white', anchor='center').pack(expand=True)

        ctk.CTkLabel(left, text='AFK Journey',
                     font=self._f_title, text_color=TEXT_BRIGHT,
                     anchor='w').pack(side='left', pady=SPACE_MD)

        # 版本号 pill
        ctk.CTkLabel(left, text=VERSION,
                     font=('Microsoft YaHei UI', 12), text_color=TEXT_DIM,
                     fg_color=BG_INPUT, corner_radius=10,
                     width=52, height=24).pack(side='left', padx=SPACE_MD, pady=SPACE_MD)

        # ── 头部状态栏已删（v1.8.80，用户要求：状态点/状态文字/计时器
        #    全部移除）——运行状态只看日志区与任务行指标 ──

        # 右侧组：时钟 + 设置（⚙ 最右）
        right = ctk.CTkFrame(hdr, fg_color='transparent')
        right.pack(side='right', padx=(0, SPACE_MD), pady=0)
        self._lbl_clock = ctk.CTkLabel(
            right, text='', font=('Microsoft YaHei UI', 12),
            text_color=TEXT_DIM, anchor='e'
        )
        self._lbl_clock.pack(side='left', padx=(0, SPACE_MD))

        self._btn_gear = ctk.CTkButton(
            right, text='⚙', font=('Segoe UI Symbol', 16),
            fg_color='transparent', hover_color=BG_BTN,
            text_color=TEXT_DIM, width=36, height=40,
            corner_radius=10,
            command=self._toggle_settings
        )
        self._btn_gear.pack(side='left')

    def _build_control_bar(self):
        bar = ctk.CTkFrame(self, fg_color='transparent')
        bar.pack(fill='x', padx=SPACE_LG, pady=(SPACE_SM, SPACE_SM))
        self._control_bar = bar   # 供底部添加抽屉 before 定位

        # 左：批量操作
        left = ctk.CTkFrame(bar, fg_color='transparent')
        left.pack(side='left')
        self._btn_select_none = ctk.CTkButton(
            left, text='☐ 全选', font=('Microsoft YaHei UI', 13),
            fg_color='transparent', hover_color=BG_BTN, text_color=TEXT,
            width=88, height=34, corner_radius=8, command=self._on_select_toggle
        )
        self._btn_select_none.pack(side='left', padx=(0, SPACE_XS))
        self._btn_expand_all = ctk.CTkButton(
            left, text='⇅ 全部展开', font=('Microsoft YaHei UI', 13),
            fg_color='transparent', hover_color=BG_BTN, text_color=TEXT,
            width=88, height=34, corner_radius=8, command=self._on_expand_toggle
        )
        self._btn_expand_all.pack(side='left', padx=(0, SPACE_XS))
        self._btn_add_task = ctk.CTkButton(
            left, text='+ 加任务', font=('Microsoft YaHei UI', 13),
            fg_color='transparent', hover_color=BG_BTN, text_color=TEXT,
            width=92, height=34, corner_radius=8, command=self._on_add_task
        )
        self._btn_add_task.pack(side='left')

        # 右：定时 + 开始任务（大红色，MaaEnd 风格）
        right = ctk.CTkFrame(bar, fg_color='transparent')
        right.pack(side='right')
        self._btn_schedule = ctk.CTkButton(
            right, text='⏱ 定时', font=('Microsoft YaHei UI', 13),
            fg_color='transparent', hover_color=BG_BTN, text_color=TEXT,
            width=72, height=34, corner_radius=8, command=self._on_schedule
        )
        self._btn_schedule.pack(side='left', padx=(0, SPACE_SM))

        self._btn_main = ctk.CTkButton(
            right, text='▶ 运行', font=('Microsoft YaHei UI', 14, 'bold'),
            fg_color=DANGER, hover_color='#f87171', text_color='white',
            height=40, width=150, corner_radius=10,
            state='disabled',
        )
        self._btn_main.pack(side='left')

    # ── 底部批量操作 ───────────────────────────────────────

    def _on_select_toggle(self):
        """勾选/取消全选 toggle（按当前状态智能切换）"""
        if self._running:
            self._append_log('[信息] 运行中不可修改任务勾选')
            return
        instances = TaskRegistry.get_instances()
        if not instances:
            return
        all_on = all(i.enabled for i in instances)
        for inst in instances:
            TaskRegistry.set_instance_enabled(inst.inst_id, not all_on)
            var = self._task_vars.get(inst.inst_id)
            if var:
                var.set(not all_on)
        TaskRegistry.save_config()
        self._update_empty_hint()
        self._refresh_batch_btns()
        self._append_log(
            f'[信息] 已{"取消勾选" if all_on else "勾选"}全部任务')

    def _on_expand_toggle(self):
        """全部展开/全部折叠 toggle"""
        if self._running:
            return
        if self._any_drawer_open():
            self._close_task_drawers()
            self._append_log('[信息] 已折叠全部抽屉')
        else:
            self._open_all_task_drawers()
            self._append_log('[信息] 已展开全部抽屉')
        self._refresh_batch_btns()

    def _any_drawer_open(self) -> bool:
        return any(d.winfo_manager() == 'pack' for d in self._drawers.values())

    def _open_all_task_drawers(self):
        """同时打开所有任务行的行内抽屉（用于「全部展开」）"""
        for inst in TaskRegistry.get_instances():
            iid = inst.inst_id
            drawer = self._drawers.get(iid)
            row = self._task_rows.get(iid, {})
            if drawer and drawer.winfo_manager() != 'pack':
                frame = row.get('frame')
                if frame:
                    drawer.pack(fill='x', pady=(0, SPACE_SM), after=frame)
                else:
                    drawer.pack(fill='x', pady=(0, SPACE_SM))
                btn = row.get('btn_drawer')
                if btn:
                    btn.configure(text='▾')
        # 互斥：收起底部添加/设置抽屉
        self._close_add_drawer()
        self._close_settings_drawer()

    def _refresh_batch_btns(self):
        """刷新底部两个批量按钮的文字/图标（按当前勾选与抽屉状态）"""
        if not hasattr(self, '_btn_select_none'):
            return
        instances = TaskRegistry.get_instances()
        n_inst = len(instances)
        n_en = sum(1 for i in instances if i.enabled)
        if n_inst > 0 and n_en >= n_inst:
            self._btn_select_none.configure(text='☑ 取消全选')
        else:
            self._btn_select_none.configure(text='☐ 全选')
        if self._any_drawer_open():
            self._btn_expand_all.configure(text='⇊ 全部折叠')
        else:
            self._btn_expand_all.configure(text='⇅ 全部展开')

    def _on_add_task(self):
        """底部「+ 加任务」→ 展开底部抽屉（v1.5.4）"""
        self._toggle_add_drawer()

    def _on_schedule(self):
        """定时启动（扩展位，一期未实现）"""
        self._append_log('[信息] 定时功能开发中')

    def _build_log_area(self, parent=None):
        """构建日志区域（v1.5.1：右列堆叠）"""
        parent = parent if parent is not None else self
        # ── 日志头部（始终可见）──
        log_hdr = ctk.CTkFrame(parent, fg_color='transparent')
        log_hdr.pack(fill='x', pady=(SPACE_XS, 0))

        # 清空按钮（左对齐）
        self._btn_clear_log = ctk.CTkButton(
            log_hdr, text='清空', font=('Microsoft YaHei UI', 12),
            fg_color='transparent', hover_color=BG_BTN,
            text_color='#475569', width=40, height=22, corner_radius=6,
            command=self._clear_log
        )
        self._btn_clear_log.pack(side='left')

        ctk.CTkLabel(
            log_hdr, text='LOG', font=('Consolas', 12, 'bold'),
            text_color='#475569', anchor='w'
        ).pack(side='left', padx=(SPACE_XS, 0))

        # ── 容器：日志 ──
        self._log_container = ctk.CTkFrame(parent, fg_color='transparent')
        self._log_container.pack(fill='both', expand=True, pady=(0, SPACE_SM))

        # LOG 显示区域（默认填满容器）
        self._log_card = ctk.CTkFrame(self._log_container, fg_color=BG_CARD, corner_radius=10)

        self._log_text = ctk.CTkTextbox(
            self._log_card,
            font=self._f_mono,
            fg_color=BG_CARD,
            text_color='#475569',
            border_width=0,
            scrollbar_button_color=BG_BTN,
            scrollbar_button_hover_color=BG_BTN_HOVER,
            wrap='word'
        )
        self._log_text.pack(fill='both', expand=True, padx=SPACE_XS, pady=SPACE_XS)
        self._log_text.configure(state='disabled')

        # tag 配置（浅色适配）
        self._log_text.tag_config('ts', foreground='#94a3b8')
        self._log_text.tag_config('ok', foreground='#334155')
        self._log_text.tag_config('warn', foreground='#b45309')
        self._log_text.tag_config('err', foreground='#dc2626')
        self._log_text.tag_config('sep', foreground='#94a3b8')
        self._log_text.tag_config('progress', foreground='#d97706')    # 推进 / >>
        self._log_text.tag_config('season', foreground=ACCENT)      # 赛季
        self._log_text.tag_config('phase', foreground='#334155')  # 阶段文字
        self._log_text.tag_config('config', foreground='#2563eb')  # 配置
        self._log_text.tag_config('data', foreground='#7c3aed')    # 数据
        # v1.8.26 统一日志规范新增标志色（级别表见 tasks/base.py LEVEL_PREFIXES）
        self._log_text.tag_config('success', foreground='#16a34a')  # [成功] 绿
        self._log_text.tag_config('click', foreground='#0891b2')    # [点击] 青
        # 次要/过程类 - 浅灰
        self._log_text.tag_config('dim_info', foreground='#9ca3af')
        # 数据/结果类 - 中灰
        self._log_text.tag_config('data_info', foreground='#6b7280')

        # 默认：LOG 填满容器
        self._show_log_full()

    def _show_log_full(self):
        """LOG 占满容器（聊天隐藏时）"""
        self._log_card.pack(fill='both', expand=True)

    # ────────────────────────────────────────────────────────────────
    #  任务清单（v1.5.3 实例化：同种任务可多实例，各自独立指标）

    def _build_task_list(self):
        """左侧任务清单：可滚动任务实例行（挂到 _left_col；v1.6.0 删 TASKS 标题）"""
        col = self._left_col

        self._task_scroll = ctk.CTkScrollableFrame(
            col, fg_color='transparent', border_width=0, corner_radius=0,
            scrollbar_button_color=BG_BTN,
            scrollbar_button_hover_color=BG_BTN_HOVER
        )
        self._task_scroll.pack(fill='both', expand=True)
        self._task_scroll._scrollbar.configure(width=4)

        self._task_rows = {}
        for inst in TaskRegistry.get_instances():
            self._add_task_row(inst)

        # 空态提示：无启用任务时显示
        self._lbl_empty_hint = ctk.CTkLabel(
            self._task_scroll, text='未勾选任务 —— 勾选后点击开始',
            font=('Microsoft YaHei UI', 12), text_color=TEXT_DIM
        )
        self._update_empty_hint()

    def _add_task_row(self, inst):
        """渲染一行任务实例（44px）：手柄+勾选+图标+名称+摘要+pill+›+X"""
        meta = TaskRegistry.get_by_id(inst.class_id)
        if meta is None:
            return
        var = tk.BooleanVar(value=inst.enabled)
        self._task_vars[inst.inst_id] = var

        # 行高由内容自适应（固定 44px 会把摘要行压缩到 8px 导致文字压边框）
        row = ctk.CTkFrame(self._task_scroll, fg_color=BG_CARD, corner_radius=8,
                           border_width=1, border_color=BG_INPUT)
        row.pack(fill='x', pady=(0, SPACE_SM))

        # 拖拽手柄（显式小高度，避免默认 28px 撑高）
        # v2.1.0：边界任务（启动/关闭游戏）位置固定 → 显示锁图标、不可拖
        _is_bound = self._is_boundary_instance(inst)
        handle = ctk.CTkLabel(row, text='🔒' if _is_bound else '⠿',
                              font=('Segoe UI Symbol', 12),
                              text_color=TEXT_DIM, width=16, height=20,
                              cursor='arrow' if _is_bound else 'fleur')
        handle.pack(side='left', padx=(SPACE_SM, 0))

        # 勾选框
        chk = ctk.CTkCheckBox(
            row, text='', variable=var, width=20, height=20,
            fg_color=ACCENT, hover_color=BG_BTN,
            border_color=BG_INPUT,
            command=lambda: self._on_task_enabled_change(inst.inst_id, var),
        )
        chk.pack(side='left', padx=(SPACE_XS, SPACE_XS))

        # emoji 图标
        ctk.CTkLabel(row, text=meta.icon, font=('Segoe UI Emoji', 14),
                     width=26, height=20, anchor='center').pack(side='left')

        # 中间文字区：上下各留 5px，白底不贴边框线（transparent 底色会盖住边框直线段）
        mid = ctk.CTkFrame(row, fg_color='transparent')
        mid.pack(side='left', fill='x', expand=True, padx=(SPACE_SM, 0), pady=5)
        lbl_name = ctk.CTkLabel(mid, text=meta.label,
                                font=('Microsoft YaHei UI', 13, 'bold'),
                                text_color=TEXT_BRIGHT, anchor='w', height=20)
        lbl_name.pack(fill='x')
        lbl_sum = ctk.CTkLabel(mid,
                               text=self._truncate(self._instance_summary(inst), 24),
                               font=('Microsoft YaHei UI', 12),
                               text_color=TEXT, anchor='w', height=16)
        lbl_sum.pack(fill='x')

        # 状态 pill（六态）
        pill = ctk.CTkLabel(row, text='', font=('Microsoft YaHei UI', 12, 'bold'),
                            fg_color='transparent', corner_radius=8, height=20,
                            padx=10, text_color='white')

        # X 删除（最右；side='right' 先 pack 者最右，必须最先 pack）
        btn_del = ctk.CTkButton(
            row, text='✕', font=('Segoe UI Symbol', 12, 'bold'),
            fg_color='transparent', hover_color='#fee2e2',
            text_color=DANGER, width=24, height=24, corner_radius=6,
            command=lambda: self._on_delete_instance(inst.inst_id),
        )
        btn_del.pack(side='right', padx=(0, 2))

        # › 抽屉开关（X 左侧）
        btn_drawer = ctk.CTkButton(
            row, text='›', font=('Segoe UI Symbol', 16, 'bold'),
            fg_color='transparent', hover_color=BG_BTN,
            text_color=TEXT_DIM, width=24, height=24, corner_radius=6,
            command=lambda: self._toggle_drawer(inst.inst_id),
        )
        btn_drawer.pack(side='right', padx=(SPACE_XS, SPACE_XS))

        # 状态 pill（六态）最后 pack → 排在最左，不再霸占最右空白
        pill.pack(side='right', padx=(SPACE_XS, 0))

        self._task_rows[inst.inst_id] = {
            'frame': row, 'var': var, 'pill': pill,
            'lbl_name': lbl_name, 'lbl_sum': lbl_sum,
            'btn_del': btn_del, 'btn_drawer': btn_drawer,
        }

        # 整行点击切换勾选（排除手柄/按钮区域；tkinter 事件不冒泡）
        for w in (row, mid, lbl_name, lbl_sum):
            w.bind('<Button-1>',
                   lambda e, iid=inst.inst_id, v=var: self._toggle_task(iid, v))

        # 拖拽排序（仅手柄触发；v2.1.0 边界任务不绑定 → 无法拖动）
        if not _is_bound:
            handle.bind('<ButtonPress-1>',
                        lambda e, iid=inst.inst_id: self._on_task_drag_press(
                            e, iid))
            handle.bind('<B1-Motion>',
                        lambda e, iid=inst.inst_id: self._on_task_drag_motion(
                            e, iid))
            handle.bind('<ButtonRelease-1>', self._on_task_drag_release)

        # 行下指标抽屉（v1.5.4，初始隐藏）
        drawer = ctk.CTkFrame(self._task_scroll, fg_color='#f0f3f7',
                              corner_radius=8, border_width=1,
                              border_color=BG_INPUT)
        self._build_drawer_content(drawer, inst)
        drawer.pack_forget()
        self._drawers[inst.inst_id] = drawer

    # ── 行内指标抽屉（v1.5.4） ──────────────────────────────

    def _open_enum_menu(self, anchor_btn, choices, var, menu_btn):
        """enum 下拉浮层（v1.8.64）：无边框浮窗 + CTk 选项按钮，替代
        原生 tk.Menu（customtkinter DropdownMenu 底层）——白底/圆角/
        边框/hover 浅灰，与抽屉风格完全统一。
        注意用 tkinter.Toplevel 而非 ctk.CTkToplevel：后者构造时注册
        after(200/1000) 回调（标题栏图标等），浮层选中即销毁 → 回调撞
        已销毁窗口弹 TclError 错误框（真机 2026-09-07 每次弹窗事故）。
        """
        top = tk.Toplevel(self)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        frm = ctk.CTkFrame(top, fg_color='#ffffff',
                           border_color=BG_INPUT, border_width=1,
                           corner_radius=8)
        frm.pack(fill='both', expand=True, padx=2, pady=2)

        def _pick(_label, _key):
            var.set(_label)
            menu_btn.configure(text=f'{_label}  ▾')
            top.destroy()

        for _key, _label in choices:
            ctk.CTkButton(
                frm, text=_label, anchor='w',
                font=('Microsoft YaHei UI', 12), text_color=TEXT_BRIGHT,
                fg_color='transparent', hover_color=BG_BTN,
                height=34, corner_radius=6,
                command=lambda l=_label, k=_key: _pick(l, k)
            ).pack(fill='x', padx=4, pady=2)

        top.update_idletasks()
        x = anchor_btn.winfo_rootx()
        y = anchor_btn.winfo_rooty() + anchor_btn.winfo_height() + 4
        top.geometry(f'+{x}+{y}')

        # 模态抓取（v1.8.64：替代 bind_all 外点检测——bind_all 的 tag 解绑
        # 不可靠，浮层销毁后回调残留会污染后续事件循环）；浮层销毁时
        # grab 自动释放，Escape 亦关闭
        top.grab_set()
        top.bind('<Escape>', lambda e: top.destroy())   # v1.8.78：原为未定义的 _close()
        top.lift()
        top.focus_force()

    def _build_drawer_content(self, drawer, inst):
        """抽屉内容（v1.8.15）：任务说明小字 + 参数表单 + 时间/轮次 + 取消/确定

        - 说明：meta.description 浅灰小字（≤50字，用户要求）
        - 参数：按 meta.options_schema 渲染（bool→勾选框），初值取
          inst.options（未设置的用 schema default）；确定时写回
          TaskRegistry.set_instance_options 并持久化
        """
        meta = TaskRegistry.get_by_id(inst.class_id)

        # ── 任务说明（浅灰小字）─────────────────────────────
        desc = (meta.description if meta else '').strip()
        if desc:
            ctk.CTkLabel(
                drawer, text=desc[:50],
                font=('Microsoft YaHei UI', 11),
                text_color=TEXT_DIM, anchor='w', justify='left', height=18
            ).pack(fill='x', padx=SPACE_MD, pady=(SPACE_SM, 0))

        # ── 参数表单（options_schema → 控件）────────────────
        # v1.8.63：新增 enum 类型（CTkOptionMenu 下拉，二选一模式）；
        # enum_maps 存「显示 label → 存储值」反查表（_save 时回写 key）
        opt_vars = {}   # key -> tk.Variable（bool 用 BooleanVar / enum 用 StringVar）
        enum_maps = {}
        schema = (meta.options_schema if meta else {}) or {}
        bool_vars_order: list = []   # v1.9.7：bool 勾选收集，底部按钮行横排
        int_vars_order: list = []    # v1.9.29：int 输入收集，按钮行横排
        for key, spec in schema.items():
            if spec.get('type') == 'bool':
                # v1.9.8：bool 勾选延后到按钮行横排——不建行（此前
                # 空 row_opt 默认高 200px，两个 bool = 400px 抽屉空白）
                var = tk.BooleanVar(
                    value=bool(inst.options.get(
                        key, spec.get('default', False)))
                )
                opt_vars[key] = var
                bool_vars_order.append((key, spec, var))
                continue
            if spec.get('type') == 'str':
                # v1.9.32：str 项渲染（Label + 文本输入框，参数区单行，
                # 长文本不适合挤按钮行——如挑战黑名单）
                row_str = ctk.CTkFrame(drawer, fg_color='transparent')
                row_str.pack(fill='x', padx=SPACE_MD, pady=(SPACE_XS, 0))
                var = tk.StringVar(value=str(
                    inst.options.get(key, spec.get('default', ''))))
                opt_vars[key] = var
                ctk.CTkLabel(
                    row_str, text=spec.get('label', key),
                    font=('Microsoft YaHei UI', 12), text_color=TEXT,
                    width=72, anchor='w'
                ).pack(side='left')
                ctk.CTkEntry(
                    row_str, width=220, textvariable=var,
                    fg_color='#ffffff', border_color=BG_INPUT, height=28,
                    placeholder_text=spec.get('desc', '')
                ).pack(side='left')
                # v1.9.44：entry 右侧灰字说明（如名单语义速查）
                if spec.get('note'):
                    ctk.CTkLabel(
                        row_str, text=spec['note'],
                        font=('Microsoft YaHei UI', 11),
                        text_color=TEXT_DIM, anchor='w',
                        justify='left'
                    ).pack(side='left', padx=(SPACE_SM, 0))
                continue
            if spec.get('type') == 'int':
                # v1.9.29：int 项延后到按钮行横排（消耗体力右侧）——
                # 不单独建参数行（v1.9.26 曾建行在参数区）
                var = tk.StringVar(value=str(
                    inst.options.get(key, spec.get('default', 0))))
                opt_vars[key] = var
                int_vars_order.append((key, spec, var))
                continue
            if spec.get('type') not in ('enum', 'bool'):
                # v1.9.7：未知类型暂无控件渲染——同理不建 row_opt
                continue
            row_opt = ctk.CTkFrame(drawer, fg_color='transparent')
            row_opt.pack(fill='x', padx=SPACE_MD, pady=(SPACE_XS, 0))
            if spec.get('type') == 'enum':
                choices = spec.get('choices') or []
                label_map = {c[0]: c[1] for c in choices}
                enum_maps[key] = {c[1]: c[0] for c in choices}
                cur = inst.options.get(key, spec.get('default', ''))
                var = tk.StringVar(
                    value=label_map.get(cur, choices[0][1] if choices else '')
                )
                opt_vars[key] = var
                cur_label = label_map.get(
                    cur, choices[0][1] if choices else '')
                var = tk.StringVar(value=cur_label)
                opt_vars[key] = var
                ctk.CTkLabel(
                    row_opt, text=spec.get('label', key),
                    font=('Microsoft YaHei UI', 12), text_color=TEXT,
                    width=72, anchor='w'
                ).pack(side='left')
                # v1.8.64：CTkOptionMenu 的展开菜单基于原生 tk.Menu
                # （customtkinter DropdownMenu），Windows 视觉天花板低
                # （白底黑字系统菜单）——改用 CTk 浮层自建下拉，风格与
                # 抽屉完全统一（白底/圆角/边框/hover 浅灰）
                menu_btn = ctk.CTkButton(
                    row_opt,
                    text=f'{cur_label}  ▾',
                    font=('Microsoft YaHei UI', 12), text_color=TEXT,
                    fg_color='#ffffff', hover_color=BG_LOG,
                    border_color=BG_INPUT, border_width=1,
                    corner_radius=6, anchor='w', width=200, height=28,
                    command=lambda: self._open_enum_menu(
                        menu_btn, choices, var, menu_btn)
                )
                menu_btn.pack(side='left')
                continue
            if spec.get('type') != 'bool':
                continue   # v1.8.63 支持 bool/enum；新增类型时在此扩展

        # v1.8.22：单次任务（oneshot）隐藏 时间/轮次 输入——该类任务
        # 运行一次自动结束（如女神试炼：追平上限+扫荡即完成），指标无意义
        oneshot: bool = bool(getattr(meta, 'oneshot', False)) if meta else False
        # v1.9.67（用户 17:56）：时间/轮次 输入**仅自动推图保留**——
        # 判断标准：任务是否依赖「按时长/轮次自动停止」的长跑循环。
        # 其余任务（含后续新增）一律不渲染；实例原配置值保留不动，
        # 不影响任务核心逻辑。
        show_metrics: bool = bool(getattr(meta, 'show_metrics', False)) \
            if meta else False

        row = ctk.CTkFrame(drawer, fg_color='transparent')
        row.pack(fill='x', padx=SPACE_MD, pady=SPACE_SM)
        if not show_metrics:
            tip = ('单次任务：运行一次自动结束' if oneshot
                   else '任务内建终止条件（次数耗尽/通关/领取完毕）')
            ctk.CTkLabel(
                row, text=tip,
                font=('Microsoft YaHei UI', 11), text_color=TEXT_DIM,
                anchor='w'
            ).pack(side='left')
        else:
            ctk.CTkLabel(row, text='时间（秒）', font=('Microsoft YaHei UI', 12),
                         text_color=TEXT, width=72, anchor='w').pack(side='left')
            e_time = ctk.CTkEntry(row, width=90, placeholder_text='无限',
                                  fg_color='#ffffff', border_color=BG_INPUT,
                                  height=28)
            e_time.pack(side='left')
            if inst.max_time:
                e_time.insert(0, str(inst.max_time))
            ctk.CTkLabel(row, text='轮次', font=('Microsoft YaHei UI', 12),
                         text_color=TEXT, width=36, anchor='e').pack(
                side='left', padx=(SPACE_SM, 0))
            e_turns = ctk.CTkEntry(row, width=70, placeholder_text='无限',
                                   fg_color='#ffffff', border_color=BG_INPUT,
                                   height=28)
            e_turns.pack(side='left')
            if inst.max_turns:
                e_turns.insert(0, str(inst.max_turns))

        def _parse(s):
            s = (s or '').strip()
            return int(s) if s else None

        def _save():
            if not show_metrics:
                # v1.9.67：不渲染时间/轮次的任务 —— 保持实例原值不动
                # （隐藏 ≠ 清空，避免破坏任务既有运行逻辑）
                mt, mturns = inst.max_time, inst.max_turns
            else:
                try:
                    mt = _parse(e_time.get())
                    mturns = _parse(e_turns.get())
                except ValueError:
                    self._append_log('[警告] 指标必须是整数')
                    return
            TaskRegistry.set_instance_metrics(inst.inst_id, mt, mturns)
            # v1.8.15：收集参数表单值写回实例并持久化
            # v1.8.63：enum 控件显示 label，写回时反查存储 key
            new_opts = {}
            for key, var in opt_vars.items():
                _st = schema.get(key, {}).get('type')
                if _st == 'enum':
                    new_opts[key] = enum_maps.get(key, {}).get(
                        var.get(), var.get()
                    )
                elif _st == 'int':
                    # v1.9.25：int 输入框解析（空/非法回退默认值）
                    try:
                        new_opts[key] = int(str(var.get()).strip()
                                            or schema[key].get('default', 0))
                    except ValueError:
                        new_opts[key] = int(schema[key].get('default', 0))
                elif _st == 'str':
                    # v1.9.32：str 输入框（原样保存，去首尾空白）
                    new_opts[key] = str(var.get()).strip()
                else:
                    new_opts[key] = bool(var.get())
            TaskRegistry.set_instance_options(inst.inst_id, new_opts)
            TaskRegistry.save_config()
            self._render_task_rows()
            opt_txt = ' '.join(
                f"{schema[k].get('label', k)}="
                + (str(v) if schema.get(k, {}).get('type') in
                   ('int', 'enum', 'str')
                   else ("开" if v else "关"))
                for k, v in new_opts.items()
            ) or '无参数'
            metrics_txt = (f' 时间={mt or "无限"} 轮次={mturns or "无限"}'
                           if show_metrics else '')
            self._append_log(
                f'[信息] 已更新配置: {meta.label} ({inst.inst_id})'
                f'{metrics_txt} {opt_txt}'
            )

        # v1.9.7（用户定案）：bool 勾选横排在按钮行左侧——消耗体力、
        # 魔潮侵袭、取消、确定同一行，消除抽屉纵向空白
        for _bk, _bspec, _bvar in bool_vars_order:
            # v1.9.9：schema 标记 disabled=未实装 → 置灰且不可勾选
            _dis = bool(_bspec.get('disabled'))
            _box = ctk.CTkCheckBox(
                row, text=_bspec.get('label', _bk), variable=_bvar,
                font=('Microsoft YaHei UI', 12),
                text_color=(TEXT_DIM if _dis else TEXT),
                fg_color=ACCENT, hover_color=BG_BTN,
                border_color=BG_INPUT, width=110, height=28,
                state=('disabled' if _dis else 'normal')
            )
            _box.pack(side='left', padx=(SPACE_SM, 0))
        # v1.9.29：int 项（保留体力）横排在勾选框右侧——label + 输入框
        for _ik, _ispec, _ivar in int_vars_order:
            ctk.CTkLabel(
                row, text=_ispec.get('label', _ik),
                font=('Microsoft YaHei UI', 12), text_color=TEXT,
                width=64, anchor='e'
            ).pack(side='left', padx=(SPACE_SM, 0))
            ctk.CTkEntry(
                row, width=80, textvariable=_ivar,
                fg_color='#ffffff', border_color=BG_INPUT, height=28
            ).pack(side='left', padx=(4, 0))
        ctk.CTkButton(row, text='确定', fg_color=ACCENT, text_color='white',
                      font=('Microsoft YaHei UI', 12, 'bold'),
                      width=64, height=28, corner_radius=6,
                      command=_save).pack(side='right')
        ctk.CTkButton(row, text='取消', fg_color='transparent',
                      hover_color=BG_BTN, text_color=TEXT,
                      font=('Microsoft YaHei UI', 12),
                      width=64, height=28, corner_radius=6,
                      command=lambda: self._toggle_drawer(inst.inst_id)).pack(
            side='right', padx=(0, SPACE_SM))

    # ── 抽屉互斥（v1.5.4） ────────────────────────────────

    def _close_task_drawers(self, except_id=None):
        """收起所有任务行抽屉（except_id 指定的保持展开）"""
        for iid, d in self._drawers.items():
            if iid != except_id and d.winfo_manager() == 'pack':
                d.pack_forget()
                r = self._task_rows.get(iid)
                if r and r.get('btn_drawer'):
                    r['btn_drawer'].configure(text='›')

    def _close_add_drawer(self):
        if self._add_drawer and self._add_drawer.winfo_manager() == 'pack':
            self._add_drawer.pack_forget()

    def _close_settings_drawer(self):
        if self._settings_open:
            self._close_settings()

    def _close_all_drawers(self):
        """Esc / 互斥：收起全部抽屉（任务 + 添加 + 设置）"""
        self._close_task_drawers()
        self._close_add_drawer()
        self._close_settings_drawer()
        self._refresh_batch_btns()

    def _toggle_drawer(self, inst_id):
        """› 切换行内抽屉（互斥：同时只展开一个抽屉）"""
        if self._running:
            self._append_log('[信息] 运行中不可修改任务')
            return
        drawer = self._drawers.get(inst_id)
        if not drawer:
            return
        row = self._task_rows.get(inst_id, {})
        btn = row.get('btn_drawer')
        if drawer.winfo_manager() == 'pack':   # 展开 -> 收起
            drawer.pack_forget()
            if btn:
                btn.configure(text='›')
            self._refresh_batch_btns()
            return
        # 互斥：收起其它任务抽屉 / 底部添加抽屉 / 设置抽屉
        self._close_task_drawers(except_id=inst_id)
        self._close_add_drawer()
        self._close_settings_drawer()
        # after= 保证抽屉紧跟自己的行，而不是被 pack 到列表末尾
        frame = row.get('frame')
        if frame:
            drawer.pack(fill='x', pady=(0, SPACE_SM), after=frame)
        else:
            drawer.pack(fill='x', pady=(0, SPACE_SM))
        if btn:
            btn.configure(text='▾')
        self._refresh_batch_btns()

    def _on_delete_instance(self, inst_id):
        """X 删除当前任务实例"""
        if self._running:
            self._append_log('[信息] 运行中不可删除任务')
            return
        inst = TaskRegistry.get_instance(inst_id)
        if not inst:
            return
        meta = TaskRegistry.get_by_id(inst.class_id)
        TaskRegistry.remove_instance(inst_id)
        TaskRegistry.save_config()
        self._render_task_rows()
        self._append_log(f'[信息] 已删除任务: {meta.label} ({inst_id})')

    def _instance_summary(self, inst) -> str:
        """实例摘要：类描述 + 结束指标"""
        meta = TaskRegistry.get_by_id(inst.class_id)
        base = meta.description if meta else ''
        metrics = []
        if inst.max_time:
            metrics.append(f'时间{inst.max_time}s')
        if inst.max_turns:
            metrics.append(f'轮次{inst.max_turns}')
        if metrics:
            return f'{base} | {" / ".join(metrics)}'
        return base or '未设指标（无限，全通关兜底）'

    # ── 拖拽排序（实例） ────────────────────────────────────

    def _on_task_drag_press(self, event, inst_id):
        # v2.1.0：边界任务（启动/关闭游戏）位置固定，禁止拖动（防御）
        _inst = TaskRegistry.get_instance(inst_id) \
            if hasattr(TaskRegistry, "get_instance") else None
        if _inst is not None and self._is_boundary_instance(_inst):
            return
        if self._running:
            return
        self._drag_task = inst_id
        self._drag_last_y = event.y_root
        self._drag_pending = False
        # 拖拽动画①：被拖行呈半透明态（浅灰底 + 文字变淡模拟）
        self._set_row_drag_style(inst_id, dragging=True)

    def _on_task_drag_motion(self, event, inst_id):
        if self._drag_task != inst_id or self._running:
            return
        dy = event.y_root - self._drag_last_y
        threshold = 26   # (行高44 + 间距8) / 2
        moved = False
        if dy > threshold:
            moved = TaskRegistry.move_instance(inst_id, 1)
            self._drag_last_y = event.y_root
        elif dy < -threshold:
            moved = TaskRegistry.move_instance(inst_id, -1)
            self._drag_last_y = event.y_root
        if moved:
            self._drag_pending = True
            # 拖拽动画②：实时换位（只重排 pack 顺序，不销毁组件，
            # 保持手柄隐式 grab 不中断连续拖动）
            self._reorder_task_rows_visual()

    def _on_task_drag_release(self, event):
        if getattr(self, '_drag_pending', False):
            TaskRegistry.save_config()
            self._render_task_rows()
            self._drag_pending = False
        if getattr(self, '_drag_task', None):
            self._set_row_drag_style(self._drag_task, dragging=False)
        self._drag_task = None

    def _set_row_drag_style(self, inst_id: str, dragging: bool):
        """拖拽半透明态：底色变浅灰、文字变淡；恢复时还原"""
        row = self._task_rows.get(inst_id)
        if not row:
            return
        frame = row['frame']
        if dragging:
            frame.configure(fg_color=BG_BTN)
            row['lbl_name'].configure(text_color=TEXT_DIM)
            row['lbl_sum'].configure(text_color='#c8cfd9')
        else:
            frame.configure(fg_color=BG_CARD)
            row['lbl_name'].configure(text_color=TEXT_BRIGHT)
            row['lbl_sum'].configure(text_color=TEXT)

    def _reorder_task_rows_visual(self):
        """按 registry 顺序重排现有行（不销毁组件，拖拽中实时换位）"""
        # 记录展开中的抽屉，重排后恢复到各自行的下方
        open_ids = [iid for iid, d in self._drawers.items()
                    if d.winfo_manager() == 'pack']
        for row in self._task_rows.values():
            row['frame'].pack_forget()
        for d in self._drawers.values():
            d.pack_forget()
        self._lbl_empty_hint.pack_forget()
        for inst in self._ordered_instances():   # v2.1.0：按渲染顺序（边界固定）
            frame = self._task_rows[inst.inst_id]['frame']
            frame.pack(fill='x', pady=(0, SPACE_SM))
            if inst.inst_id in open_ids:
                self._drawers[inst.inst_id].pack(
                    fill='x', pady=(0, SPACE_SM), after=frame)
        self._lbl_empty_hint.pack(fill='x', pady=SPACE_MD)
        self._update_empty_hint()

    # ── v2.1.0（用户 20:22）：边界任务在 GUI 内固定位置、不可拖动 ──
    _BOUNDARY_FIRST_CLASS: str = 'start_game'
    _BOUNDARY_LAST_CLASS: str = 'close_game'

    @classmethod
    def _ordered_instances(cls):
        """GUI 显示顺序：启动游戏恒在首、关闭游戏恒在尾（其余保持原序）。"""
        insts = list(TaskRegistry.get_instances())
        firsts = [i for i in insts
                  if i.class_id == cls._BOUNDARY_FIRST_CLASS]
        lasts = [i for i in insts
                 if i.class_id == cls._BOUNDARY_LAST_CLASS]
        middle = [i for i in insts
                  if i.class_id not in (cls._BOUNDARY_FIRST_CLASS,
                                        cls._BOUNDARY_LAST_CLASS)]
        return firsts + middle + lasts

    @classmethod
    def _is_boundary_instance(cls, inst) -> bool:
        """是否为位置固定的边界任务（启动/关闭游戏）。"""
        return inst.class_id in (cls._BOUNDARY_FIRST_CLASS,
                                 cls._BOUNDARY_LAST_CLASS)

    def _render_task_rows(self):
        """销毁并重建任务行（排序/添加/删除/指标变更后调用）"""
        for child in self._task_scroll.winfo_children():
            child.destroy()
        self._task_rows = {}
        self._drawers = {}
        for inst in self._ordered_instances():   # v2.1.0 边界任务固定位置
            self._add_task_row(inst)
        # 重建空态提示（子组件销毁会连带）
        self._lbl_empty_hint = ctk.CTkLabel(
            self._task_scroll, text='未勾选任务 —— 勾选后点击开始',
            font=('Microsoft YaHei UI', 12), text_color=TEXT_DIM
        )
        self._update_empty_hint()
        self._refresh_batch_btns()

    @staticmethod
    def _truncate(text: str, max_len: int) -> str:
        """超长文本截断，防止行内溢出"""
        if len(text) <= max_len:
            return text
        return text[:max_len - 1] + '…'

    def _toggle_task(self, inst_id: str, var: tk.BooleanVar):
        """整行点击切换勾选"""
        var.set(not var.get())
        self._on_task_enabled_change(inst_id, var)

    def _update_empty_hint(self):
        """刷新空态提示（无启用实例时显示）"""
        if not hasattr(self, '_lbl_empty_hint'):
            return
        if TaskRegistry.get_enabled_instances():
            self._lbl_empty_hint.pack_forget()
        else:
            self._lbl_empty_hint.pack(fill='x', pady=SPACE_MD)

    def _reset_task_rows(self):
        """复位所有任务行到 idle 状态（必须在主线程调用）"""
        for inst in TaskRegistry.get_instances():
            row = self._task_rows.get(inst.inst_id)
            if row:
                row['frame'].configure(border_color=BG_INPUT, border_width=1)
                row['pill'].configure(text='', fg_color='transparent')

    def _on_task_status(self, task_label, task_index, task_total, task_state,
                        inst_id=None):
        """TaskScheduler.status_cb 回调：更新任务行六态（线程安全，切主线程）"""
        self._post_ui(lambda: self._apply_task_status(inst_id, task_state))

    def _apply_task_status(self, inst_id, task_state):
        """六态机（pill 标签，按 inst_id 更新）"""
        if not inst_id:
            return
        row = self._task_rows.get(inst_id)
        if not row:
            return
        frame = row['frame']
        pill = row['pill']
        if task_state == 'running':
            frame.configure(border_color=ACCENT, border_width=2)
            pill.configure(text='运行中', fg_color=ACCENT, text_color='white')
            # 记录运行实例（供 _refresh_timer 刷新 HH:MM:SS）
            self._running_inst_id = inst_id
            self._running_start_time = time.time()
        elif task_state == 'done':
            frame.configure(border_color=SUCCESS, border_width=1)
            pill.configure(text='完成', fg_color='#059669', text_color='white')
            self._running_inst_id = None
        elif task_state == 'failed':
            frame.configure(border_color=DANGER, border_width=1)
            pill.configure(text='失败', fg_color=DANGER, text_color='white')
            self._running_inst_id = None
        elif task_state == 'stopped':
            frame.configure(border_color=ORANGE, border_width=1)
            pill.configure(text='已停止', fg_color='#d97706', text_color='white')
            self._running_inst_id = None
        elif task_state == 'skipped':
            frame.configure(border_color=BG_INPUT, border_width=1)
            pill.configure(text='跳过', fg_color='#9ca3af', text_color='white')
            self._running_inst_id = None

    # ── 实例操作 ────────────────────────────────────────────

    def _on_add_instance(self, class_id: str):
        """行内 + 按钮：添加同 class 新实例"""
        if self._running:
            self._append_log('[信息] 运行中不可添加任务')
            return
        inst = TaskRegistry.add_instance(class_id)
        TaskRegistry.save_config()
        self._render_task_rows()
        meta = TaskRegistry.get_by_id(class_id)
        self._append_log(
            f'[信息] 已添加任务实例: {meta.label} ({inst.inst_id})'
        )

    # ── 底部「添加任务」抽屉（v1.5.4，替代弹窗） ──────────

    def _build_add_drawer(self):
        """底部加任务抽屉（初始隐藏，展开时 before 控制栏）"""
        drawer = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10,
                              border_width=1, border_color=BG_INPUT)
        hdr = ctk.CTkFrame(drawer, fg_color='transparent')
        hdr.pack(fill='x', padx=SPACE_MD, pady=(SPACE_SM, 0))
        ctk.CTkLabel(hdr, text='加任务（同种可加多次）',
                     font=('Microsoft YaHei UI', 13, 'bold'),
                     text_color=TEXT_BRIGHT, anchor='w').pack(side='left')
        ctk.CTkButton(hdr, text='✕', font=('Segoe UI Symbol', 13, 'bold'),
                      fg_color='transparent', hover_color='#fee2e2',
                      text_color=DANGER, width=24, height=24, corner_radius=4,
                      command=self._toggle_add_drawer).pack(side='right')

        # 细胞网格（v1.5.8）：无组标题，定宽细胞左对齐，一行放不下自动换行
        count_map = {}
        for inst in TaskRegistry.get_instances():
            count_map[inst.class_id] = count_map.get(inst.class_id, 0) + 1

        grid_wrap = ctk.CTkFrame(drawer, fg_color='transparent')
        grid_wrap.pack(fill='x', padx=SPACE_MD, pady=(SPACE_SM, SPACE_SM))
        cols = 4
        metas = TaskRegistry.get_all()
        for idx, meta in enumerate(metas):
            cell = self._build_add_cell(
                grid_wrap, meta, count_map.get(meta.id, 0))
            cell.grid(row=idx // cols, column=idx % cols,
                      sticky='w', padx=SPACE_XS, pady=SPACE_XS)

        drawer.pack_forget()
        self._add_drawer = drawer

    def _build_add_cell(self, parent, meta, count: int):
        """任务细胞（定宽）：+ / 图标 / 名称 / 已添加数量徽章（点击任意位置添加）"""
        ready = meta.status == 'ready'
        cell = ctk.CTkFrame(parent, fg_color=BG_INPUT, corner_radius=8,
                            width=172, height=36, cursor='hand2')
        cell.pack_propagate(False)
        ctk.CTkLabel(cell, text='+' if ready else '🔒',
                     font=('Segoe UI Symbol', 14, 'bold'),
                     text_color=ACCENT if ready else TEXT_DIM,
                     width=18, height=24).pack(side='left', padx=(SPACE_SM, 0))
        ctk.CTkLabel(cell, text=meta.icon, font=('Segoe UI Emoji', 13),
                     width=22, height=24).pack(side='left')
        ctk.CTkLabel(cell, text=meta.label,
                     font=('Microsoft YaHei UI', 13, 'bold'),
                     text_color=TEXT_BRIGHT if ready else TEXT_DIM,
                     height=24).pack(side='left', padx=(SPACE_XS, 0))
        if count:   # 未添加过则完全不显示徽章
            ctk.CTkLabel(cell, text=str(count),
                         font=('Microsoft YaHei UI', 12, 'bold'),
                         fg_color='#ffffff', corner_radius=9, width=26,
                         height=20, text_color=TEXT).pack(
                side='right', padx=(SPACE_XS, SPACE_SM))
        # 整细胞可点击（tkinter 事件不冒泡，需绑到全部子组件）
        targets = [cell] + list(cell.winfo_children())
        for w in targets:
            if ready:
                w.bind('<Button-1>',
                       lambda e, cid=meta.id: self._add_from_drawer(cid))
            else:
                w.bind('<Button-1>',
                       lambda e, m=meta: self._add_rejected(m))
        return cell

    def _toggle_add_drawer(self):
        """展开/收起底部加任务抽屉（互斥：关闭任务抽屉）"""
        if self._running:
            self._append_log('[信息] 运行中不可添加任务')
            return
        if self._add_drawer and self._add_drawer.winfo_manager() == 'pack':
            self._add_drawer.pack_forget()
            return
        # 互斥：收起所有任务抽屉与设置抽屉
        self._close_task_drawers()
        self._close_settings_drawer()
        self._add_drawer.pack(fill='x', padx=SPACE_LG,
                              pady=(0, SPACE_SM), before=self._control_bar)

    def _add_from_drawer(self, class_id: str):
        """从细胞网格添加实例（成功后自动收起抽屉）"""
        inst = TaskRegistry.add_instance(class_id)
        TaskRegistry.save_config()
        self._render_task_rows()
        meta = TaskRegistry.get_by_id(class_id)
        self._append_log(f'[信息] 已添加任务: {meta.label} ({inst.inst_id})')
        self._close_add_drawer()

    def _add_rejected(self, meta):
        """点击未实现任务细胞：不添加，日志红字提示"""
        self._append_log(f'[错误] {meta.label} 尚未实现，无法添加')

    # ── 设置抽屉 ──

    def _build_settings_drawer(self):
        # 抽屉用 place 绝对定位，从右侧覆盖进来
        self._settings_drawer = tk.Frame(self, bg=BG_DRAWER, width=290)
        self._settings_drawer.place_forget()

        inner = ctk.CTkFrame(self._settings_drawer, fg_color=BG_DRAWER, corner_radius=0)
        inner.pack(fill='both', expand=True)

        self._settings_inner = inner

        # 标题栏
        drawer_hdr = ctk.CTkFrame(self._settings_inner, fg_color=BG_HEADER, height=44, corner_radius=0)
        drawer_hdr.pack(fill='x', padx=0, pady=0)
        drawer_hdr.pack_propagate(False)

        ctk.CTkLabel(drawer_hdr, text='⚙ 设置',
                     font=self._f_sub, text_color=TEXT_BRIGHT,
                     anchor='w').pack(side='left', padx=SPACE_MD, pady=SPACE_SM)

        self._btn_settings_close = ctk.CTkButton(
            drawer_hdr, text='✕', font=('Segoe UI Symbol', 16),
            fg_color='transparent', hover_color=BG_BTN,
            text_color=TEXT_DIM, width=44, height=44, corner_radius=6,
            command=self._toggle_settings
        )
        self._btn_settings_close.pack(side='right', padx=SPACE_XS, pady=SPACE_XS)

        # 可滚动内容区
        drawer_content = ctk.CTkScrollableFrame(
            self._settings_inner, fg_color='transparent',
            scrollbar_button_color=BG_BTN,
            scrollbar_button_hover_color=BG_BTN_HOVER
        )
        drawer_content.pack(fill='both', expand=True, padx=0, pady=0)
        drawer_content._scrollbar.configure(width=4)

        pad = {'padx': SPACE_MD, 'pady': (SPACE_MD, 0), 'anchor': 'w', 'fill': 'x'}

        # ── 窗口设置 ──
        ctk.CTkLabel(drawer_content, text='窗口',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)

        # 透明度
        row_alpha = ctk.CTkFrame(drawer_content, fg_color='transparent')
        row_alpha.pack(**pad)
        ctk.CTkLabel(row_alpha, text='透明度', font=self._f_small,
                     text_color=TEXT_DIM, width=54, anchor='w').pack(side='left')
        self._slider_alpha = ctk.CTkSlider(
            row_alpha, from_=0.5, to=1.0, number_of_steps=10,
            fg_color=BG_INPUT, progress_color=ACCENT,
            button_color=ACCENT, button_hover_color='#7aa3ff',
            command=self._on_alpha_change, width=100
        )
        self._slider_alpha.pack(side='left', padx=(0, SPACE_SM))
        self._slider_alpha.set(1.0)

        # 置顶
        row_top = ctk.CTkFrame(drawer_content, fg_color='transparent')
        row_top.pack(**pad)
        self._chk_top = ctk.CTkCheckBox(
            row_top, text='窗口置顶',
            font=self._f_small, text_color=TEXT,
            fg_color=BG_INPUT, hover_color=BG_BTN,
            border_color=BG_BTN,
            command=self._on_topmost_change
        )
        self._chk_top.pack(side='left')

        # ── ⑤ 关于 ──
        sep5 = ctk.CTkFrame(drawer_content, fg_color=BG_BTN, height=1)
        sep5.pack(fill='x', padx=SPACE_MD, pady=SPACE_MD)

        ctk.CTkLabel(drawer_content, text=f'🧠 关于 {VERSION}',
                     font=self._f_small, text_color=TEXT, anchor='w').pack(**pad)
        info_lines = [
            '• Maa识别 PP-识别v5 引擎',
            '• MaaFramework 流程 架构',
            '• ROI 区域 识别 优化',
            '• ColorMatch 颜色检测',
            '• freeze 画面稳定等待',
        ]
        for line in info_lines:
            ctk.CTkLabel(drawer_content, text=line,
                         font=('Microsoft YaHei UI', 12), text_color=TEXT_DIM,
                         anchor='w').pack(**pad)

        # ── ⑥ 自动更新（v2.2.20）──
        sep6 = ctk.CTkFrame(drawer_content, fg_color=BG_BTN, height=1)
        sep6.pack(fill='x', padx=SPACE_MD, pady=SPACE_MD)

        row = ctk.CTkFrame(drawer_content, fg_color='transparent')
        row.pack(**pad)   # pad 里已含 fill='x'
        self._update_btn = ctk.CTkButton(
            row, text='🔄 检查更新', width=108, height=30,
            font=self._f_small, fg_color=BG_BTN, hover_color=BG_INPUT,
            border_color=BG_BTN, command=self._on_check_update,
        )
        self._update_btn.pack(side='left')
        self._update_apply_btn = ctk.CTkButton(
            row, text='⬇️ 立即更新', width=108, height=30,
            font=self._f_small, fg_color=BG_BTN, hover_color=BG_INPUT,
            border_color=BG_BTN, command=self._on_apply_update,
        )
        # 未发现新版本前不显示「立即更新」
        # 注意：CTkLabel **不支持 justify**（tkinter.Label 才有）→ 传了会在
        # 构建设置抽屉时抛错、导致 App 构造失败（本次已踩，A/B 才定位到）
        # ⚠️ 名字**绝不能**叫 _update_status —— 那是 core 的状态回调方法
        #（_init_bot_background 会把它当 status_callback 传给引擎，
        #  同名会把它覆盖成 CTkLabel → core 调 set_status() 报
        #  'CTkLabel' object is not callable，v2.2.20 真机即因此崩）
        self._upd_status_lbl = ctk.CTkLabel(
            drawer_content, text='', font=('Microsoft YaHei UI', 12),
            text_color=TEXT_DIM, anchor='w', wraplength=300,
        )
        self._upd_status_lbl.pack(**pad)

    # ── 自动更新 ────────────────────────────────────────────

    def _set_update_status(self, text: str, color=None) -> None:
        """更新状态文案（**必须主线程调用**，后台线程走 _post_ui）。"""
        try:
            self._upd_status_lbl.configure(text=text,
                                          text_color=color or TEXT_DIM)
        except Exception:
            pass

    def _on_check_update(self, silent: bool = False) -> None:
        """检查 GitHub 最新版（后台线程，不阻塞界面）。"""
        def _work():
            try:
                from afk_bot import updater
                # v2.2.23（用户 10-27）：**源码模式走 git** —— 不再需要
                # Release/Token，也不用每次传 136MB 的 exe ✓
                if not getattr(sys, 'frozen', False):
                    info = updater.check_source()
                    if info is None:
                        self._post_ui(lambda: self._set_update_status(
                            f'✅ 已是最新（源码 {VERSION}）'))
                    else:
                        self._pending_update = 'source'
                        self._post_ui(lambda: self._on_update_found(
                            f'🆕 远端有新提交：{info.local} → {info.remote}',
                            f'落后 {info.behind} 个提交（源码模式，git pull）'))
                    return
                info = updater.check_latest()
                if info is None:
                    self._post_ui(lambda: self._set_update_status(
                        f'✅ 已是最新版（{VERSION}）'))
                else:
                    size = (f'{info.size / 1048576:.1f}MB'
                            if info.size else '大小未知')
                    self._post_ui(lambda: self._on_update_found(
                        f'🆕 发现新版本 {info.tag}（{size}）',
                        (info.notes or '')[:120]))
            except Exception as e:
                msg = f'❌ {e}'
                self._post_ui(lambda: self._set_update_status(msg))

        self._set_update_status('⏳ 正在检查更新…')
        threading.Thread(target=_work, daemon=True,
                         name='update-check').start()

    def _on_update_found(self, title: str, detail: str = '') -> None:
        """**主线程**：发现新版本的 UI 更新（源码/打包共用）。"""
        self._set_update_status(f'{title}\n{detail}' if detail else title,
                                color=WARN if 'WARN' in globals() else None)
        try:
            self._update_apply_btn.pack(side='left', padx=(SPACE_SM, 0))
        except Exception:
            pass

    def _on_apply_update(self) -> None:
        """下载并替换（仅打包 exe 生效）。"""
        info = getattr(self, '_pending_update', None)
        if info is None:
            return
        # v2.2.23：源码模式 → 直接 git pull（无需 Release / 无关 exe 体积）
        if not getattr(sys, 'frozen', False):
            def _work_src():
                try:
                    from afk_bot import updater
                    self._post_ui(lambda: self._set_update_status('⏳ 正在 git pull…'))
                    updater.git_pull()
                    self._post_ui(lambda: self._set_update_status(
                        '✅ 已更新到最新源码，请**重启工具**生效'))
                    self._pending_update = None
                except Exception as e:
                    msg = f'❌ 更新失败：{e}'
                    self._post_ui(lambda: self._set_update_status(msg))
            threading.Thread(target=_work_src, daemon=True,
                             name='update-git').start()
            return

        def _work():
            try:
                from afk_bot import updater
                self._post_ui(lambda: self._set_update_status('⏳ 正在下载…'))
                path = updater.download_asset(info)
                self._post_ui(lambda: self._set_update_status('⏳ 正在安装（即将重启）…'))
                updater.launch_updater(path)
                # 让替换脚本能覆盖 exe：必须真正退出进程
                self._post_ui(lambda: (self.destroy(), os._exit(0)))
            except Exception as e:
                msg = f'❌ 更新失败：{e}'
                self._post_ui(lambda: self._set_update_status(msg))

        threading.Thread(target=_work, daemon=True,
                         name='update-apply').start()

    def _clear_log(self):
        """清空日志区"""
        self._log_text.configure(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.configure(state='disabled')
        self._append_log('— 日志已清空 —')

    def _toggle_settings(self):
        if self._settings_open:
            self._close_settings()
        else:
            self._open_settings()

    def _open_settings(self):
        # 互斥：收起任务抽屉与底部添加抽屉，避免遮罩重叠
        self._close_task_drawers()
        self._close_add_drawer()
        self._settings_open = True
        self._btn_gear.configure(text_color=ACCENT)
        self.update_idletasks()
        # 抽屉先放在右侧隐藏位置（x=290）
        self._settings_drawer.place(relx=1.0, rely=0.0, relheight=1.0, anchor='ne', x=290, y=0)
        self._settings_drawer.lift()
        # 启动滑入动画（250ms，10步）
        self._settings_anim_step(290, 0, 10, None)

    def _settings_anim_step(self, x_from, x_to, steps_left, callback=None):
        if steps_left <= 0:
            self._settings_drawer.place_configure(x=x_to)
            if callback:
                callback()
            return
        # 计算当前帧的 x（ease-out 缓动）
        t = 1.0 - (steps_left - 1) / 10.0  # 0→1
        t = 1 - (1 - t) ** 2  # ease-out 二次方
        x = int(x_from + (x_to - x_from) * t)
        self._settings_drawer.place_configure(x=x)
        self.after(25, lambda: self._settings_anim_step(x_from, x_to, steps_left - 1, callback))

    def _close_settings(self):
        self._settings_open = False
        self._btn_gear.configure(text_color=TEXT_DIM)
        # 启动滑出动画（250ms，10步），结束后隐藏
        self._settings_anim_step(0, 290, 10, callback=lambda: self._settings_drawer.place_forget())

    # ── GUI 设置持久化（v1.8.16：gui_config.json） ──────

    def _load_gui_settings(self):
        """启动时恢复透明度/置顶/窗口几何（无文件或字段缺失时用默认值）"""
        try:
            with open(GUI_CONFIG, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception:
            return
        geo = str(cfg.get('geometry') or '')
        if geo:
            try:
                self.geometry(geo)
            except Exception:
                pass
        alpha = cfg.get('alpha')
        if isinstance(alpha, (int, float)):
            alpha = min(1.0, max(0.5, float(alpha)))   # 与滑条范围一致
            self._slider_alpha.set(alpha)
            self.attributes('-alpha', alpha)
        if cfg.get('topmost'):
            self._chk_top.select()
            self._on_topmost_change()

    def _save_gui_setting(self, key, value):
        """读改写单键保存（不覆盖其他键）；IO 失败静默，不影响主流程"""
        try:
            try:
                with open(GUI_CONFIG, encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}
            cfg[key] = value
            with open(GUI_CONFIG, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ────────────────────────────────────────────────────────────────
    #  事件

    def _on_alpha_change(self, val):
        v = round(val, 1)
        self.attributes('-alpha', v)
        self._save_gui_setting('alpha', v)

    def _on_topmost_change(self):
        v = bool(self._chk_top.get())
        self.attributes('-topmost', v)
        self._save_gui_setting('topmost', v)

    def _on_task_enabled_change(self, inst_id: str, var: tk.BooleanVar):
        TaskRegistry.set_instance_enabled(inst_id, bool(var.get()))
        TaskRegistry.save_config()
        self._update_empty_hint()
        self._refresh_batch_btns()

    # ────────────────────────────────────────────────────────────────
    #  日志

    def _append_log(self, msg):
        self._log_queue.put(msg)
        # v2.1.27（P0-2）：留一份现场快照——崩溃报告自带最后 20 行日志
        try:
            _recent_logs.append(msg)
        except Exception:
            pass
        # 同时写文件日志
        try:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            self._log_file.write(f'[{ts}] {msg}\n')
        except Exception as e:
            # v2.1.17：不再自我递归（磁盘满/文件被占用会持续失败 →
            # 后台线程 RecursionError）。只警告一次，且直接写 stderr。
            if not getattr(self, '_log_write_warned', False):
                self._log_write_warned = True
                try:
                    sys.stderr.write(f'[警告] 日志写入失败 | {e}\n')
                except Exception:
                    pass
    def _post_ui(self, fn):
        """把 UI 操作投递到**主线程**执行（后台线程必须走这里）。

        背景（真机 2026-09-11 13:08 CRASH_REPORT.log）：`_init_bot_background`
        等**后台线程**直接调 `self.after(...)` → Tkinter 抛
        「RuntimeError: main thread is not in main loop」（after 内部
        `createcommand` 只能在主线程调用）；且 `except` 分支**又调一次**
        after → 异常二次抛出 → 线程崩溃、日志刷屏。
        现统一入队，由主线程 `_flush`（每 200ms）消费执行；窗口关闭后丢弃。

        Args:
            fn: 无参可调用对象（在主线程执行）
        """
        if getattr(self, '_closing', False):
            return
        try:
            self._ui_queue.put(fn)
        except Exception:
            pass

    def _start_flush_timer(self):
        """启动日志+状态批量刷新定时器（每200ms一次）"""
        if self._flush_id:
            self.after_cancel(self._flush_id)
        self._flush_id = self.after(200, self._flush)

    def _flush(self):
        """批量刷新日志队列 + 合并状态更新为一次GUI更新"""
        try:
            # v2.1.17：先执行后台线程投递的 UI 任务（每轮上限 20 个，防饿死）
            for _ in range(20):
                try:
                    _ui_fn = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    _ui_fn()
                except Exception as _e:
                    self._append_log(f'[警告] UI 任务执行失败 | {_e}')
            log_msgs = []
            latest_status = None
            while True:
                try:
                    item = self._log_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(item, tuple) and item[0] == '__status__':
                    latest_status = item[1]
                else:
                    log_msgs.append(item)

            # 批量写日志（所有日志一次性写入，state只切换2次）
            if log_msgs:
                self._log_text.configure(state='normal')
                ts = time.strftime('%H:%M:%S')
                for msg in log_msgs:
                    msg = msg.strip()  # 去除前导/尾随空格，确保 tag 匹配正确
                    tag = self._pick_log_tag(msg)
                    self._log_text.insert('end', f'[{ts}] ', 'ts')
                    self._log_text.insert('end', msg + '\n', tag or '')
                self._log_text.see('end')
                line_count = int(self._log_text.index('end-1c').split('.')[0])
                if line_count > 2000:
                    self._log_text.delete('1.0', f'{line_count - 1500}.0')
                self._log_text.configure(state='disabled')

            # 状态只刷新最后一次（避免多次调用导致GUI抖动）
            if latest_status is not None:
                self._refresh_ui(latest_status)
        except Exception as e:
            self._append_log(f'[错误] 刷新失败 | {e}')
        self._flush_id = self.after(200, self._flush)

    @staticmethod
    def _lerp_color(c1, c2, t):
        """HSL 颜色插值: t=0→c1, t=1→c2，绕过 RGB 直线穿绿区"""
        import colorsys
        r1, g1, b1 = int(c1[1:3], 16) / 255, int(c1[3:5], 16) / 255, int(c1[5:7], 16) / 255
        r2, g2, b2 = int(c2[1:3], 16) / 255, int(c2[3:5], 16) / 255, int(c2[5:7], 16) / 255
        h1, l1, s1 = colorsys.rgb_to_hls(r1, g1, b1)
        h2, l2, s2 = colorsys.rgb_to_hls(r2, g2, b2)
        if abs(h2 - h1) > 0.5:
            if h1 < h2: h1 += 1.0
            else:       h2 += 1.0
        h = (h1 + (h2 - h1) * t) % 1.0
        l = l1 + (l2 - l1) * t
        s = s1 + (s2 - s1) * t
        r, g, b = colorsys.hls_to_rgb(h, l, s)
        return f'#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}'

    # ── v1.8.26 统一日志规范：括号标志分词着色 ────────────────
    # 级别表与 tasks/base.py LEVEL_PREFIXES 对齐。任务日志带 [任务名] 前缀
    # （如 "[异界迷宫] [警告] xxx"），旧 startswith 匹配不上导致任务日志
    # 全部无色 → 改为提取消息头部全部 [括号] 标志分词判定。
    _LOG_LEVEL_TAGS = {
        '错误': 'err', '警告': 'warn', '成功': 'success', '点击': 'click',
        '数据': 'data', '配置': 'config', '调试': 'dim_info', '跳过': 'dim_info',
    }
    _LOG_INFO_DIM_KWS = ('点击', '导航', '尝试', '等待', '检测', 'ESC',
                         '退出键', '关闭', '返回', '切换', '恢复', '比例→',
                         'OCR', '调试截图')
    _LOG_INFO_DATA_KWS = ('坐标', '画面', '分辨率', 'OCR记忆', '赛季关卡',
                          '当前关卡', '轮 |', '推图轮数')

    @classmethod
    def _pick_log_tag(cls, msg: str):
        """按消息头部 [括号] 标志分词选着色 tag（v1.8.26 规范）。

        优先级：分隔线 > 错误 > 警告 > 成功 > 点击 > 数据 > 配置 > 调试/跳过
        > [信息]（按关键词细分 dim/data/核心） > 特殊内容（推进/赛季/暂停）。
        """
        # 第一优先：分隔线
        if msg.startswith('─') or msg.startswith('═') or msg.startswith('='):
            return 'sep'
        # 头部连续 [括号] 组 → 提取标志（兼容 [任务名][级别] 两种顺序）
        import re
        m = re.match(r'^((?:\[[^\]]+\]\s*)+)', msg)
        tokens = re.findall(r'\[([^\]]+)\]', m.group(1)) if m else []
        for t in tokens:
            tag = cls._LOG_LEVEL_TAGS.get(t)
            if tag:
                return tag
        if '信息' in tokens:
            if any(kw in msg for kw in cls._LOG_INFO_DIM_KWS):
                return 'dim_info'
            if any(kw in msg for kw in cls._LOG_INFO_DATA_KWS):
                return 'data_info'
            return 'ok'
        # 无级别标志：特殊内容匹配（兼容 bot 层无标志消息）
        if '>>' in msg or '推进' in msg:
            return 'progress'
        if '赛季' in msg:
            return 'season'
        if '已暂停' in msg:
            return 'warn'
        if '恢复运行' in msg:
            return 'ok'
        if '当前阶段' in msg or msg.startswith('第 '):
            return 'phase'
        return None

    def _refresh_timer(self):
        """每秒刷新计时器（只更新任务行指标与时钟，v1.8.80 状态栏已删）"""
        try:
            # 更新任务行运行指标（HH:MM:SS；v1.8.88 起不再显示轮次）
            if self._running and self._running_inst_id:
                row = self._task_rows.get(self._running_inst_id)
                if row:
                    el = int(time.time() - self._running_start_time)
                    row['lbl_sum'].configure(
                        text=f'{el // 3600:02d}:{el % 3600 // 60:02d}:'
                             f'{el % 60:02d}'
                    )

            # 更新时钟
            clock_txt = time.strftime('%H:%M')
            self._lbl_clock.configure(text=clock_txt)
        except Exception as e:
            self._append_log(f'[错误] 定时器失败 | {e}')
        self._timer_id = self.after(1000, self._refresh_timer)

    # ────────────────────────────────────────────────────────────────
    #  状态更新

    def _update_status(self, **kwargs):
        self._status.update(kwargs)
        # 入队，由批量定时器统一刷新GUI（避免 after(0) 洪水）
        # 注意：传入 self._status 的副本，保证 _flush 拿到的是完整状态
        self._log_queue.put(('__status__', dict(self._status)))

    def _refresh_ui(self, data=None):
        """v1.8.80：头部状态栏已删 → 状态刷新变空操作（保留方法兼容旧调用）"""
        return

    # ────────────────────────────────────────────────────────────────
    #  控制按钮

    def _on_start(self):
        if not self._bot_ready:
            self._append_log('[信息] OCR 引擎初始化中，请稍候...')
            return
        if self._running:
            self._append_log('[F11] 已在运行中')
            return
        # 等旧 bot 线程完全结束
        if self._bot_thread is not None and self._bot_thread.is_alive():
            self._append_log('[警告] 上一次任务尚未结束，请稍等...')
            return
        # 空清单校验（v1.5.1）：GUI 可见的启用任务 = enabled 且在 TASK_CLASSES 中
        if not TaskRegistry.get_enabled_instances():
            self._append_log('[信息] 未勾选任何任务，请在左侧清单中勾选')
            return
        # 清空队列
        while not self._log_queue.empty():
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                break

        # 更新 bot 配置（bot 已在后台初始化完毕）
        self._bot.log = self._append_log
        self._bot.set_status = self._update_status
        self._bot.config.update({
            'log_callback': self._append_log,
            'status_callback': self._update_status,
            'verbose': False,
        })
        # v1.5.2 修复：清残留停止/暂停事件，否则 TaskScheduler 立即 break 返回 {}
        if hasattr(self._bot, '_stop_event'):
            self._bot._stop_event.clear()
        if hasattr(self._bot, '_pause'):
            self._bot._pause.set()  # 解除暂停（v1.5.1 也可能残留暂停状态）

        self._running = True
        self._status['running'] = True
        self._refresh_timer()
        self._btn_main.configure(text='■ 停止', fg_color=DANGER,
                                hover_color='#f87171', text_color='white',
                                command=self._on_stop)

        self._log_text.configure(state='normal')
        self._log_text.delete('1.0', 'end')
        self._log_text.configure(state='disabled')

        # 启动主循环
        self._bot_thread = threading.Thread(
            target=self._bot_thread_wrapper, daemon=True
        )
        self._bot_thread.start()
        self._refresh_timer()

    def _bot_thread_wrapper(self):
        # 复位所有任务行 → idle（切主线程）
        self._post_ui(self._reset_task_rows)

        # 构建调度器：按实例清单顺序执行启用任务（v1.5.3）
        sched = TaskScheduler(
            self._bot,
            logger=self._append_log,
            status_cb=self._on_task_status,
        )
        for inst in TaskRegistry.get_enabled_instances():
            sched.add(inst)

        results = sched.run_all()   # 阻塞直到全部结束/停止
        self._append_log(f'[信息] 任务汇总: {zh_results(results)}')
        self._running = False
        self._post_ui(self._reset_buttons)

    # ── 停止快捷键 F12（v2.2.0）───────────────────────────────────

    def _bind_global_hotkeys(self):
        """注册**系统级**停止快捷键 F12。

        为什么不用 Tk 的 `self.bind('<F12>')`：Tk 绑定只在**工具窗口有焦点**
        时触发，而运行时焦点在游戏上（工具还会主动把游戏切到前台）→
        键等于白按。故走 `keyboard` 库的系统级钩子（引擎层 `loop.py` 亦用
        它注册 F12）。

        不抑制按键（`suppress` 默认 False）：游戏自身仍能收到 F12，
        不改变游戏行为。
        """
        self._hotkey_f12 = None
        try:
            import keyboard
            self._hotkey_f12 = keyboard.add_hotkey(
                'f12', self._on_hotkey_stop)
            self._append_log('[配置] 停止快捷键 F12 已就绪（全局生效，'
                             '运行中随时可按）')
        except Exception as e:
            # 注册失败不影响主流程：按钮停止仍然可用
            self._append_log(
                f'[警告] F12 热键注册失败（{e}）——请改用「■ 停止」按钮')

    def _on_hotkey_stop(self):
        """F12 触发停止。

        ⚠️ 本方法可能在 `keyboard` 库的**后台线程**被调用 → 绝不直接操作
        Tk（项目铁律：后台线程禁止调 after/控件）→ 统一投递主线程。
        """
        if getattr(self, '_closing', False):
            return
        self._post_ui(self._do_hotkey_stop)

    def _do_hotkey_stop(self):
        """主线程执行：未运行时忽略，避免空按刷日志。"""
        # 关闭中：已入队的回调也要丢弃 —— 此时窗口可能已销毁
        # （`_post_ui` 只拦"关闭后新入队"，拦不住"关闭前已入队"）
        if getattr(self, '_closing', False):
            return
        if not getattr(self, '_running', False):
            return
        self._append_log('[信息] 收到 F12 停止快捷键')
        self._on_stop()

    def _on_stop(self):
        self._running = False
        if self._bot:
            self._bot.stop()
        self._reset_buttons()
        self._update_status(running=False)
        self._append_log('[停止]')

    def _reset_buttons(self):
        """重置控制按钮到初始状态（▶ 运行）"""
        self._btn_main.configure(text='▶ 运行', fg_color=DANGER,
                                hover_color='#f87171', text_color='white',
                                command=self._on_start)
        # 停止计时器刷新
        if self._timer_id:
            self.after_cancel(self._timer_id)
            self._timer_id = None

    def _on_closing(self):
        """关闭窗口：保存几何 → 停止bot → 关闭日志 → 销毁窗口 → 兜底强制退出"""
        # v2.1.17：标记关闭中 → _post_ui 丢弃后台线程后续投递（避免
        # mainloop 退出后仍向已销毁窗口投递任务）
        self._closing = True
        try:
            self.update_idletasks()
            self._save_gui_setting('geometry', self.geometry())   # v1.8.16
        except Exception:
            pass
        try:
            if self._bot:
                self._bot.stop()
        except Exception:
            pass
        # v2.2.0：注销 F12 全局热键（避免钩子残留）
        try:
            if getattr(self, '_hotkey_f12', None) is not None:
                import keyboard
                keyboard.remove_hotkey(self._hotkey_f12)
                self._hotkey_f12 = None
        except Exception:
            pass
        try:
            if hasattr(self, '_log_file'):
                self._log_file.close()
        except Exception:
            pass
        # 强制退出：3秒后如果窗口还没关闭，直接 exit
        def _force_exit():
            import os
            os._exit(0)
        self.after(3000, _force_exit)
        try:
            self.destroy()
        except Exception:
            pass

# ═══════════════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════════════

def _check_admin():
    """检查管理员权限，返回True/False"""
    try:
        import ctypes
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:              # v2.1.27（P2-9）：裸 except 会吞掉
        is_admin = False           # KeyboardInterrupt / SystemExit，收窄
    return is_admin

if __name__ == '__main__':
    # v2.2.23：命令行自检/更新（不打开界面，便于排查 exe 的 HTTPS/证书问题）
    if '--check-update' in sys.argv or '--update' in sys.argv:
        # 打包版是**无控制台**程序 → print 看不到，故同时落盘到
        # exe 同目录的 _update_check.log（便于用户/排查时取证）
        import contextlib
        import io as _io
        from afk_bot import updater as _upd
        _args = ['--update'] if '--update' in sys.argv else ['--check']
        _buf = _io.StringIO()
        with contextlib.redirect_stdout(_buf):
            _rc = _upd.main(_args)
        _text = _buf.getvalue()
        try:
            print(_text)
        except Exception:
            pass
        try:
            _log = os.path.join(os.path.dirname(os.path.abspath(__file__))
                                if not getattr(sys, 'frozen', False)
                                else os.path.dirname(sys.executable),
                                '_update_check.log')
            with open(_log, 'w', encoding='utf-8') as f:
                f.write(_text)
        except Exception:
            pass
        raise SystemExit(_rc)

    ctk.set_appearance_mode('light')
    ctk.set_default_color_theme('blue')

    app = App()
    app.protocol('WM_DELETE_WINDOW', app._on_closing)
    app.mainloop()
