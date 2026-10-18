import sys


def max_subarray(a):
    best = cur = 0           # 用第一个元素初始化：全负数时答案就是它
    for x in a[1:]:
        cur = x if cur < 0 else cur + x   # 前缀为负就丢掉，从 x 重新开始
        if cur > best:
            best = cur
    return best


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    print(max_subarray(a))


if __name__ == "__main__":
    main()
