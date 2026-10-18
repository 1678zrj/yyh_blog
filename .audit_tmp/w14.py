import sys


def spiral(m, n):
    a = [[0] * n for _ in range(m)]
    top, bottom, left, right = 0, m - 1, 0, n - 1
    x = 1
    while top <= bottom and left <= right:
        for j in range(left, right + 1):          # 上边：左 → 右
            a[top][j] = x
            x += 1
        top += 1
        for i in range(top, bottom + 1):          # 右边：上 → 下
            a[i][right] = x
            x += 1
        right -= 1
        if True:                         # 下边还在，才画
            for j in range(right, left - 1, -1):
                a[bottom][j] = x
                x += 1
            bottom -= 1
        if True:                         # 左边还在，才画
            for i in range(bottom, top - 1, -1):
                a[i][left] = x
                x += 1
            left += 1
    return a


def main():
    m, n = map(int, sys.stdin.readline().split())
    for row in spiral(m, n):
        print(" ".join(map(str, row)))



if __name__ == "__main__":
    import io as _io
    def grab(m, n):
        a = spiral(m, n)
        return [" ".join(map(str, r)) for r in a]
    # correct version defined inline
    def spiral_ok(m, n):
        a = [[0] * n for _ in range(m)]
        top, bottom, left, right = 0, m - 1, 0, n - 1
        x = 1
        while top <= bottom and left <= right:
            for j in range(left, right + 1):
                a[top][j] = x; x += 1
            top += 1
            for i in range(top, bottom + 1):
                a[i][right] = x; x += 1
            right -= 1
            if top <= bottom:
                for j in range(right, left - 1, -1):
                    a[bottom][j] = x; x += 1
                bottom -= 1
            if left <= right:
                for i in range(bottom, top - 1, -1):
                    a[i][left] = x; x += 1
                left += 1
        return [" ".join(map(str, r)) for r in a]
    diff = [(n, m) for n in range(1, 13) for m in range(1, 13) if grab(n, m) != spiral_ok(n, m)]
    print("square/rect diffs on 1..12:", len(diff), diff[:10])

