import sys


def main():
    n = int(sys.stdin.readline())
    out = []
    for _ in range(n):
        name = sys.stdin.readline().rstrip("\n")   # 整行读，只去掉换行
        out.append(str(len(name)))
    sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
