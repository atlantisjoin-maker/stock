from __future__ import annotations
import hashlib, json, shutil, subprocess, sys, zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
VERSION=(ROOT/"VERSION").read_text().strip()
DIST=ROOT/"dist"
DIST.mkdir(exist_ok=True)

subprocess.run([sys.executable,"scripts/generate_manifest.py"],cwd=ROOT,check=True)
subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],cwd=ROOT,check=True,env={**__import__('os').environ,"PYTHONPATH":str(ROOT/"src")})

source_zip=DIST/f"AStockWebTerminal_{VERSION}_source.zip"
runtime_zip=DIST/f"AStockWebTerminal_{VERSION}_runtime.zip"
exclude={".git",".venv","__pycache__","build","dist","backups"}

def add_tree(z, runtime=False):
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or any(part in exclude for part in p.parts): continue
        rel=p.relative_to(ROOT)
        if runtime and (rel.parts[0] in {"docs","tests",".github","scripts"} or rel.name in {"CONTRIBUTING.md","DEVELOPMENT.md","RELEASE.md","MAINTAINER_HANDOFF.md"}):
            continue
        if rel.as_posix()=="data/terminal.db": continue
        z.write(p,rel.as_posix())

with zipfile.ZipFile(source_zip,"w",zipfile.ZIP_DEFLATED) as z: add_tree(z,False)
with zipfile.ZipFile(runtime_zip,"w",zipfile.ZIP_DEFLATED) as z: add_tree(z,True)
for p in [source_zip,runtime_zip]:
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    (Path(str(p)+".sha256")).write_text(f"{sha}  {p.name}\n",encoding="utf-8")
    print(p,sha)
