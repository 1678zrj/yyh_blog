import sys


def longest_consecutive(nums):
    s = set(nums)
    best = 0
    for x in s:
        if x - 1 in s:          # 不是序列起点：跳过（这一句决定了复杂度）
            continue
        y = x
        while y + 1 in s:
            y += 1
        if y - x + 1 > best:
            best = y - x + 1
    return best


def main():
    data = sys.stdin.buffer.read().split()
    if not data:
        return
    n = int(data[0])
    nums = list(map(int, data[1:1 + n]))
    print(longest_consecutive(nums))


if __name__ == "__main__":
    main()
