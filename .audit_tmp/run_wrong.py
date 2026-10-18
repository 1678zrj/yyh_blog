# -*- coding: utf-8 -*-
import os, subprocess, sys, re

PY = r"D:\mycode\python\my_blog\.venv\Scripts\python.exe"
TMP = os.path.dirname(os.path.abspath(__file__))
SOL = os.path.join(TMP, "sol")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
_n = [0]

def run_src(src, inp, timeout=15):
    _n[0] += 1
    p = os.path.join(TMP, "w%02d.py" % _n[0])
    open(p, "w", encoding="utf-8", newline="\n").write(src)
    try:
        r = subprocess.run([PY, p], input=inp.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, env=env)
        return (r.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
                r.stderr.decode("utf-8", "replace"), r.returncode)
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>", "", -999

def load(lab):
    return open(os.path.join(SOL, lab.replace("-", "_") + ".py"), encoding="utf-8").read()

def show(tag, out, err):
    tail = [l for l in err.strip().splitlines() if l.strip()][-1:] or [""]
    print("%-46s out=%r  err=%s" % (tag, out, tail[0][:110]))

# ---- IO-1 错法一: T = int(input()) ----
io1_T = """import sys
def main():
    T = int(input())
    out = []
    for _ in range(T):
        a, b = map(int, input().split())
        out.append(str(a + b))
    print("\\n".join(out))
main()
"""
o, e, rc = run_src(io1_T, "1 2\n-5 5\n1000000000 -1000000000\n")
show("IO-1 wrong#1 (T=int(input))", o, e)
print("   err full:", e.strip().splitlines()[-1] if e else "")
o, e, rc = run_src(io1_T, "3\n1 2\n4 5\n7 8\n")
show("IO-1 wrong#1 on '3 then 3 pairs'", o, e)

# ---- IO-1 错法二: EOFError loop with blank line ----
io1_eof = """import sys
def main():
    while True:
        try:
            a, b = map(int, sys.stdin.readline().split())
        except EOFError:
            break
        print(a + b)
main()
"""
o, e, rc = run_src(io1_eof, "1 2\n\n-5 5\n")
show("IO-1 wrong#2 (EOFError) with blank line", o, e)
print("   err full:", e.strip().splitlines()[-1] if e else "")

# ---- IO-3a 错法一 ----
io3a_line = """import sys
def main():
    n = int(sys.stdin.readline())
    a = list(map(int, input().split()))
    print(max(a) - min(a))
main()
"""
o, e, rc = run_src(io3a_line, "5\n1 2\n3 4 5\n")
show("IO-3a wrong#1 (input().split one line)", o, e)

io3a_perline = """import sys
def main():
    n = int(sys.stdin.readline())
    a = [int(input()) for _ in range(n)]
    print(max(a) - min(a))
main()
"""
o, e, rc = run_src(io3a_perline, "5 1 2 3 4 5\n")
show("IO-3a wrong#2 (one per line)", o, e)
print("   err full:", e.strip().splitlines()[-1] if e else "")

# ---- IO-3b 错法 ----
io3b_tok = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    out = [str(len(t.decode())) for t in data[1:1 + n]]
    sys.stdout.write("\\n".join(out) + "\\n")
main()
"""
o, e, rc = run_src(io3b_tok, "3\nZhang San\nLi Si\nWang Wu\n")
show("IO-3b wrong (read().split tokens)", o, e)

# ---- S-1 most_common ----
s1_wrong = """import sys
from collections import Counter
def main():
    s = sys.stdin.readline().strip()
    for ch, c in Counter(s).most_common(3):
        print(ch, c)
main()
"""
o, e, rc = run_src(s1_wrong, "cba\n")
show("S-1 wrong (most_common(3)) on cba", o, e)

# ---- S-2 s == s[::-1] ----
s2_wrong = """import sys
def main():
    s = sys.stdin.readline().rstrip("\\n")
    print("YES" if s == s[::-1] else "NO")
