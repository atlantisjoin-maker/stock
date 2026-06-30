from __future__ import annotations
import hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={".git",".venv","__pycache__","build","dist","backups"}
rows=[]
for path in sorted(ROOT.rglob("*")):
    if not path.is_file() or any(part in EXCLUDE for part in path.parts): continue
    if path.name=="PROJECT_MANIFEST.json": continue
    rel=path.relative_to(ROOT).as_posix()
    rows.append({"path":rel,"size":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()})
payload={"version":(ROOT/"VERSION").read_text().strip(),"files":rows}
(ROOT/"PROJECT_MANIFEST.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(f"manifest: {len(rows)} files")
