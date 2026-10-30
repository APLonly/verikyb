"""Print every Makefile target that has a trailing '## description' comment."""

import re
import sys

PATTERN = re.compile(r"^([a-zA-Z_-]+):.*## (.*)")

for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            match = PATTERN.match(line)
            if match:
                print(f"  {match[1]:<14} {match[2]}")
