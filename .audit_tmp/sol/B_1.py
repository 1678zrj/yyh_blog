import sys


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    stu = []
    idx = 1
    for _ in range(n):
        sid = data[idx].decode()
        name = data[idx + 1].decode()
        chinese = int(data[idx + 2])
        math = int(data[idx + 3])
        idx += 4
        stu.append((sid, name, chinese, math))
    # 一个 key 里写完三档优先级：总分降序 → 语文降序 → 学号升序
    stu.sort(key=lambda s: (-(s[2] + s[3]), -s[2], s[0]))
    out = ["%s %s %d %d %d" % (s[0], s[1], s[2] + s[3], s[2], s[3]) for s in stu]
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
