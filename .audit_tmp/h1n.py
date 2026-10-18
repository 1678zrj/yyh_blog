import sys, time
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
print(solve(False))
