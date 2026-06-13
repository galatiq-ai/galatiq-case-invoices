import importlib, sys, traceback
from pathlib import Path

# Ensure project root is on sys.path so top-level modules (e.g., tools.py) are importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    m = importlib.import_module('tools')
    print('tools loaded, members:', [n for n in dir(m) if not n.startswith('_')])
    # manual approval has been removed; list members only
except Exception as e:
    print('ERROR', type(e).__name__, e)
    traceback.print_exc()
