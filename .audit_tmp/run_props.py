# -*- coding: utf-8 -*-
"""Property tests + timing remeasure for the exam chapter."""
import os, sys, random, time, subprocess, io, statistics, bisect
from functools import cmp_to_key

PY = r"D:\mycode\python\my_blog\.venv\Scripts\python.exe"
TMP = os.path.dirname(os.path.abspath(__file__))
SOL = os.path.join(TMP, "sol")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
sys.path.insert(0, SOL)

import S_1, S_2, S_3, A_1, A_2, A_3, M_1, M_2, H_1, B_1, B_2, B_3, D_1, D_2, D_3

def run_file(lab, inp, timeout=60):
    p = os.path.join(SOL, lab.replace("-", "_") + ".py")
    r = subprocess.run([PY, p], input=inp.encode("utf-8"), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout, env=env)
    return r.stdout.decode("utf-8").replace("\r\n", "\n"), r.stderr.decode("utf-8"), r.returncode

def call(lab, inp, timeout=60):
    return run_file(lab, inp, timeout)[0]

random.seed(20261018)

# ---------- 1. S-1 ----------
bad = 0
for _ in range(200):
    n = random.randint(1, 40)
    s = "".join(random.choice("abcde") for _ in range(n))
    cnt = {}
    for ch in s:
        cnt[ch] = cnt.get(ch, 0) + 1
    ref = "".join("%s %d\n" % (ch, c) for ch, c in
                  sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))[:3])
    got = call("S-1", s + "\n")
    if got != ref:
        bad += 1
        print("S-1 MISMATCH", s, repr(got), repr(ref)); break
print("S-1  piao: 200 cases, mismatches =", bad)

# ---------- 2. S-2 ----------
bad = 0
for _ in range(200):
    n = random.randint(0, 25)
    alphabet = "abAB .,;!09\u4e2d"
    s = "".join(random.choice(alphabet) for _ in range(n))
    t = "".join(ch.lower() for ch in s if ch.isalnum())
    ref = "YES\n" if t == t[::-1] else "NO\n"
    got = call("S-2", s + "\n")
    if got != ref:
        bad += 1
        print("S-2 MISMATCH", repr(s), repr(got), repr(ref)); break
print("S-2  piao: 200 cases, mismatches =", bad)

# ---------- 3. S-3 ----------
def brute_s3(s):
    best = 0
    for i in range(len(s)):
        seen = set()
        for j in range(i, len(s)):
            if s[j] in seen:
                break
            seen.add(s[j])
            best = max(best, j - i + 1)
    return best
bad = 0
for _ in range(200):
    s = "".join(random.choice("abc") for _ in range(random.randint(0, 28)))
    got = call("S-3", s + "\n").strip()
    if got != str(brute_s3(s)):
        bad += 1
        print("S-3 MISMATCH", s, got, brute_s3(s)); break
print("S-3  piao: 200 cases, mismatches =", bad)

# ---------- 4. A-1 ----------
def brute_a1(a, t):
    n = len(a)
    for i in range(n):
        for j in range(i + 1, n):
            if a[i] + a[j] == t:
                return (i + 1, j + 1)
    return None
bad = 0
for _ in range(200):
    n = random.randint(2, 9)
    a = sorted(random.randint(-5, 9) for _ in range(n))
    t = random.randint(-8, 16)
    exp = brute_a1(a, t)
    inp = "%d %d\n%s\n" % (n, t, " ".join(map(str, a)))
    got = call("A-1", inp).strip()
    want = "-1" if exp is None else "%d %d" % exp
    if got != want:
        bad += 1
        print("A-1 MISMATCH", a, t, "got", got, "want", want)
        if bad > 3: break
print("A-1  piao: 200 cases, mismatches =", bad)

# ---------- 5. A-2 ----------
bad = 0
for _ in range(200):
    n = random.randint(1, 12)
    a = [random.choice([0, 0, 1, -1, 2, -3, 7]) for _ in range(n)]
    ref = [x for x in a if x != 0] + [0] * a.count(0)
    got = call("A-2", "%d\n%s\n" % (n, " ".join(map(str, a)))).strip()
    if got != " ".join(map(str, ref)):
        bad += 1
        print("A-2 MISMATCH", a, got, ref); break
print("A-2  piao: 200 cases, mismatches =", bad)

