import sys
def main():
    data = sys.stdin.buffer.read().split()
    n, k = int(data[0]), int(data[1])
    L = list(map(int, data[2:2+n]))
    print(sum(L) // k)
main()
