import sys
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
    sys.stdout.write("\n".join(out) + "\n")
main()
