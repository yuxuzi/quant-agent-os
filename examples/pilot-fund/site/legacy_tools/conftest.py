"""Lets the draft's test import the legacy code; written by `qaos legacy wrap`."""

import sys
from pathlib import Path

# Delete these lines once the legacy package is installed in this environment.
ROOT = Path(__file__).parent / "../../facility"
sys.path.insert(0, str(ROOT.resolve()))
