def lower_bound(a, x):
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
