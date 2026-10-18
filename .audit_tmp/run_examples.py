import os, subprocess, sys, json

PY = r"D:\mycode\python\my_blog\.venv\Scripts\python.exe"
TMP = os.path.dirname(os.path.abspath(__file__))
SOL = os.path.join(TMP, "sol")
env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

def run(lab, inp, timeout=60, src=None):
    p = os.path.join(SOL, lab.replace("-", "_") + ".py")
    if src is not None:
        p = os.path.join(TMP, "var_" + lab.replace("-", "_") + ".py")
        open(p, "w", encoding="utf-8", newline="\n").write(src)
    try:
        r = subprocess.run([PY, p], input=inp.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=timeout, env=env)
        return (r.stdout.decode("utf-8", "replace").replace("\r\n", "\n"),
                r.stderr.decode("utf-8", "replace"), r.returncode)
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>", "", -999

cases = []
def c(lab, name, inp, exp):
    cases.append((lab, name, inp, exp))

# ---------------- IO-1 ----------------
c("IO-1", "example", "1 2\n-5 5\n1000000000 -1000000000\n", "3\n0\n0\n")
c("IO-1", "empty", "", "")
c("IO-1", "blank-lines", "1 2\n\n\n-5 5\n", "3\n0\n")
c("IO-1", "tabs+multispace", "1\t2\n   3    4\n", "3\n7\n")
c("IO-1", "no-trailing-nl", "1 2", "3\n")
c("IO-1", "overflow-ll", "9223372036854775807 1\n", "9223372036854775808\n")

# ---------------- IO-2 ----------------
c("IO-2", "example", "3\n1 2\n-5 5\n1000000000 -1000000000\n",
  "Case #1: 3\nCase #2: 0\nCase #3: 0\n")
c("IO-2", "T=0 (text claims no output)", "0\n", "")
c("IO-2", "empty", "", "")
c("IO-2", "negative", "2\n1 2\n-3 -5\n", "Case #1: 3\nCase #2: -8\n")
c("IO-2", "T=1", "1\n2 3\n", "Case #1: 5\n")

# ---------------- IO-3a ----------------
c("IO-3a", "split-lines", "5\n1 2\n3 4 5\n", "4\n")
c("IO-3a", "one-line", "5 1 2 3 4 5\n", "4\n")
c("IO-3a", "n=1", "1\n7\n", "0\n")
c("IO-3a", "negatives", "4\n-5 3 -1 0\n", "8\n")

# ---------------- IO-3b ----------------
c("IO-3b", "example", "3\nZhang San\nLi Si\nWang Wu\n", "9\n5\n7\n")
c("IO-3b", "blank-name-line", "1\n\n", "0\n")
c("IO-3b", "leading-space", "1\n  ab\n", "4\n")

# ---------------- S-1 ----------------
c("S-1", "example", "aabbbccddd\n", "b 3\nd 3\na 2\n")
c("S-1", "cba", "cba\n", "a 1\nb 1\nc 1\n")
c("S-1", "empty", "\n", "")
c("S-1", "single", "a\n", "a 1\n")
c("S-1", "abcd", "abcd\n", "a 1\nb 1\nc 1\n")

# ---------------- S-2 ----------------
c("S-2", "panama", "A man, a plan, a canal: Panama\n", "YES\n")
c("S-2", "ab", "ab\n", "NO\n")
c("S-2", "0P", "0P\n", "NO\n")
c("S-2", "zhong", "\u4e2da\u4e2d\n", "YES\n")
c("S-2", "empty", "\n", "YES\n")
c("S-2", "punct-only", ".,\n", "YES\n")
c("S-2", "aA", "aA\n", "YES\n")

# ---------------- S-3 ----------------
c("S-3", "abcabcbb", "abcabcbb\n", "3\n")
c("S-3", "bbbbb", "bbbbb\n", "1\n")
c("S-3", "pwwkew", "pwwkew\n", "3\n")
c("S-3", "abba", "abba\n", "2\n")
c("S-3", "empty", "\n", "0\n")
c("S-3", "all-distinct", "abcdef\n", "6\n")

# ---------------- A-1 ----------------
c("A-1", "example1", "4 9\n2 7 11 15\n", "1 2\n")
c("A-1", "example2", "3 4\n1 2 3\n", "1 3\n")
c("A-1", "example3", "2 3\n1 1\n", "-1\n")
c("A-1", "dup", "2 2\n1 1\n", "1 2\n")
c("A-1", "n=2 no sol", "2 0\n1 1\n", "-1\n")

# ---------------- A-2 ----------------
c("A-2", "example", "5\n0 1 0 3 12\n", "1 3 12 0 0\n")
c("A-2", "all-zero", "3\n0 0 0\n", "0 0 0\n")
c("A-2", "neg", "4\n-1 0 -2 0\n", "-1 -2 0 0\n")
c("A-2", "n=1", "1\n0\n", "0\n")

