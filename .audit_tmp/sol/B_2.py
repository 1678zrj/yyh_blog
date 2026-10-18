import sys


def lower_bound(a, x):
    lo, hi = 0, len(a)          # 答案落在 [0, n]：x 比所有元素都大时是 n
    while lo < hi:              # 区间非空就继续
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1        # mid 及其左边都不可能是答案
        else:
            hi = mid            # mid 可能是答案，保留
    return lo


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    m = int(data[1 + n])
    qs = list(map(int, data[2 + n:2 + n + m]))
    sys.stdout.write(" ".join(str(lower_bound(a, x)) for x in qs) + "\n")


if __name__ == "__main__":
    main()
