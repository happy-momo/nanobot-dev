"""Test-path bootstrap: make `edge_adviser` (the inner package) importable.

`edge_adviser/tests/` sits inside the edge_adviser *subproject* directory whose
own package is `edge_adviser/edge_adviser/`. pytest's rootdir insertion does not
cover it (the repo root is the nanobot package), so every test module previously
bootstrapped `sys.path` by hand — and only when it remembered. One conftest,
one rule: PYTHONPATH includes the subproject root before any module imports.
"""
from pathlib import Path
import sys

_SUBPROJECT = Path(__file__).resolve().parent.parent   # edge_adviser/
if str(_SUBPROJECT) not in sys.path:
    sys.path.insert(0, str(_SUBPROJECT))
