import sys
def main():
    data = sys.stdin.buffer.read().split()
    n = int(data[0])
    out = [str(len(t.decode())) for t in data[1:1 + n]]
    sys.stdout.write("\n".join(out) + "\n")
main()
