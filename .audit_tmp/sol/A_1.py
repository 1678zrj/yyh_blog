import sys


def main():
    data = sys.stdin.buffer.read().split()
    n, target = int(data[0]), int(data[1])
    a = list(map(int, data[2:2 + n]))
    i, j = 0, n - 1
    while i < j:
        s = a[i] + a[j]
        if s == target:
            print(i + 1, j + 1)        # 输出 1-based 下标
            return
        if s < target:
            i += 1                     # 和太小：左端右移
        else:
            j -= 1                     # 和太大：右端左移
    print(-1)


if __name__ == "__main__":
    main()