# ---------- 6. A-3 (union-find reference) ----------
def ref_a3(segs):
    n = len(segs)
    par = list(range(n))
    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]; x = par[x]
        return x
    def uni(x, y):
        par[find(x)] = find(y)
    for i in range(n):
        for j in range(i + 1, n):
            li, ri = segs[i]; lj, rj = segs[j]
            if not (ri < lj or rj < li):     # overlap or touch
                uni(i, j)
    comp = {}
    for i, (l, r) in enumerate(segs):
        k = find(i)
        if k not in comp:
            comp[k] = [l, r]
        else:
            comp[k][0] = min(comp[k][0], l)
            comp[k][1] = max(comp[k][1], r)
    out = sorted(comp.values())
    return str(len(out)) + "\n" + "".join("%d %d\n" % (l, r) for l, r in out)
bad = 0
for _ in range(200):
    n = random.randint(0, 8)
    segs = []
    for _ in range(n):
        l = random.randint(0, 12)
        r = random.randint(l, 14)
        segs.append((l, r))
    inp = "%d\n" % n + "".join("%d %d\n" % s for s in segs)
    got = call("A-3", inp)
    ref = ref_a3(segs)
    if got != ref:
        bad += 1
        print("A-3 MISMATCH", segs, repr(got), repr(ref))
        if bad > 3: break
print("A-3  piao: 200 cases, mismatches =", bad)

# ---------- 7. M-1 invariants (m,n = 1..8) ----------
fails = []
for m in range(1, 9):
    for n in range(1, 9):
        a = M_1.spiral(m, n)
        flat = [x for row in a for x in row]
        ok = (sorted(flat) == list(range(1, m * n + 1)))
        pos = {a[i][j]: (i, j) for i in range(m) for j in range(n)}
        for v in range(1, m * n):
            i1, j1 = pos[v]; i2, j2 = pos[v + 1]
            if abs(i1 - i2) + abs(j1 - j2) != 1:
                ok = False
        if not ok:
            fails.append((m, n))
print("M-1  invariants 1..8 x 1..8 (64 groups): fails =", fails)

# square claim: guards removed, n x n identical
def spiral_noguard(m, n):
    a = [[0] * n for _ in range(m)]
    top, bottom, left, right = 0, m - 1, 0, n - 1
    x = 1
    while top <= bottom and left <= right:
        for j in range(left, right + 1):
            a[top][j] = x; x += 1
        top += 1
        for i in range(top, bottom + 1):
            a[i][right] = x; x += 1
        right -= 1
        for j in range(right, left - 1, -1):
            a[bottom][j] = x; x += 1
        bottom -= 1
        for i in range(bottom, top - 1, -1):
            a[i][left] = x; x += 1
        left += 1
    return a
sq_diff = [n for n in range(1, 13) if spiral_noguard(n, n) != M_1.spiral(n, n)]
all_diff = [(m, n) for m in range(1, 9) for n in range(1, 9)
            if spiral_noguard(m, n) != M_1.spiral(m, n)]
print("M-1  no-guard squares 1..12 diff:", sq_diff, "| all 1..8 diffs:", len(all_diff), all_diff[:6])

# ---------- 8. M-2 last survivor vs recurrence ----------
def f_rec(n, m):
    f = 0
    for i in range(2, n + 1):
        f = (f + m) % i
    return f + 1
bad = 0
for _ in range(200):
    n = random.randint(1, 30); m = random.randint(1, 40)
    order = M_2.josephus(n, m)
    if sorted(order) != list(range(1, n + 1)) or order[-1] != f_rec(n, m):
        bad += 1
        print("M-2 MISMATCH", n, m, order, f_rec(n, m)); break
print("M-2  piao: 200 cases, mismatches =", bad)

# ---------- 9. H-1 ----------
def ref_h1(nums):
    s = sorted(set(nums)); best = 0; cur = 0; prev = None
    for x in s:
        cur = cur + 1 if prev is not None and x == prev + 1 else 1
        best = max(best, cur); prev = x
    return best
bad = 0
for _ in range(200):
    n = random.randint(0, 15)
    nums = [random.randint(-10, 12) for _ in range(n)]
    inp = "%d\n%s\n" % (n, " ".join(map(str, nums))) if n else "0\n"
    got = call("H-1", inp).strip()
    if got != str(ref_h1(nums)):
        bad += 1
        print("H-1 MISMATCH", nums, got, ref_h1(nums)); break
print("H-1  piao: 200 cases, mismatches =", bad)

# ---------- 10. B-1 ----------
def ref_b1(rows):
    def cmp(x, y):
        tx, ty = x[2] + x[3], y[2] + y[3]
        if tx != ty: return -1 if tx > ty else 1
        if x[2] != y[2]: return -1 if x[2] > y[2] else 1
        return -1 if x[0] < y[0] else (0 if x[0] == y[0] else 1)
    rows = sorted(rows, key=cmp_to_key(cmp))
    return "".join("%s %s %d %d %d\n" % (r[0], r[1], r[2] + r[3], r[2], r[3]) for r in rows)
