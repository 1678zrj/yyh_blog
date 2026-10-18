import sys
def main():
    while True:
        try:
            a, b = map(int, sys.stdin.readline().split())
        except EOFError:
            break
        print(a + b)
main()
