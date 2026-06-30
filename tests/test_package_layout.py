import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class PackageLayoutTests(unittest.TestCase):
    def test_version_files(self):
        version=(ROOT/'VERSION').read_text(encoding='utf-8').strip()
        init=(ROOT/'src/astock_terminal/__init__.py').read_text(encoding='utf-8')
        self.assertIn(version,init)
    def test_provider_modules(self):
        self.assertTrue((ROOT/'src/astock_terminal/providers/tencent.py').exists())
        self.assertTrue((ROOT/'src/astock_terminal/providers/mootdx_provider.py').exists())
        self.assertTrue((ROOT/'src/astock_terminal/providers/a_stock_data.py').exists())
    def test_no_runtime_db_in_source(self):
        self.assertFalse((ROOT/'data/terminal.db').exists())

if __name__=='__main__': unittest.main()
