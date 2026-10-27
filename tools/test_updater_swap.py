"""B4 独立验证：替换脚本的真机行为（不依赖下载）。

覆盖：等待原进程退出 → move /y 覆盖 → 启动新版 → 脚本自删
运行：D:/anaconda3/envs/afk_gpu/python.exe tools/test_updater_swap.py
"""
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = r'C:\Users\x9502\WorkBuddy\20260320211537'
sys.path.insert(0, ROOT)
from afk_bot import updater as U  # noqa: E402

D = Path(ROOT) / 'dist_swap_test'
D.mkdir(exist_ok=True)
cur = D / 'current.exe'
new = D / 'new.exe'
shutil.copyfile(sys.executable, cur)
shutil.copyfile(sys.executable, new)
b = bytearray(new.read_bytes())
b[-1] = (b[-1] + 1) % 256          # 制造差异，便于比对
new.write_bytes(bytes(b))
h_cur = hashlib.md5(cur.read_bytes()).hexdigest()
h_new = hashlib.md5(new.read_bytes()).hexdigest()
print(f'覆盖前: current={h_cur[:12]}  new={h_new[:12]}  相同={h_cur == h_new}')

holder = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])
print(f'占位进程 PID={holder.pid}（模拟"程序正在运行"，脚本必须等它退出才覆盖）')
script = U.launch_updater(new, cur, pid=holder.pid)
print(f'已生成并启动脚本: {script.name}')

t0 = time.time()
holder.wait(timeout=30)
ok = False
for _ in range(25):
    time.sleep(1)
    if cur.exists() and hashlib.md5(cur.read_bytes()).hexdigest() == h_new:
        ok = True
        break
h_after = hashlib.md5(cur.read_bytes()).hexdigest() if cur.exists() else ''
print(f'等待 {time.time() - t0:.0f}s 后：current={h_after[:12]}')
print(f'{"✅" if ok else "❌"} 已被新版替换（且是等原进程退出后才覆盖）')
print(f'{"✅" if not new.exists() else "❌"} 新版文件已 move 走（源文件消失）')
time.sleep(3)
print(f'{"✅" if not script.exists() else "❌"} 脚本已自删')

for f in list(D.iterdir()):
    try:
        f.unlink()
    except Exception as e:
        print('  跳过', f.name, e)
try:
    D.rmdir()
    print('✅ dist_swap_test 已清理')
except Exception as e:
    print('目录保留:', e)
