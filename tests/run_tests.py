# -*- coding: utf-8 -*-
"""统一测试调度脚本（v1.9.2 测试流程优化 · 措施 2/3/5/6）。

解决的问题：全量串行 25~30 分钟，且每次回归靠人工挑脚本。

能力：
  --level 1|2|3        分层分级（本地/提交 · PR · 发布）
  --changed            按 git 变更范围增量筛选用例（措施 3）
  --jobs N             文件级并行（默认 4；Tk/GUI 类自动串行）
  --shard i/n          文件级分片（CI 多机/多进程用，措施 6）
  --retry N            失败重试（flaky 隔离，措施 5）
  --quarantine         失败用例写入 tests/quarantine.txt 并在后续默认跳过
  --slow-report        输出各脚本耗时与慢用例 Top20（措施 4 画像）
  --real               AFK_TEST_FAST=0，跑真实时间（发布级兜底）

用法：
    python tests/run_tests.py --level 1
    python tests/run_tests.py --level 2 --jobs 4
    python tests/run_tests.py --level 3 --real
    python tests/run_tests.py --changed --level 2
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# v2.4.9：改用当前解释器——旧硬编码路径在 Anaconda 迁移后必然失效
PY = sys.executable
QUARANTINE = os.path.join(ROOT, 'tests', 'quarantine.txt')

# v2.4.16：Tk 用例兜底探测（SUITES 的独占标记靠人工维护，漏标就会抢 Tk）
TK_IMPORT_RE = re.compile(
    r'^\s*(?:import|from)\s+(?:customtkinter|tkinter)\b', re.M)

# 脚本 → (层级集合, 是否独占(需串行))
# L1 本地/提交级：<60s；L2 PR 级：全逻辑覆盖；L3 发布级：含矩阵与真实时间
SUITES = {
    'tests/test_task_scheduler_smoke.py': ({1, 2, 3}, False),
    'tests/test_navigator_smoke.py': ({1, 2, 3}, False),
    'tests/test_home_task_dryrun.py': ({1, 2, 3}, False),
    'tests/test_gui_drawer_smoke.py': ({1, 2, 3}, True),      # Tk 独占
    'tests/test_task_description_consistency.py': ({1, 2, 3}, False),  # 全量文案一致性
    'tests/test_season_equipment_dryrun.py': ({1, 2, 3}, False),
    'tests/test_season_class_tpl_inner.py': ({1, 2, 3}, False),   # v2.4.5 圆标内缩模板
    'tests/test_season_click_cap.py': ({1, 2, 3}, False),       # v2.4.8 取消点击硬上限
    'tests/test_portrait_result_close.py': ({1, 2, 3}, False),
    'tests/test_artistic_text_fallback.py': ({1, 2, 3}, False),
    'tests/test_res_path.py': ({1, 2, 3}, False),
    'tests/test_chest_region.py': ({1, 2, 3}, False),
    'tests/test_switch_btn_region.py': ({1, 2, 3}, False),
    'tests/test_switch_btn_match.py': ({1, 2, 3}, False),   # v2.4.20 真实算法端到端（合成三布局）
    'tests/test_updater_net_retry.py': ({1, 2, 3}, False),
    'tests/test_data_dir.py': ({1, 2, 3}, False),               # v2.4.12 数据目录与旧文件迁移
    'tests/test_updater_atomic_download.py': ({1, 2, 3}, False),
    'tests/test_update_flow.py': ({1, 2, 3}, False),            # v2.4.15 源码/exe 更新链路
    'tests/test_run_tests_scheduler.py': ({1, 2, 3}, False),    # v2.4.16 并行/Tk 两阶段隔离
    'tests/test_packaging_deps.py': ({1, 2, 3}, False),         # v2.4.17 Tcl/Tk DLL 版本钉死
    'tests/test_infra_speedup.py': ({1, 2, 3}, False),          # v2.4.22 roi/模板缓存/两段式
    'tests/test_roi_safety.py': ({1, 2, 3}, False),             # v2.4.22 ROI 未命中自动全屏兜底
    'tests/test_updater_lock_retry.py': ({1, 2, 3}, False),     # v2.4.18 目标被占用时重试覆盖
    'tests/test_updater_diag.py': ({1, 2, 3}, False),
    'tests/test_status_display_chain.py': ({1, 2, 3}, True),  # Tk 独占
    'tests/test_status_display.py': ({1, 2, 3}, False),
    'tests/test_startup_loading.py': ({1, 2, 3}, False),
    'tests/test_startup_bind_hwnd.py': ({1, 2, 3}, False),   # v2.4.4 冷启动句柄绑定
    'tests/test_battle_count_consistency.py': ({1, 2, 3}, False),
    'tests/test_rec_empty_switch.py': ({1, 2, 3}, False),   # v2.4.3 推荐阵容为空
    'tests/test_auto_farm_stop_residue.py': ({1, 2, 3}, False),
    'tests/test_goddess_trial_dryrun.py': ({2, 3}, False),
    'tests/test_guild_battle_dryrun.py': ({2, 3}, False),
    'tests/test_tianqiong_trial_dryrun.py': ({2, 3}, False),
    'tests/test_tianqiong_challenge_conf.py': ({1, 2, 3}, False),   # v2.4.6 挑战低分漏检
    'tests/test_threshold_consistency.py': ({1, 2, 3}, False),  # v2.4.7 阈值一致性审计
    'tests/test_guild_result_matrix.py': ({1, 2, 3}, False),
    'tests/test_bug_report.py': ({1, 2, 3}, True),
    'tests/test_afk_limit_confirm.py': ({1, 2, 3}, False),
    'tests/test_updater_verify.py': ({1, 2, 3}, False),
    'tests/test_challenge_btn_verify.py': ({2, 3}, False),
    'tests/test_labyrinth_v1820.py': ({2, 3}, False),
    'tests/test_labyrinth_nav_dryrun.py': ({2, 3}, False),
    'tests/test_global_nav_consistency.py': ({2, 3}, False),
    'tests/test_global_nav_matrix.py': ({3}, False),
    'tests/test_labyrinth_full_round_dryrun.py': ({3}, False),
    # v1.9.69：补齐新任务测试登记（此前改这些文件不触发对应测试）
    'tests/test_arena_dryrun.py': ({2, 3}, False),
    'tests/test_arena_roster.py': ({1, 2, 3}, False),
    'tests/test_daily_quest_dryrun.py': ({2, 3}, False),
    'tests/test_friend_gift_dryrun.py': ({2, 3}, False),
    'tests/test_dream_realm_dryrun.py': ({2, 3}, False),
    'tests/test_game_control_dryrun.py': ({1, 2, 3}, False),
    'tests/test_nav_planner.py': ({1, 2, 3}, False),
    'tests/test_nav_scene.py': ({2, 3}, False),
    'tests/test_quit_dialog.py': ({1, 2, 3}, False),
    'tests/test_boundary_order.py': ({1, 2, 3}, False),
    'tests/test_labyrinth_no_battle.py': ({1, 2, 3}, False),
    'tests/test_nav_skip_when_ready.py': ({1, 2, 3}, False),
    'tests/test_nav_edges_coverage.py': ({1, 2, 3}, False),
    'tests/test_imprint_record_chain.py': ({1, 2, 3}, False),
    'tests/test_imprint_record_fix_verify.py': ({1, 2, 3}, False),
    'tests/test_card_quality.py': ({1, 2, 3}, False),
    'tests/test_card_quality_verify.py': ({1, 2, 3}, False),
    'tests/test_shop_flow.py': ({1, 2, 3}, False),
    # v2.1.22：此前未登记 → 长期静默失败（2.x 段是 v1.8.x 缺陷证伪探针）
    'tests/test_shop_flow_verify.py': ({1, 2, 3}, False),
    'tests/test_imprint_select_verify.py': ({1, 2, 3}, False),
    'tests/test_imprint_select_fix_verify.py': ({1, 2, 3}, False),
    'tests/test_no_battle_abort.py': ({1, 2, 3}, False),
    'tests/test_no_battle_abort_verify.py': ({1, 2, 3}, False),
    'tests/test_menu_nav_shortcut.py': ({1, 2, 3}, False),
    'tests/test_layer_convention.py': ({1, 2, 3}, False),
    'tests/test_layer_convention_verify.py': ({1, 2, 3}, False),
    # v2.1.18：挂机收益假成功 + 停止即时性
    'tests/test_idle_reward_claim.py': ({1, 2, 3}, False),
    # v2.1.21：结束探索 → 烙印库存镜像清空
    'tests/test_orb_reset_on_end.py': ({1, 2, 3}, False),
    # v2.1.23：烙印选卡黑名单「同卡」窗口
    'tests/test_imprint_pick_blacklist.py': ({1, 2, 3}, False),
    # v2.1.24：导航「规划」日志必须如实（不打印未执行的路径）
    'tests/test_nav_plan_log_honest.py': ({1, 2, 3}, False),
    # v2.1.25：每日任务两级标签检测（日常任务 → 每日任务）
    'tests/test_daily_quest_tab.py': ({1, 2, 3}, False),
    # v2.1.27：日志卫生（P0-1 状态限流 / P0-2 崩溃日志拆分 / P2-8 轮转）
    'tests/test_log_hygiene.py': ({1, 2, 3}, False),
    # v2.2.0：停止快捷键 F12
    'tests/test_stop_hotkey.py': ({1, 2, 3}, False),
    # v2.2.0：使用说明与源码一致性（防手册漂移）
    'tests/test_user_manual_accuracy.py': ({1, 2, 3}, False),
    # v2.2.1：烙印选择界面被误判成胜利弹层（真机 09-12）
    'tests/test_imprint_victory_misjudge.py': ({1, 2, 3}, False),
}

# 变更文件 → 必跑脚本（增量筛选，措施 3）
CHANGE_MAP = [
    # v2.4.22 基础设施提速（dev 分支阶段 1）：
    #   ocr_screen 开放 roi 且缓存键必须含 roi；模板解码缓存；多尺度两段式
    ('afk_bot/infra/screen.py', ['tests/test_infra_speedup.py']),
    ('afk_bot/infra/input.py', ['tests/test_infra_speedup.py',
                                'tests/test_switch_btn_match.py',
                                'tests/test_switch_btn_region.py']),
    ('tasks/labyrinth.py', ['tests/test_labyrinth_v1820.py',
                            'tests/test_labyrinth_nav_dryrun.py',
                            'tests/test_labyrinth_full_round_dryrun.py',
                            'tests/test_orb_reset_on_end.py',
                            'tests/test_imprint_pick_blacklist.py']),
    ('tasks/guild_battle.py', ['tests/test_guild_battle_dryrun.py']),
    ('tasks/tianqiong_trial.py', ['tests/test_tianqiong_trial_dryrun.py',
                                  'tests/test_tianqiong_challenge_conf.py',
                                  'tests/test_threshold_consistency.py']),
    ('afk_bot/auto_farm/loop.py', ['tests/test_afk_limit_confirm.py',
                                   'tests/test_battle_count_consistency.py',
                                   'tests/test_rec_empty_switch.py',
                                   'tests/test_auto_farm_stop_residue.py']),
    ('afk_bot/updater.py', ['tests/test_updater_verify.py']),
    ('afk_bot/auto_farm/formation.py',
     ['tests/test_challenge_btn_verify.py',
      'tests/test_rec_empty_switch.py']),
    ('tasks/global_nav.py', ['tests/test_global_nav_matrix.py',
                             'tests/test_labyrinth_nav_dryrun.py',
                             'tests/test_labyrinth_v1820.py',
                             'tests/test_nav_plan_log_honest.py']),
    ('tasks/screen_registry.py', ['tests/test_global_nav_matrix.py',
                                  'tests/test_labyrinth_v1820.py',
                                  'tests/test_labyrinth_nav_dryrun.py']),
    ('tasks/home_task.py', ['tests/test_home_task_dryrun.py']),
    ('tasks/goddess_trial.py', ['tests/test_goddess_trial_dryrun.py']),
    ('tasks/base.py', ['tests/test_task_scheduler_smoke.py',
                       'tests/test_labyrinth_v1820.py',
                       'tests/test_home_task_dryrun.py',
                       'tests/test_idle_reward_claim.py']),
    ('tasks/auto_farm.py', ['tests/test_idle_reward_claim.py']),
    ('tasks/scheduler.py', ['tests/test_task_scheduler_smoke.py']),
    ('task_registry.py', ['tests/test_task_scheduler_smoke.py',
                          'tests/test_gui_drawer_smoke.py',
                          'tests/test_data_dir.py']),
    ('afk_gui.py', ['tests/test_gui_drawer_smoke.py',
                    'tests/test_stop_hotkey.py',
                    'tests/test_data_dir.py']),
    ('afk_bot/services/navigator.py', ['tests/test_navigator_smoke.py',
                                       'tests/test_global_nav_matrix.py']),
    ('templates/', ['tests/test_labyrinth_v1820.py']),
    # v1.9.69：新任务/新模块的增量映射
    ('tasks/arena.py', ['tests/test_arena_dryrun.py',
                        'tests/test_arena_roster.py']),
    ('tasks/daily_quest.py', ['tests/test_daily_quest_dryrun.py',
                              'tests/test_daily_quest_tab.py']),
    ('tasks/base.py', ['tests/test_log_hygiene.py']),
    ('tasks/screen_registry.py',
     ['tests/test_imprint_victory_misjudge.py',
      'tests/test_global_nav_matrix.py',
      'tests/test_startup_loading.py']),
    ('tasks/friend_gift.py', ['tests/test_friend_gift_dryrun.py']),
    ('tasks/dream_realm.py', ['tests/test_dream_realm_dryrun.py']),
    ('tasks/game_control.py', ['tests/test_game_control_dryrun.py',
                               'tests/test_startup_loading.py',
                               'tests/test_startup_bind_hwnd.py']),
    ('tasks/global_nav.py', ['tests/test_startup_loading.py',
                             'tests/test_global_nav_matrix.py']),
    ('tasks/game_launcher.py', ['tests/test_game_control_dryrun.py',
                                'tests/test_data_dir.py']),
    ('tasks/nav_planner.py', ['tests/test_nav_planner.py',
                              'tests/test_nav_scene.py']),
    ('tasks/base.py#quit', ['tests/test_quit_dialog.py']),
    ('tasks/menu_nav.py', ['tests/test_daily_quest_dryrun.py',
                           'tests/test_friend_gift_dryrun.py']),
    ('tasks/scheduler.py#order',
     ['tests/test_boundary_order.py']),
    ('tasks/catalog.py', ['tests/test_task_scheduler_smoke.py',
                          'tests/test_gui_drawer_smoke.py',
                          'tests/test_task_description_consistency.py']),
    # v2.4.12 运行期数据目录（日志/配置落 %LOCALAPPDATA%）
    ('app_paths.py', ['tests/test_data_dir.py']),
    # v2.4.16 调度器自身改动（两阶段隔离 / Tk 探测）
    ('tests/run_tests.py', ['tests/test_run_tests_scheduler.py']),
    # v2.4.17 Tcl/Tk DLL 错配会让 exe 启动即崩
    ('AFKJ自动化工具.spec', ['tests/test_packaging_deps.py',
                             'tests/test_data_dir.py']),
    # v2.3.48 检查更新网络重试；v2.4.15 补源码/exe 更新全链路
    ('afk_bot/updater.py', ['tests/test_updater_net_retry.py',
                            'tests/test_updater_atomic_download.py',
                            'tests/test_update_flow.py',
                            'tests/test_updater_lock_retry.py']),
    # v2.3.47 > 按钮搜索区域（竖屏放宽）
    ('afk_bot/auto_farm/formation.py', ['tests/test_switch_btn_region.py',
                                        'tests/test_switch_btn_match.py']),
    # v2.3.46 挂机宝箱/沙漏区域（横竖屏归一）
    ('tasks/auto_farm.py', ['tests/test_chest_region.py',
                            'tests/test_auto_farm_stop_residue.py']),
    # v2.3.45 资源路径（源码模式模板）
    ('afk_bot/auto_farm/loop.py', ['tests/test_res_path.py']),
    ('templates/', ['tests/test_res_path.py']),
    # v2.3.43 P0 艺术字兜底审计
    ('tasks/arena.py', ['tests/test_artistic_text_fallback.py']),
    # v2.3.41 竖屏结算死循环
    ('afk_bot/auto_farm/battle.py',
     ['tests/test_portrait_result_close.py',
      'tests/test_battle_count_consistency.py']),
    # v2.3.37 赛季装备锻造
    ('tasks/season_equipment.py',
     ['tests/test_season_equipment_dryrun.py',
      'tests/test_season_class_tpl_inner.py',
      'tests/test_season_click_cap.py',
      'tests/test_task_scheduler_smoke.py']),
    ('tasks/screen_registry.py',
     ['tests/test_nav_edges_coverage.py',
      'tests/test_global_nav_consistency.py',
      'tests/test_threshold_consistency.py']),
    # v2.4.7：共享阈值常量或基类默认判据改动 → 必跑全库一致性审计
    ('tasks/text_conf.py', ['tests/test_threshold_consistency.py']),
    ('tasks/base.py', ['tests/test_threshold_consistency.py']),
    ('tasks/friend_gift.py', ['tests/test_friend_gift_dryrun.py',
                              'tests/test_threshold_consistency.py']),
    ('templates/season_class_*.png',
     ['tests/test_season_equipment_dryrun.py']),
    # v2.3.35：任务栏文案一致性——任一任务说明/渲染链路改动都须跑全量校验
    ('afk_gui.py#desc', ['tests/test_task_description_consistency.py']),
    ('tasks/tianqiong_trial.py#desc',
     ['tests/test_task_description_consistency.py']),
]


def git_changed_files() -> list:
    try:
        out = subprocess.run(['git', 'diff', '--name-only', 'HEAD'],
                             cwd=ROOT, capture_output=True, text=True,
                             timeout=10)
        files = [l.strip() for l in out.stdout.splitlines() if l.strip()]
        # 未提交的改动（工作区）
        out2 = subprocess.run(['git', 'diff', '--name-only'],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=10)
        files += [l.strip() for l in out2.stdout.splitlines() if l.strip()]
        return sorted(set(files))
    except Exception:
        return []


def load_quarantine() -> set:
    if not os.path.isfile(QUARANTINE):
        return set()
    with open(QUARANTINE, encoding='utf-8') as f:
        return {l.strip() for l in f if l.strip() and not l.startswith('#')}


def add_quarantine(suite: str) -> None:
    old = load_quarantine()
    old.add(suite)
    with open(QUARANTINE, 'w', encoding='utf-8') as f:
        f.write('# flaky 隔离名单（run_tests.py --quarantine 自动写入）\n')
        for s in sorted(old):
            f.write(s + '\n')


def run_one(suite: str, real: bool = False, timeout: int = 1800) -> dict:
    env = dict(os.environ)
    env['AFK_TEST_FAST'] = '0' if real else '1'
    t0 = time.time()
    try:
        p = subprocess.run([PY, suite], cwd=ROOT, capture_output=True,
                           text=True, timeout=timeout, env=env)
        out = p.stdout + p.stderr
        ok = p.returncode == 0
    except subprocess.TimeoutExpired:
        out, ok = f'TIMEOUT({timeout}s)', False
    dur = time.time() - t0
    # 解析通过数（脚本输出形如 "通过 173/173" 或 "══ ...：通过 19/19 ══"）
    import re
    m = re.findall(r'通过\s*(\d+)\s*/\s*(\d+)', out)
    summary = m[-1] if m else ('?', '?')
    fails = [l for l in out.splitlines() if l.startswith('❌')]
    return {'suite': suite, 'ok': ok, 'dur': dur, 'summary': summary,
            'fails': fails[:5], 'out_tail': out[-400:]}


def detect_tk_suites(suites) -> set:
    """扫描脚本源码，把导入 tkinter/customtkinter 的用例识别为 Tk 用例。

    v2.4.16：SUITES 里的独占标记靠人工维护，漏标就会和 GUI 用例抢 Tk。
    这里按源码兜底——宁可多串行一个，也不要并行抢 Tk 资源。
    """
    found = set()
    for s in suites:
        try:
            with open(os.path.join(ROOT, s), encoding='utf-8',
                      errors='ignore') as f:
                if TK_IMPORT_RE.search(f.read()):
                    found.add(s)
        except OSError:
            continue
    return found


def execute_suites(suites, jobs: int, real: bool, run_fn=None):
    """两阶段执行，返回 (results, 串行执行的脚本列表)。

    v2.4.16 修复（L1 并行既存缺陷）：旧实现把串行批次放在
    `with ThreadPoolExecutor(...)` **块内部**跑，也就是 Tk 用例与并行批次
    同时运行——所谓「独占」并不独占。GUI 用例会因此偶发拿到未完成的布局
    （winfo_width 偏小 / 与另一 Tk 脚本抢窗口），断言随机抖动。

    现在的顺序：
      1. 线程池跑完全部非 Tk 用例，`with` 退出时所有 future 已 join；
      2. 之后才在主线程串行跑 Tk 用例，保证同一时刻只有一个 Tk 进程。
    """
    run_fn = run_fn or run_one
    tk_auto = detect_tk_suites(suites)
    serial = [s for s in suites
              if SUITES.get(s, (set(), False))[1] or s in tk_auto]
    parallel = [s for s in suites if s not in serial]

    results = []
    if parallel:
        with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
            futs = [ex.submit(run_fn, s, real) for s in parallel]
            for fu in futs:
                results.append(fu.result())    # 全部 join 后才离开 with
    for s in serial:                           # 并行池已完全停下
        results.append(run_fn(s, real))
    return results, serial


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--level', type=int, default=2, choices=[1, 2, 3])
    ap.add_argument('--changed', action='store_true', help='按 git 变更增量筛选')
    ap.add_argument('--jobs', type=int, default=4)
    ap.add_argument('--shard', default='', help='i/n 文件级分片')
    ap.add_argument('--retry', type=int, default=0)
    ap.add_argument('--quarantine', action='store_true')
    ap.add_argument('--slow-report', action='store_true')
    ap.add_argument('--real', action='store_true', help='真实时间（不加速）')
    a = ap.parse_args()

    suites = [s for s, (lv, _x) in SUITES.items() if a.level in lv]

    if a.changed:
        changed = git_changed_files()
        picked = set()
        for f in changed:
            for key, ss in CHANGE_MAP:
                if f.startswith(key):
                    picked.update(ss)
        if picked:
            suites = [s for s in suites if s in picked] or suites
            print(f'[增量] 变更 {len(changed)} 个文件 → 命中 {len(suites)} 个测试脚本')
        else:
            print('[增量] 未命中映射，回退到完整层级')

    q = load_quarantine()
    if q:
        before = len(suites)
        suites = [s for s in suites if s not in q]
        if before != len(suites):
            print(f'[隔离] 跳过 {before - len(suites)} 个隔离脚本')

    if a.shard:
        i, n = (int(x) for x in a.shard.split('/'))
        suites = [s for idx, s in enumerate(suites) if idx % n == i - 1]

    print(f'[计划] L{a.level}{" 真实时间" if a.real else " 加速"} '
          f'共 {len(suites)} 个脚本：')
    for s in suites:
        print('   -', s)

    results = []
    t0 = time.time()
    results, serial_used = execute_suites(suites, a.jobs, a.real)
    if serial_used:
        print(f'[独占] {len(serial_used)} 个 Tk 脚本在并行批次结束后串行执行：')
        for s in serial_used:
            print('   -', s)

    # 失败重试（flaky）
    if a.retry:
        for r in list(results):
            if r['ok']:
                continue
            for _ in range(a.retry):
                r2 = run_one(r['suite'], a.real)
                if r2['ok']:
                    r.update(r2)
                    r['suite'] += ' (重试通过)'
                    break
            else:
                if a.quarantine:
                    add_quarantine(r['suite'])
                    r['suite'] += ' [已隔离]'

    total = time.time() - t0
    print('\n' + '=' * 60)
    print(f'{"脚本":<44}{"结果":<8}{"耗时":>8}')
    print('-' * 60)
    for r in sorted(results, key=lambda x: -x['dur']):
        flag = '✅' if r['ok'] else '❌'
        print(f"{r['suite']:<44}{flag:<8}{r['dur']:>7.1f}s  "
              f"{r['summary'][0]}/{r['summary'][1]}")
    print('-' * 60)
    failed = [r for r in results if not r['ok']]
    print(f'合计耗时 {total:.1f}s（并行 jobs={a.jobs}）| '
          f'通过 {len(results) - len(failed)}/{len(results)}')

    if a.slow_report:
        print('\n慢脚本 Top5：')
        for r in sorted(results, key=lambda x: -x['dur'])[:5]:
            print(f"  {r['dur']:>7.1f}s  {r['suite']}")

    for r in failed:
        print(f"\n❌ {r['suite']} 失败明细：")
        for l in r['fails']:
            print('   ', l)
        if not r['fails']:
            print('   ', r['out_tail'][-300:])
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
