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
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = r"D:/anaconda3/envs/afk_gpu/python.exe"
QUARANTINE = os.path.join(ROOT, 'tests', 'quarantine.txt')

# 脚本 → (层级集合, 是否独占(需串行))
# L1 本地/提交级：<60s；L2 PR 级：全逻辑覆盖；L3 发布级：含矩阵与真实时间
SUITES = {
    'tests/test_task_scheduler_smoke.py': ({1, 2, 3}, False),
    'tests/test_navigator_smoke.py': ({1, 2, 3}, False),
    'tests/test_home_task_dryrun.py': ({1, 2, 3}, False),
    'tests/test_gui_drawer_smoke.py': ({1, 2, 3}, True),      # Tk 独占
    'tests/test_goddess_trial_dryrun.py': ({2, 3}, False),
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
    ('tasks/labyrinth.py', ['tests/test_labyrinth_v1820.py',
                            'tests/test_labyrinth_nav_dryrun.py',
                            'tests/test_labyrinth_full_round_dryrun.py',
                            'tests/test_orb_reset_on_end.py',
                            'tests/test_imprint_pick_blacklist.py']),
    ('afk_bot/auto_farm/formation.py',
     ['tests/test_challenge_btn_verify.py']),
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
                          'tests/test_gui_drawer_smoke.py']),
    ('afk_gui.py', ['tests/test_gui_drawer_smoke.py',
                    'tests/test_stop_hotkey.py']),
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
      'tests/test_global_nav_matrix.py']),
    ('tasks/friend_gift.py', ['tests/test_friend_gift_dryrun.py']),
    ('tasks/dream_realm.py', ['tests/test_dream_realm_dryrun.py']),
    ('tasks/game_control.py', ['tests/test_game_control_dryrun.py']),
    ('tasks/game_launcher.py', ['tests/test_game_control_dryrun.py']),
    ('tasks/nav_planner.py', ['tests/test_nav_planner.py',
                              'tests/test_nav_scene.py']),
    ('tasks/base.py#quit', ['tests/test_quit_dialog.py']),
    ('tasks/menu_nav.py', ['tests/test_daily_quest_dryrun.py',
                           'tests/test_friend_gift_dryrun.py']),
    ('tasks/scheduler.py#order',
     ['tests/test_boundary_order.py']),
    ('tasks/catalog.py', ['tests/test_task_scheduler_smoke.py',
                          'tests/test_gui_drawer_smoke.py']),
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
    parallel = [s for s in suites if not SUITES[s][1]]
    serial = [s for s in suites if SUITES[s][1]]
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        futs = {ex.submit(run_one, s, a.real): s for s in parallel}
        for s in serial:      # GUI/Tk 独占
            results.append(run_one(s, a.real))
        for fu, s in futs.items():
            results.append(fu.result())

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
