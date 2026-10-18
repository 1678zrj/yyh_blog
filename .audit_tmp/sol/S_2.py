import sys


def is_palindrome(s):
    i, j = 0, len(s) - 1
    while i < j:
        if not s[i].isalnum():          # 左边不是字母数字：跳过
            i += 1
        elif not s[j].isalnum():        # 右边不是字母数字：跳过
            j -= 1
        elif s[i].lower() != s[j].lower():
            return False
        else:
            i += 1
            j -= 1
    return True


def main():
    s = sys.stdin.readline().rstrip("\n")   # 整行读：空格也是它的一部分
    print("YES" if is_palindrome(s) else "NO")


if __name__ == "__main__":
    main()
