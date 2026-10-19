"""Versioned execution-aware research tools; no production physics edits."""
from pathlib import Path
import sys
_src=str(Path(__file__).resolve().parents[2]/'src')
if _src not in sys.path:sys.path.insert(0,_src)
