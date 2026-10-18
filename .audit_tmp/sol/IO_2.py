import sys


def main():
    data = sys.stdin.buffer.read().split()
    if not data:
        return
    t = int(data[0])
    idx = 1
    out = []
    for k in range(1, t + 1):                   # 组号从 1 开始
        a, b = int(data[idx]), int(data[idx + 1])
        idx += 2
        out.append("Case #%d: %d" % (k, a + b))  # 冒号后恰一个空格，行末没有空格
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
