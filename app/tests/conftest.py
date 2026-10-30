import os
import sys

# Make `fragdex` and the test helpers importable when pytest runs from the repo root.
HERE = os.path.dirname(__file__)
for path in (os.path.dirname(HERE), HERE):
    if path not in sys.path:
        sys.path.insert(0, path)
