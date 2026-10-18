import sys


def main():
    data = sys.stdin.buffer.read().split()   # 把所有 token 一次读进来
    if not data:
        return
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    print(max(a) - min(a))


if __name__ == "__main__":
    main()
