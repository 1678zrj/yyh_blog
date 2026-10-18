import sys
def main():
    data = sys.stdin.buffer.read().split()
    t = int(data[0]); out = []
    for k in range(t):
        out.append("Case #%d: %d" % (k, int(data[2*k+1]) + int(data[2*k+2])))
    sys.stdout.write("\n".join(out) + "\n")
main()
