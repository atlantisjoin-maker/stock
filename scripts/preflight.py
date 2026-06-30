from __future__ import annotations
import json, os, py_compile, subprocess, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
issues=[]
for p in list((ROOT/"src").rglob("*.py"))+list((ROOT/"tests").rglob("*.py")):
    try: py_compile.compile(str(p),doraise=True)
    except Exception as exc: issues.append(f"compile {p.relative_to(ROOT)}: {exc}")
try:
    subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-v"],cwd=ROOT,check=True,env={**os.environ,"PYTHONPATH":str(ROOT/"src")})
except Exception as exc: issues.append(f"tests: {exc}")
result={"ok":not issues,"python":sys.version,"issues":issues}
(ROOT/"PREFLIGHT_REPORT.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result,ensure_ascii=False,indent=2))
raise SystemExit(0 if not issues else 1)
