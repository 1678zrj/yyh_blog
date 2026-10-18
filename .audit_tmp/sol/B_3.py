import sys


def max_piece(L, k):
    lo, hi = 1, max(L)              # 答案范围 [1, 最长的一根]
    ans = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        cnt = 0
        for x in L:
            cnt += x // mid         # 这根能切出几段
        if cnt >= k:                # 切得出来：试试更长
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def main():
    data = sys.stdin.buffer.read().split()
    n, k = int(data[0]), int(data[1])
    L = list(map(int, data[2:2 + n]))
    print(max_piece(L, k))


if __name__ == "__main__":
    main()