# ---------------- A-3 ----------------
c("A-3", "example", "4\n1 3\n8 10\n2 6\n15 18\n", "3\n1 6\n8 10\n15 18\n")
c("A-3", "touch", "2\n1 4\n4 5\n", "1\n1 5\n")
c("A-3", "n=0", "0\n", "0\n")
c("A-3", "contains", "2\n1 10\n2 3\n", "1\n1 10\n")
c("A-3", "single-point", "1\n5 5\n", "1\n5 5\n")

# ---------------- M-1 ----------------
c("M-1", "3x5", "3 5\n", "1 2 3 4 5\n12 13 14 15 6\n11 10 9 8 7\n")
c("M-1", "4x4", "4 4\n", "1 2 3 4\n12 13 14 5\n11 16 15 6\n10 9 8 7\n")
c("M-1", "1x1", "1 1\n", "1\n")
c("M-1", "1x5", "1 5\n", "1 2 3 4 5\n")
c("M-1", "5x1", "5 1\n", "1\n2\n3\n4\n5\n")
c("M-1", "2x3", "2 3\n", "1 2 3\n6 5 4\n")

# ---------------- M-2 ----------------
c("M-2", "5 3", "5 3\n", "3 1 5 2 4\n")
c("M-2", "5 1", "5 1\n", "1 2 3 4 5\n")
c("M-2", "1 1", "1 1\n", "1\n")
c("M-2", "6 2", "6 2\n", "2 4 6 3 1 5\n")
c("M-2", "m>n", "3 7\n", "1 2 3\n")

# ---------------- H-1 ----------------
c("H-1", "example", "6\n100 4 200 1 3 2\n", "4\n")
c("H-1", "dup", "3\n1 1 2\n", "2\n")
c("H-1", "n=0", "0\n", "0\n")
c("H-1", "negative", "2\n-1 -2\n", "2\n")
c("H-1", "all-same", "3\n7 7 7\n", "1\n")

# ---------------- B-1 ----------------
c("B-1", "example",
  "4\n003 \u738b\u4e94 90 90\n001 \u674e\u56db 95 85\n002 \u5f20\u4e09 80 100\n004 \u8d75\u516d 90 90\n",
  "001 \u674e\u56db 180 95 85\n003 \u738b\u4e94 180 90 90\n004 \u8d75\u516d 180 90 90\n002 \u5f20\u4e09 180 80 100\n")
c("B-1", "n=1", "1\n001 A 90 90\n", "001 A 180 90 90\n")

# ---------------- B-2 ----------------
c("B-2", "example", "4\n1 3 3 5\n4\n0 3 4 9\n", "0 1 3 4\n")
c("B-2", "n=0", "0\n3\n1 2 3\n", "0 0 0\n")
c("B-2", "all-dup", "3\n2 2 2\n2\n2 3\n", "0 3\n")

# ---------------- B-3 ----------------
c("B-3", "example1", "3 7\n10 20 30\n", "7\n")
c("B-3", "example2", "2 2\n9 1\n", "4\n")
c("B-3", "example3", "1 3\n2\n", "0\n")
c("B-3", "k=1", "1 1\n10\n", "10\n")
c("B-3", "exact", "1 4\n8\n", "2\n")

# ---------------- D-1 ----------------
for n, e in [(0, 1), (1, 1), (2, 2), (3, 3), (10, 89), (20, 10946)]:
    c("D-1", "n=%d" % n, "%d\n" % n, "%d\n" % e)

# ---------------- D-2 ----------------
c("D-2", "example", "3 5\n2 3\n3 4\n4 5\n", "7\n")
c("D-2", "one-item", "1 4\n2 3\n", "3\n")
c("D-2", "W=0", "2 0\n1 1\n2 2\n", "0\n")
c("D-2", "all-overweight", "2 1\n5 100\n6 200\n", "0\n")

# ---------------- D-3 ----------------
c("D-3", "all-neg", "3\n-3 -1 -2\n", "-1\n")
c("D-3", "single-neg", "1\n-5\n", "-5\n")
c("D-3", "example", "8\n1 -2 3 10 -4 7 2 -5\n", "18\n")
c("D-3", "all-pos", "3\n1 2 3\n", "6\n")

fails = 0
for lab, name, inp, exp in cases:
    out, err, rc = run(lab, inp)
    ok = (out == exp and rc == 0)
    if not ok:
        fails += 1
        print("FAIL %-6s %-28s rc=%s\n   in =%r\n   exp=%r\n   got=%r\n   err=%s"
              % (lab, name, rc, inp, exp, out, err.strip().splitlines()[-1:] or ""))
print("TOTAL cases:", len(cases), "FAILS:", fails)
