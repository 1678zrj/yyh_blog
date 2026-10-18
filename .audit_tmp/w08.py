import sys
def main():
    s = sys.stdin.readline().rstrip("\n")
    print("YES" if s == s[::-1] else "NO")
main()
