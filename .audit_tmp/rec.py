import time
def f(n):
    return 1 if n <= 1 else f(n-1) + f(n-2)
best = []
for _ in range(3):
    t0 = time.perf_counter(); v = f(35); best.append(time.perf_counter()-t0)
print(" ".join("%.2f" % x for x in best), v)
