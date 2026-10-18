import sys


def longest_unique(s):
    last = {}                 # 字符 -> 最近一次出现的下标
    left = 0
    best = 0
    for right, ch in enumerate(s):
        # 只有当"上次出现位置"还在当前窗口内，left 才需要右移
        if ch in last:
            left = last[ch] + 1
        last[ch] = right
        if right - left + 1 > best:
            best = right - left + 1
    return best


def main():
    s = sys.stdin.readline().strip()      # 题面保证只含小写字母
    print(longest_unique(s))


if __name__ == "__main__":
    main()