main()
"""
o, e, rc = run_src(s2_wrong, "A man, a plan, a canal: Panama\n")
show("S-2 wrong (s == s[::-1])", o, e)

# ---- S-3 wrong condition ----
s3_wrong = load("S-3").replace("if ch in last and last[ch] >= left:", "if ch in last:")
o, e, rc = run_src(s3_wrong, "abba\n")
show("S-3 wrong (no last[ch]>=left) on abba", o, e)

# ---- A-2 sort key ----
a2_sort = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    a.sort(key=lambda x: x == 0)
    print(" ".join(map(str, a)))
main()
"""
o, e, rc = run_src(a2_sort, "5\n0 1 0 3 12\n")
show("A-2 alt (sort key x==0)", o, e)

# ---- A-2 while 0 in a: infinite loop ----
a2_loop = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    k = 0
    while 0 in a:
        a.remove(0); a.append(0)
        k += 1
        if k > 200000:
            print("ITER>200000 (still looping)"); return
    print(" ".join(map(str, a)))
main()
"""
o, e, rc = run_src(a2_loop, "5\n0 1 0 3 12\n")
show("A-2 wrong#1 (while 0 in a) 200k cap", o, e)

# ---- A-3 assign r ----
a3_wrong = load("A-3").replace("            if r > res[-1][1]:           # 不能直接写 = r：新区间可能被旧区间包住\n                res[-1][1] = r",
                               "            res[-1][1] = r")
assert "if r > res[-1][1]" not in a3_wrong
o, e, rc = run_src(a3_wrong, "2\n1 10\n2 3\n")
show("A-3 wrong (=r) on [1,10]+[2,3]", o, e)

# ---- M-1 without the two guards ----
m1_wrong = load("M-1").replace("if top <= bottom:", "if True:").replace("if left <= right:", "if True:")
o, e, rc = run_src(m1_wrong, "3 5\n")
show("M-1 no-guard on 3x5", o, e)
print("   got lines:", o.strip().split("\n"))

# squares 1..12 comparison
m1_check = load("M-1").replace(
    '    m, n = map(int, sys.stdin.readline().split())',
    '    import json\n    bad = []\n    m, n = map(int, sys.stdin.readline().split())')
o, e, rc = run_src(m1_wrong.replace(
    'if __name__ == "__main__":\n    main()',
    '''
if __name__ == "__main__":
    import io as _io
    def grab(m, n):
        a = spiral(m, n)
        return [" ".join(map(str, r)) for r in a]
    # correct version defined inline
    def spiral_ok(m, n):
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
            if top <= bottom:
                for j in range(right, left - 1, -1):
                    a[bottom][j] = x; x += 1
                bottom -= 1
            if left <= right:
                for i in range(bottom, top - 1, -1):
                    a[i][left] = x; x += 1
                left += 1
        return [" ".join(map(str, r)) for r in a]
    diff = [(n, m) for n in range(1, 13) for m in range(1, 13) if grab(n, m) != spiral_ok(n, m)]
    print("square/rect diffs on 1..12:", len(diff), diff[:10])
'''), "1 1\n")
print("M-1 wrong-vs-right over m,n=1..12:", o.strip(), e.strip()[-200:])

# ---- M-2 with extra idx += 1 ----
m2 = load("M-2")
m2_bad = m2.replace("        order.append(people.pop(idx))       # pop 之后，idx 正好指向下一个人的位置",
                    "        order.append(people.pop(idx))\n        idx += 1")
assert "idx += 1" in m2_bad
o, e, rc = run_src(m2_bad, "5 3\n")
show("M-2 wrong (extra idx+=1) on 5 3", o, e)

# ---- B-1 three sorts ----
b1_wrong = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0]); stu = []; idx = 1
    for _ in range(n):
        stu.append((data[idx].decode(), data[idx+1].decode(), int(data[idx+2]), int(data[idx+3])))
        idx += 4
    stu.sort(key=lambda s: -(s[2] + s[3]))
    stu.sort(key=lambda s: -s[2])
    stu.sort(key=lambda s: s[0])
    out = ["%s %s %d %d %d" % (s[0], s[1], s[2]+s[3], s[2], s[3]) for s in stu]
    sys.stdout.write("\\n".join(out) + "\\n")
main()
"""
o, e, rc = run_src(b1_wrong, "4\n003 \u738b\u4e94 90 90\n001 \u674e\u56db 95 85\n002 \u5f20\u4e09 80 100\n004 \u8d75\u516d 90 90\n")
show("B-1 wrong (3 sorts high->low)", o, e)

