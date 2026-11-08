import sys
from pathlib import Path

# make the scripts in the repository root importable from the tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
