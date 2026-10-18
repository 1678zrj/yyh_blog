
import sys, time, statistics
mode = sys.argv[1]
f = open(r"D:\\mycode\\python\\my_blog\\.audit_tmp\\big.txt", "r", encoding="utf-8")
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
print("%s median %.4f all %s" % (mode, statistics.median(ts), ["%.3f" % x for x in ts]))
