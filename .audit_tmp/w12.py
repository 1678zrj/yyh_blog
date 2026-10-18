import sys


def main():
    data = sys.stdin.buffer.read().split()
    if not data:
        return
    n = int(data[0])
    segs = []
    idx = 1
    for _ in range(n):
        segs.append([int(data[idx]), int(data[idx + 1])])
        idx += 2
    segs.sort()                          # 左端点升序（相同则右端点升序）
    res = []
    for l, r in segs:
        if res and l <= res[-1][1]:      # 相接（l 等于上一段右端点）也算重叠
            res[-1][1] = r
        else:
            res.append([l, r])
    out = ["%d %d" % (l, r) for l, r in res]
    sys.stdout.write(str(len(res)) + "\n")
    if out:
        sys.stdout.write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
