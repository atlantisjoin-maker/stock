from __future__ import annotations
import argparse, shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ROOT/"backups"))
    args=parser.parse_args()
    dest=Path(args.output)/datetime.now().strftime("%Y%m%d_%H%M%S")
    dest.mkdir(parents=True, exist_ok=True)
    for name in ["config.json", "data/terminal.db"]:
        src=ROOT/name
        if src.exists():
            target=dest/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src,target)
    print(dest)

if __name__=="__main__": main()
