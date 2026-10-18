import sys
def main():
    n = int(sys.stdin.readline())
    a = [int(input()) for _ in range(n)]
    print(max(a) - min(a))
main()
