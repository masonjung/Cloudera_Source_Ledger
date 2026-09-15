"""Quick demo of the source_ledger library."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "retrieval"))

from source_ledger import text_to_urls

if __name__ == "__main__":
    for url in text_to_urls("Cloudera CDP supports use cases", max_results=16):
        print(url)
