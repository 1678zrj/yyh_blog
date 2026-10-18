import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    a = list(map(int, data[1:1 + n]))
    k = 0
    while 0 in a:
        a.remove(0); a.append(0)
        k += 1
        if k > 200000:
            print("ITER>200000 (still looping)"); return
    print(" ".join(map(str, a)))
main()
