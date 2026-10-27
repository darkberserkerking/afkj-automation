"""B3/B4：真实下载 Release 附件 + 替换脚本沙盒验证（不碰真 exe）。

运行：D:/anaconda3/envs/afk_gpu/python.exe _test_updater_e2e.py
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = r'C:\Users\x9502\WorkBuddy\20260320211537'
sys.path.insert(0, ROOT)
import afk_bot.config as cfg       # noqa: E402
from afk_bot import updater as U   # noqa: E402

D = Path(ROOT) / 'dist_swap_test'
D.mkdir(exist_ok=True)

print('=== B3. 真实下载 Release 附件 ===')
cfg.VERSION = 'v2.2.28'            # 伪造旧版本，让更新器认为有新版
info = U.check_latest()
print(f'  发现: {info.tag} / {info.asset_name} / {info.size / 1048576:.1f}MB')
t0 = time.time()
p = U.download_asset(info, dest_dir=D)
dt = time.time() - t0
size = p.stat().st_size
magic = p.read_bytes()[:2]
print(f'  下载: {p.name}  {size} 字节  用时 {dt:.1f}s  '
      f'速度 {size / dt / 1048576:.1f}MB/s')
print(f'  校验: 大小与接口一致={size == info.size}  PE 头(MZ)={magic == b"MZ"}')

print()
print('=== B4. 替换脚本沙盒测试（假 exe，真实走 等待→覆盖→重启→自删）===')
cur = D / 'current.exe'
new = D / 'new.exe'
shutil.copyfile(sys.executable, cur)
shutil.copyfile(sys.executable, new)
b = bytearray(new.read_bytes())
b[-1] = (b[-1] + 1) % 256          # 制造一个字节差异，便于比对
new.write_bytes(bytes(b))
h_cur = hashlib.md5(cur.read_bytes()).hexdigest()
h_new = hashlib.md5(new.read_bytes()).hexdigest()
print(f'  覆盖前: current={h_cur[:12]}  new={h_new[:12]}  是否相同={h_cur == h_new}')

holder = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
print(f'  占位进程 PID={holder.pid}（模拟程序正在运行，脚本需等它退出）')
script = U.launch_updater(new, cur, pid=holder.pid)
print(f'  已生成并启动脚本: {script.name}')
holder.wait(timeout=30)

ok = False
for _ in range(25):
    time.sleep(1)
    if cur.exists() and hashlib.md5(cur.read_bytes()).hexdigest() == h_new:
        ok = True
        break
h_after = hashlib.md5(cur.read_bytes()).hexdigest() if cur.exists() else ''
print(f'  覆盖后: current={h_after[:12]}')
print(f'  {"✅" if ok else "❌"} 已被新版替换（等待原进程退出后才覆盖）')
print(f'  {"✅" if not new.exists() else "❌"} 新版文件已 move 走（源文件消失）')
time.sleep(2)
print(f'  {"✅" if not script.exists() else "❌"} 脚本已自删')

print()
print('=== 清理（仅测试目录）===')
for f in D.iterdir():
    try:
        f.unlink()
    except Exception as e:
        print('  跳过', f.name, e)
try:
    D.rmdir()
    print('  ✅ dist_swap_test 已清理')
except Exception as e:
    print('  目录保留:', e)
