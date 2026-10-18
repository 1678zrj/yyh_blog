import os, re, sys, io

MD = r"D:\mycode\python\my_blog\interview\exam\4-计算机专业\07-机试编程题.md"
TMP = os.path.dirname(os.path.abspath(__file__))
text = open(MD, encoding="utf-8").read()

blocks = re.findall(r"```python\n(.*?)```", text, re.S)
sol = [b for b in blocks if "def main" in b]
print("total code blocks:", len(blocks), "complete scripts:", len(sol))

labels = ["IO-1", "IO-2", "IO-3a", "IO-3b", "S-1", "S-2", "S-3",
          "A-1", "A-2", "A-3", "M-1", "M-2", "H-1",
          "B-1", "B-2", "B-3", "D-1", "D-2", "D-3"]
assert len(sol) == len(labels), (len(sol), len(labels))

os.makedirs(os.path.join(TMP, "sol"), exist_ok=True)
for lab, src in zip(labels, sol):
    fn = os.path.join(TMP, "sol", lab.replace("-", "_") + ".py")
    open(fn, "w", encoding="utf-8", newline="\n").write(src)
    print(lab, "->", len(src.splitlines()), "lines")
