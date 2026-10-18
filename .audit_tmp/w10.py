import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    a.sort(key=lambda x: x == 0)
    print(" ".join(map(str, a)))
main()
