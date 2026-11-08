#!/usr/bin/env python3
"""Count cards in data/cards.jsonl (or an alternate path) whose
first_message starts with a harness prefix (extract.HARNESS_PREFIXES,
after stripping leading whitespace), plus cards with no first_message at
all. Stdlib only; reads the cards file read-only; prints aggregates only.

Usage: harness_first_count.py [cards.jsonl]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import extract


def main():
    default = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "cards.jsonl")
    path = sys.argv[1] if len(sys.argv) > 1 else default
    total = harness = none = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            c = json.loads(line)
            total += 1
            fm = c.get("first_message")
            if fm is None:
                none += 1
            elif extract.starts_with_harness_prefix(fm):
                harness += 1
    print("cards: %d  first_message with harness prefix: %d  "
          "no first_message: %d" % (total, harness, none))


if __name__ == "__main__":
    main()
