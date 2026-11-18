"""Lets the draft's test import the legacy code; written by `qaos legacy wrap`.

It also builds a throwaway copy of the fund's warehouse, because kestrel_quant reads the
warehouse named by KPC_WAREHOUSE when it first connects.
"""

import os
import sys
import tempfile
from pathlib import Path

# Delete these lines once the legacy package is installed in this environment.
ROOT = Path(__file__).parent / "../../facility"
sys.path.insert(0, str(ROOT.resolve()))
sys.path.insert(0, str((ROOT / "warehouse").resolve()))
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))  # site/: warehouse_store

if "KPC_WAREHOUSE" not in os.environ:
    from build_warehouse import build

    warehouse = Path(tempfile.mkdtemp(prefix="kpc-warehouse-")) / "kestrel.db"
    build(warehouse)
    os.environ["KPC_WAREHOUSE"] = str(warehouse)
