# -*- coding: utf-8 -*-
import os, sys, time, random, io, subprocess, statistics

TMP = os.path.dirname(os.path.abspath(__file__))
SOL = os.path.join(TMP, "sol")
sys.path.insert(0, SOL)
import A_1, D_1, M_2, H_1

def call(lab, inp):
    p = os.path.join(SOL, lab.replace("-", "_") + ".py")
    r = subprocess.run([sys.executable, p], input=inp.encode(), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    return r.stdout.decode().replace("\r\n", "\n")

# ---- A-1 corrected reference: min i, then max j with that i ----
def ref_a1(a, t):
    n = len(a)
    for i in range(n):
        for j in range(n - 1, i, -1):
            if a[i] + a[j] == t:
                return (i + 1, j + 1)
    return None
random.seed(7)
bad = 0
for _ in range(2000):
    n = random.randint(2, 9)
    a = sorted(random.randint(-5, 9) for _ in range(n))
    t = random.randint(-8, 16)
    exp = ref_a1(a, t)
    got = call("A-1", "%d %d\n%s\n" % (n, t, " ".join(map(str, a)))).strip()
    want = "-1" if exp is None else "%d %d" % exp
    if got != want:
        bad += 1
        if bad <= 3:
            print("A-1 MISMATCH", a, t, got, want)
print("A-1 piao (min i, max j) 2000 cases, mismatches =", bad)

# ---- timing helpers ----
def med(fn, reps=5):
    ts = []
    for _ in range(reps):
        t0 = time.perf_counter(); fn(); ts.append(time.perf_counter() - t0)
    return statistics.median(ts), ts

# ---- D-1 ----
t, all_t = med(lambda: D_1.climb(10 ** 7))
print("D-1 climb(1e7): median %.2fs  all=%s  -> loops/s=%.2e" % (t, ["%.2f" % x for x in all_t], 1e7 / t))

sys.setrecursionlimit(10000)
calls = [0]
def fib_rec(n):
    calls[0] += 1
    if n <= 1: return 1
    return fib_rec(n - 1) + fib_rec(n - 2)
t0 = time.perf_counter(); v = fib_rec(35); dt = time.perf_counter() - t0
print("D-1 naive recursion n=35: %.2fs, calls=%d, value=%d" % (dt, calls[0], v))

sys.setrecursionlimit(1000)
try:
    fib_rec(1000)
    print("D-1 recursion n=1000: no error?!")
except RecursionError as e:
    print("D-1 recursion n=1000 -> RecursionError:", str(e)[:90])

# ---- M-2 n=1000 ----
t, _ = med(lambda: M_2.josephus(1000, 3))
print("M-2 josephus(1000,3): median %.3fs" % t)

# ---- H-1 with / without guard ----
def h_guard(nums):
    s = set(nums); best = 0
    for x in s:
        if x - 1 in s: continue
        y = x
        while y + 1 in s: y += 1
        best = max(best, y - x + 1)
    return best
def h_noguard(nums):
    s = set(nums); best = 0
    for x in s:
        y = x
        while y + 1 in s: y += 1
        best = max(best, y - x + 1)
    return best
data = list(range(1, 10001))
t1, _ = med(lambda: h_guard(data))
t2, _ = med(lambda: h_noguard(data), reps=3)
print("H-1 n=10000: guard %.3fs, no-guard %.3fs, ratio %.1fx" % (t1, t2, t2 / t1))

# ---- 1.3 input parsing, 200k lines ----
lines = ["%d %d" % (i, i + 1) for i in range(200000)]
blob = "\n".join(lines) + "\n"

def via_input():
    old = sys.stdin
    sys.stdin = io.StringIO(blob)
    s = 0
    try:
        for line in sys.stdin:
            a, b = line.split()
            s += int(a) + int(b)
    finally:
        sys.stdin = old
    return s

def via_readline():
    old = sys.stdin
    sys.stdin = io.StringIO(blob)
    s = 0
    try:
        while True:
            line = sys.stdin.readline()
            if not line: break
            a, b = line.split()
            s += int(a) + int(b)
    finally:
        sys.stdin = old
    return s

def via_readsplit():
    return sum(map(int, blob.encode().split()))

for name, fn in [("input()", via_input), ("readline()", via_readline), ("read().split()", via_readsplit)]:
    t, ts = med(fn)
    print("1.3 parse 200k lines via %-16s median %.3fs  all=%s" % (name, t, ["%.3f" % x for x in ts]))
