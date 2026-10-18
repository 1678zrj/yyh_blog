import sys


def josephus(n, m):
    people = list(range(1, n + 1))
    idx = 0                     # 当前"报 1"的人的下标
    order = []
    while people:
        idx = (idx + m - 1) % len(people)   # 数 m 个人，落到的下标
        order.append(people.pop(idx))       # pop 之后，idx 正好指向下一个人的位置
    return order


def main():
    n, m = map(int, sys.stdin.readline().split())
    print(" ".join(map(str, josephus(n, m))))


if __name__ == "__main__":
    main()
