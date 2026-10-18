import sys

MOD = 1_000_000_007


def climb(n):
    a, b = 1, 1                 # f(0) = 1, f(1) = 1
    for _ in range(n):
        a, b = b, (a + b) % MOD
    return a


def main():
    n = int(sys.stdin.readline())
    print(climb(n))


if __name__ == "__main__":
    main()
