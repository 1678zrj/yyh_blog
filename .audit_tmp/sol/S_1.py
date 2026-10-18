import sys
from collections import Counter


def main():
    s = sys.stdin.readline().strip()
    cnt = Counter(s)
    # 次数降序、同次数字符升序 —— 两个键必须写进同一个 key 里
    items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))
    out = ["%s %d" % (ch, c) for ch, c in items[:3]]
    sys.stdout.write("\n".join(out))
    if out:
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
