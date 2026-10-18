import sys
def main():
    n = int(sys.stdin.readline())
    a = list(map(int, input().split()))
    print(max(a) - min(a))
main()
