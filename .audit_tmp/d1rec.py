import time
def f(n):
    return 1 if n <= 1 else f(n-1) + f(n-2)
t0 = time.perf_counter(); v = f(35); print("%.3f %d" % (time.perf_counter()-t0, v))
