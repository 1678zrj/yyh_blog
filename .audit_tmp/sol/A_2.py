import sys


def move_zeroes(a):
    k = 0                              # k = 下一个"非零元素"该放的位置
    for i in range(len(a)):
        if a[i] != 0:
            a[k], a[i] = a[i], a[k]
            k += 1


def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    move_zeroes(a)
    print(" ".join(map(str, a)))


if __name__ == "__main__":
    main()
