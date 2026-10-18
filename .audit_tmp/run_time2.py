# -*- coding: utf-8 -*-
import os, sys, time, subprocess, statistics, tempfile

PY = r"D:\mycode\python\my_blog\.venv\Scripts\python.exe"
TMP = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

def e2e(body, stdin="", reps=5, tmpname="e2e.py"):
    p = os.path.join(TMP, tmpname)
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter()
        subprocess.run([PY, p], input=stdin.encode(), stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, env=env)
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts), ts

# ---- D-1 n=10^7 end to end ----
src = open(os.path.join(TMP, "sol", "D_1.py"), encoding="utf-8").read()
t, ts = e2e(src, "10000000\n", 5, "d1.py")
print("D-1 e2e n=1e7 median %.2fs all=%s" % (t, ["%.2f" % x for x in ts]))

# ---- D-1 naive recursion n=35 end to end ----
rec = """import time
def f(n):
    return 1 if n <= 1 else f(n-1) + f(n-2)
t0 = time.perf_counter(); v = f(35); print("%.3f %d" % (time.perf_counter()-t0, v))
"""
t, ts = e2e(rec, "", 3, "d1rec.py")
print("D-1 recursion n=35 e2e median %.2fs all=%s (includes startup)" % (t, ["%.2f" % x for x in ts]))

# ---- H-1 n=10000 both versions end to end ----
h1 = """import sys, time
def solve(guard):
    data = sys.stdin.buffer.read().split()
    n = int(data[0]); nums = list(map(int, data[1:1+n]))
    s = set(nums); best = 0
    for x in s:
        if guard and x - 1 in s: continue
        y = x
        while y + 1 in s: y += 1
        if y - x + 1 > best: best = y - x + 1
    return best
print(solve(%s))
"""
inp = "10000\n" + " ".join(map(str, range(1, 10001))) + "\n"
tg, tg_all = e2e(h1 % "True", inp, 5, "h1g.py")
tn, tn_all = e2e(h1 % "False", inp, 3, "h1n.py")
print("H-1 e2e guard %.3fs %s | noguard %.2fs %s | ratio %.0fx"
      % (tg, ["%.3f" % x for x in tg_all], tn, ["%.2f" % x for x in tn_all], tn / tg))

# ---- M-2 n=1000 end to end ----
src = open(os.path.join(TMP, "sol", "M_2.py"), encoding="utf-8").read()
t, ts = e2e(src, "1000 3\n", 5, "m2.py")
print("M-2 e2e n=1000 median %.3fs all=%s" % (t, ["%.3f" % x for x in ts]))

# ---- 1.3 parsing: 200k lines, end-to-end from a file via stdin ----
datafile = os.path.join(TMP, "big.txt")
with open(datafile, "w", encoding="utf-8", newline="\n") as f:
    for i in range(200000):
        f.write("%d %d\n" % (i, i + 1))
variants = {
    "input()": """import sys
s = 0
for line in sys.stdin:
    a, b = line.split(); s += int(a) + int(b)
print(s)
""",
    "readline()": """import sys
s = 0
while True:
    line = sys.stdin.readline()
    if not line: break
    a, b = line.split(); s += int(a) + int(b)
print(s)
""",
    "read().split()": """import sys
d = sys.stdin.buffer.read().split()
s = 0
for i in range(0, len(d), 2):
    s += int(d[i]) + int(d[i+1])
print(s)
""",
}
for name, body in variants.items():
    p = os.path.join(TMP, "p.py")
    open(p, "w", encoding="utf-8", newline="\n").write(body)
    ts = []
    for _ in range(5):
        with open(datafile, "rb") as fin:
            t0 = time.perf_counter()
            subprocess.run([PY, p], stdin=fin, stdout=subprocess.DEVNULL, env=env)
            ts.append(time.perf_counter() - t0)
    print("1.3 e2e %-16s median %.3fs all=%s" % (name, statistics.median(ts), ["%.3f" % x for x in ts]))

# ---- 1.6 module shadowing ----
d = os.path.join(TMP, "shadow")
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "random.py"), "w", encoding="utf-8").write("print('local random')\n")
r = subprocess.run([PY, "-c", "import random; print(random.__file__)"], cwd=d,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
print("1.6 shadow import random ->", r.stdout.decode().strip().replace("\r\n", " | "),
      "| err:", r.stderr.decode().strip()[:80])
r2 = subprocess.run([PY, "-c", "print('ok')"], cwd=d, stdout=subprocess.PIPE, env=env)
print("1.6 from shadow dir, plain run ->", r2.stdout.decode().strip()[:40])