bad = 0
for _ in range(200):
    n = random.randint(1, 8)
    rows = []
    for _ in range(n):
        sid = "%03d" % random.randint(1, 20)
        name = random.choice(["A", "B", "C"])
        rows.append((sid, name, random.choice([80, 90, 95]), random.choice([85, 90, 100])))
    inp = "%d\n" % n + "".join("%s %s %d %d\n" % r for r in rows)
    got = call("B-1", inp)
    ref = ref_b1(rows)
    if got != ref:
        bad += 1
        print("B-1 MISMATCH", rows, repr(got), repr(ref))
        if bad > 2: break
print("B-1  piao: 200 cases, mismatches =", bad)

# ---------- 11. B-2 ----------
bad = 0
for _ in range(200):
    n = random.randint(0, 12)
    a = sorted(random.randint(-5, 10) for _ in range(n))
    m = random.randint(1, 6)
    qs = [random.randint(-8, 12) for _ in range(m)]
    inp = "%d\n%s\n%d\n%s\n" % (n, " ".join(map(str, a)), m, " ".join(map(str, qs)))
    got = call("B-2", inp).strip()
    want = " ".join(str(bisect.bisect_left(a, x)) for x in qs)
    if got != want:
        bad += 1
        print("B-2 MISMATCH", a, qs, got, want); break
print("B-2  piao: 200 cases, mismatches =", bad)

# ---------- 12. B-3 ----------
def ref_b3(L, k):
    for Lp in range(max(L), 0, -1):
        if sum(x // Lp for x in L) >= k:
            return Lp
    return 0
bad = 0
for _ in range(200):
    n = random.randint(1, 5)
    L = [random.randint(1, 40) for _ in range(n)]
    k = random.randint(1, 30)
    inp = "%d %d\n%s\n" % (n, k, " ".join(map(str, L)))
    got = call("B-3", inp).strip()
    if got != str(ref_b3(L, k)):
        bad += 1
        print("B-3 MISMATCH", L, k, got, ref_b3(L, k)); break
print("B-3  piao: 200 cases, mismatches =", bad)

# ---------- 13. D-1 vs matrix fast-doubling ----------
MOD = 10 ** 9 + 7
def fib(n):  # F(0)=0, F(1)=1
    if n == 0: return 0
    a, b = 0, 1
    for bit in bin(n)[2:]:
        c = a * ((2 * b - a) % MOD) % MOD
        d = (a * a + b * b) % MOD
        if bit == "0":
            a, b = c, d
        else:
            a, b = d, (c + d) % MOD
    return a
bad = 0
for _ in range(200):
    n = random.randint(0, 300)
    if D_1.climb(n) != fib(n + 1) % MOD:
        bad += 1
        print("D-1 MISMATCH", n, D_1.climb(n), fib(n + 1)); break
print("D-1  piao: 200 cases (0..300), mismatches =", bad, "| climb(10^7) begins:",
      str(D_1.climb(2000))[:6], "...")

# ---------- 14. D-2 vs 2D DP ----------
def ref_d2(W, items):
    dp = [[0] * (W + 1) for _ in range(len(items) + 1)]
    for i, (w, v) in enumerate(items, 1):
        for c in range(W + 1):
            dp[i][c] = dp[i - 1][c]
            if c >= w and dp[i - 1][c - w] + v > dp[i][c]:
                dp[i][c] = dp[i - 1][c - w] + v
    return dp[len(items)][W]
bad = 0
for _ in range(200):
    n = random.randint(1, 6); W = random.randint(0, 20)
    items = [(random.randint(1, 8), random.randint(0, 15)) for _ in range(n)]
    inp = "%d %d\n" % (n, W) + "".join("%d %d\n" % it for it in items)
    got = call("D-2", inp).strip()
    if got != str(ref_d2(W, items)):
        bad += 1
        print("D-2 MISMATCH", W, items, got, ref_d2(W, items)); break
print("D-2  piao: 200 cases, mismatches =", bad)

# ---------- 15. D-3 vs O(n^2) ----------
def ref_d3(a):
    best = a[0]
    for i in range(len(a)):
        s = 0
        for j in range(i, len(a)):
            s += a[j]; best = max(best, s)
    return best
bad = 0
for _ in range(200):
    n = random.randint(1, 15)
    a = [random.randint(-10, 10) for _ in range(n)]
    inp = "%d\n%s\n" % (n, " ".join(map(str, a)))
    got = call("D-3", inp).strip()
    if got != str(ref_d3(a)):
        bad += 1
        print("D-3 MISMATCH", a, got, ref_d3(a)); break
print("D-3  piao: 200 cases, mismatches =", bad)
