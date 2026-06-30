"""兼容旧版启动方式：python web_app.py。"""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
if __name__ == "__main__":
    os.environ.setdefault("ASTOCK_HOME", str(ROOT))
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from astock_terminal.app import *  # noqa: F401,F403,E402
from astock_terminal.app import main  # noqa: E402

if __name__ == "__main__":
    main()