# ---- B-2 <= ----
b2_wrong = load("B-2").replace("if a[mid] < x:", "if a[mid] <= x:")
o, e, rc = run_src(b2_wrong, "4\n1 3 3 5\n4\n0 3 4 9\n")
show("B-2 wrong (<=) on queries 0 3 4 9", o, e)

# ---- B-2 lo=mid trace ----
b2_trace = """def lower_bound(a, x):
    lo, hi = 0, len(a)
    states = []
    for step in range(12):
        if not (lo < hi):
            states.append(("END", lo, hi)); break
        mid = (lo + hi) // 2
        states.append((step, lo, hi, mid))
        if a[mid] < x:
            lo = mid
        else:
            hi = mid
    return states
print(lower_bound([1, 3, 3, 5], 3))
"""
o, e, rc = run_src(b2_trace, "")
show("B-2 wrong (lo=mid) trace", o, e)

# ---- B-3 sum//k ----
b3_wrong = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    n, k = int(data[0]), int(data[1])
    L = list(map(int, data[2:2+n]))
    print(sum(L) // k)
main()
"""
o, e, rc = run_src(b3_wrong, "2 2\n9 1\n")
show("B-3 wrong (sum//k) on 9 1", o, e)

# ---- D-2 positive order ----
d2_wrong = load("D-2").replace("for c in range(W, w - 1, -1):   # 倒序：dp[c-w] 还是\"没放这件物品\"的值",
                               "for c in range(w, W + 1):")
assert "range(W, w - 1, -1)" not in d2_wrong
o, e, rc = run_src(d2_wrong, "1 4\n2 3\n")
show("D-2 wrong (ascending) on 1 4 / 2 3", o, e)

# ---- D-3 zero init ----
d3_wrong = load("D-3").replace("best = cur = a[0]", "best = cur = 0")
o, e, rc = run_src(d3_wrong, "3\n-3 -1 -2\n")
show("D-3 wrong (0 init) on all-neg", o, e)

# ---- IO-2 four wrong reprs ----
v1 = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(1, t + 1):
        out.append("Case #%d: %d " % (k, int(data[2*k-1]) + int(data[2*k])))
    sys.stdout.write("\\n".join(out) + "\\n")
main()
"""
o, e, rc = run_src(v1, "2\n1 2\n3 4\n")
print("IO-2 wrong#1 trailing space repr:", repr(o))

v2 = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(1, t + 1):
        out.append("Case #%d: %d" % (k, int(data[2*k-1]) + int(data[2*k])))
    sys.stdout.write("\\n\\n".join(out) + "\\n\\n")
main()
"""
o, e, rc = run_src(v2, "2\n1 2\n3 4\n")
print("IO-2 wrong#2 blank line repr:", repr(o))

v3 = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(t):
        out.append("Case #%d: %d" % (k, int(data[2*k+1]) + int(data[2*k+2])))
    sys.stdout.write("\\n".join(out) + "\\n")
main()
"""
o, e, rc = run_src(v3, "2\n1 2\n3 4\n")
print("IO-2 wrong#3 zero-based repr:", repr(o))

v4 = """import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(1, t + 1):
        out.append("Case #%d:%d" % (k, int(data[2*k-1]) + int(data[2*k])))
    sys.stdout.write("\\n".join(out) + "\\n")
main()
"""
o, e, rc = run_src(v4, "2\n1 2\n3 4\n")
print("IO-2 wrong#4 missing space repr:", repr(o))

# ---- isalnum unicode claims ----
o, e, rc = run_src("print('\u4e2d'.isalnum(), '\uff0c'.isalnum())\n", "")
print("isalnum check (expect True False):", o.strip())
