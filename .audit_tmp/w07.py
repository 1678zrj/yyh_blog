import sys
from collections import Counter
def main():
    s = sys.stdin.readline().strip()
    for ch, c in Counter(s).most_common(3):
        print(ch, c)
main()
