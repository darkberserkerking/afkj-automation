# -*- coding: utf-8 -*-
"""检查更新：诊断信息保真 + 诊断日志落盘（v2.3.50）。

真机 23:49：exe 报「网络连接被中断（TLS 握手未完成）」，
但**看不到** `_default_fetch` 拼好的「已尝试：…」诊断。

根因：`_fetch_with_retry` 的 `except Exception` 把 `_default_fetch` 抛的
`UpdateError` 当普通网络错误**二次包装**，`_friendly_net_hint` 又按关键字
翻译 → 诊断被覆盖。

本测试锁死：
  ① 失败时错误信息必须保留「已尝试：…」且带诊断日志路径；
  ② 诊断日志必须落盘，并记录 frozen 状态 / env proxy / 各路径结果
     （exe vs 源码对比就靠这几行）。

运行：D:/anaconda3/envs/afk_gpu/python.exe tests/test_updater_diag.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from afk_bot import updater as U  # noqa: E402

FAILED = []
PASSED = [0]


def check(name, cond, extra=''):
    ok = bool(cond)
    if not ok:
        FAILED.append(name)
    else:
        PASSED[0] += 1
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {extra}", flush=True)




# 真机：exe 报「网络连接被中断」但看不到「已尝试：…」——
# 因 _fetch_with_retry 的 except Exception 把 _default_fetch 抛的
# UpdateError 二次包装，_friendly_net_hint 又按关键字覆盖了诊断。
print('── 诊断信息保真（v2.3.50）──')
import os as _os

_old_api = U.API_LATEST
_old_ports = U._LOCAL_PROXY_PORTS
_old_cache = U._LOCAL_PROXY_CACHE
_diag_log = U._diag_path()
try:
    for _k in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
               'http_proxy', 'https_proxy', 'all_proxy'):
        _os.environ.pop(_k, None)
    U.API_LATEST = 'https://afkj-invalid-host-for-test.invalid/x'
    U._LOCAL_PROXY_PORTS = (1, 2, 3)
    U._LOCAL_PROXY_CACHE = None
    if _os.path.isfile(_diag_log):
        _os.remove(_diag_log)

    _msg = ''
    try:
        U.check_latest(token=None, timeout=2)
        check('1a 不可达主机应抛 UpdateError', False, '未抛异常')
    except U.UpdateError as e:
        _msg = str(e)
        check('1a 不可达主机抛 UpdateError', True, _msg[:50])

    check('1b 错误信息保留「已尝试」诊断', '已尝试' in _msg, _msg[:80])
    check('1c 错误信息含诊断日志路径', '诊断日志' in _msg)
    check('1d 不泄露底层 _ssl.c 细节', '_ssl.c' not in _msg)
    check('1e 诊断日志已落盘并记录 frozen 状态',
          _os.path.isfile(_diag_log)
          and 'frozen=' in open(_diag_log, encoding='utf-8').read(),
          _diag_log)
    _diag_txt = (open(_diag_log, encoding='utf-8').read()
                 if _os.path.isfile(_diag_log) else '')
    check('1f 诊断日志记录了 env proxy 与各路径结果',
          'env proxy' in _diag_txt and '系统代理' in _diag_txt,
          _diag_txt.splitlines()[:2])
finally:
    U.API_LATEST = _old_api
    U._LOCAL_PROXY_PORTS = _old_ports
    U._LOCAL_PROXY_CACHE = _old_cache

print('\n' + '=' * 56)
if FAILED:
    print(f'❌ 失败 {len(FAILED)} 项：')
    for n in FAILED:
        print('   -', n)
    sys.exit(1)
print(f'✅ 全部通过（{PASSED[0]}/{PASSED[0]}）')
