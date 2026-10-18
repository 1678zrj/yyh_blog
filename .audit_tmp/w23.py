import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(1, t + 1):
        out.append("Case #%d: %d" % (k, int(data[2*k-1]) + int(data[2*k])))
    sys.stdout.write("\n\n".join(out) + "\n\n")
main()
