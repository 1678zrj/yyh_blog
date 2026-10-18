import sys
d = sys.stdin.buffer.read().split()
s = 0
for i in range(0, len(d), 2):
    s += int(d[i]) + int(d[i+1])
print(s)
