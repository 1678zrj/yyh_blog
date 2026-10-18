import sys


def knapsack(W, items):
    dp = [0] * (W + 1)                  # dp[c] = 容量 c 时的最大价值
    for w, v in items:
        for c in range(w, W + 1):
            cand = dp[c - w] + v
            if cand > dp[c]:
                dp[c] = cand
    return dp[W]


def main():
    data = sys.stdin.buffer.read().split()
    n, W = int(data[0]), int(data[1])
    items = []
    idx = 2
    for _ in range(n):
        items.append((int(data[idx]), int(data[idx + 1])))
        idx += 2
    print(knapsack(W, items))


if __name__ == "__main__":
    main()
