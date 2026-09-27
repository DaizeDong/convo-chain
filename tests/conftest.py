"""Run the suite against the source tree, not whatever copy happens to be installed.

Putting src/ first on sys.path means `python -B -m pytest tests/` from a fresh clone tests these
bytes without an install step. A stale installed copy winning the import would make the suite
certify code that is not in this checkout.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
sys.path.insert(0, SRC)
