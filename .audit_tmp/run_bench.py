# -*- coding: utf-8 -*-
import os, sys, time, statistics, subprocess, random

PY = r"D:\mycode\python\my_blog\.venv\Scripts\python.exe"
TMP = os.path.dirname(os.path.abspath(__file__))
datafile = os.path.join(TMP, "big.txt")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

# ---------- in-process parse benchmark, stdin = real file ----------
bench = r"""
import sys, time, statistics
mode = sys.argv[1]
f = open(r"%s", "r", encoding="utf-8")
sys.stdin = f

def run_input_iter():
    s = 0
    for line in sys.stdin:
        a, b = line.split(); s += int(a) + int(b)
    return s

def run_input_call():
    s = 0
    for _ in range(200000):
        a, b = input().split(); s += int(a) + int(b)
    return s

def run_readline():
    s = 0
    while True:
        line = sys.stdin.readline()
        if not line: break
        a, b = line.split(); s += int(a) + int(b)
    return s

def run_readsplit():
    f.seek(0)
    d = f.buffer.read().split() if hasattr(f, 'buffer') else f.read().split()
    s = 0
    for i in range(0, len(d), 2):
        s += int(d[i]) + int(d[i + 1])
    return s

fn = {"input_iter": run_input_iter, "input_call": run_input_call,
      "readline": run_readline, "readsplit": run_readsplit}[mode]

ts = []
for _ in range(7):
    f.seek(0)
    t0 = time.perf_counter()
    v = fn()
    ts.append(time.perf_counter() - t0)
print("%%s median %%.4f all %%s" %% (mode, statistics.median(ts), ["%%.3f" %% x for x in ts]))
""" % datafile.replace("\\", "\\\\")

p = os.path.join(TMP, "bench.py")
open(p, "w", encoding="utf-8", newline="\n").write(bench)
for mode in ["input_iter", "input_call", "readline", "readsplit"]:
    r = subprocess.run([PY, p, mode], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    print("1.3 in-process", r.stdout.decode().strip(), r.stderr.decode().strip()[:100])

# ---------- recursion n=35, isolated, 3 runs of 3 ----------
rec = """import time
def f(n):
    return 1 if n <= 1 else f(n-1) + f(n-2)
best = []
for _ in range(3):
    t0 = time.perf_counter(); v = f(35); best.append(time.perf_counter()-t0)
print(" ".join("%.2f" % x for x in best), v)
"""
p2 = os.path.join(TMP, "rec.py")
open(p2, "w", encoding="utf-8", newline="\n").write(rec)
for k in range(2):
    r = subprocess.run([PY, p2], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    print("D-1 recursion n=35 run%d:" % k, r.stdout.decode().strip())
