import sys


def main():
    out = []
    for line in sys.stdin:              # 迭代 stdin：读到 EOF 自然结束
        line = line.strip()
        if not line:                    # 空行不算一组数据
            continue
        a, b = map(int, line.split())
        out.append(str(a + b))
    sys.stdout.write("\n".join(out))
    if out:
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
