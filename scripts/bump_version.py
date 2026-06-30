from __future__ import annotations
import argparse, re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument("version");a=p.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+",a.version): raise SystemExit("Use semantic version, e.g. 4.2.0")
    (ROOT/"VERSION").write_text(a.version+"\n",encoding="utf-8")
    init=ROOT/"src/astock_terminal/__init__.py"
    init.write_text(re.sub(r'__version__ = "[^"]+"',f'__version__ = "{a.version}"',init.read_text()),encoding="utf-8")
    pyproject=ROOT/"pyproject.toml"
    pyproject.write_text(re.sub(r'version = "[^"]+"',f'version = "{a.version}"',pyproject.read_text(),count=1),encoding="utf-8")
    print(a.version)
if __name__=="__main__":main()
