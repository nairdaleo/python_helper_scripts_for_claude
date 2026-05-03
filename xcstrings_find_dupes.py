#!/usr/bin/env python3
"""
xcstrings_find_dupes.py
Find repeated English values and stale entries in an .xcstrings file.

Usage:
    python3 xcstrings_find_dupes.py --xcstrings path/to/Localizable.xcstrings \
        [--source-locale en] \
        [--show-stale]

What it does:
  - Finds keys where the source-locale value is identical (case-insensitive optional)
  - Reports stale keys (extractionState == "stale") with their current value
  - Helps spot near-miss duplicates that waste translation budget and confuse Xcode

Output:
  DUPLICATE VALUES  — multiple keys with the same English text
  STALE KEYS        — Xcode marked these as stale (no longer extracted from source)
"""

import argparse
import json
from collections import defaultdict


def main():
    parser = argparse.ArgumentParser(
        description="Find duplicate values and stale keys in .xcstrings"
    )
    parser.add_argument("--xcstrings", required=True, help="Path to Localizable.xcstrings")
    parser.add_argument("--source-locale", default="en",
                        help="Locale to use as the comparison source (default: en)")
    parser.add_argument("--show-stale", action="store_true",
                        help="Also show all stale keys (default: always shown)")
    parser.add_argument("--case-sensitive", action="store_true",
                        help="Treat 'Foo' and 'foo' as different values (default: case-insensitive)")
    args = parser.parse_args()

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    strings = data["strings"]
    source = args.source_locale

    # ── 1. Find duplicate source values ──────────────────────────────────────
    value_to_keys = defaultdict(list)
    stale_keys = []
    no_source = []

    for key, entry in strings.items():
        # Track stale
        if entry.get("extractionState") == "stale":
            stale_keys.append(key)

        locs = entry.get("localizations", {})
        src_unit = locs.get(source, {}).get("stringUnit", {})
        value = src_unit.get("value")

        if value is None:
            # Could be a key that IS its own value (sourceLanguage key == value)
            # Xcode omits the stringUnit when key == value for the source locale
            value = key

        norm = value if args.case_sensitive else value.lower()
        value_to_keys[norm].append((key, value))

    dupes = {v: keys for v, keys in value_to_keys.items() if len(keys) > 1}

    # ── 2. Print report ───────────────────────────────────────────────────────
    print("=" * 70)
    print("XCSTRINGS DUPLICATE VALUE FINDER")
    print(f"  File          : {args.xcstrings}")
    print(f"  Source locale : {source}")
    print(f"  Total keys    : {len(strings)}")
    print("=" * 70)

    # Duplicates
    print(f"\n── DUPLICATE VALUES ({len(dupes)} groups) ──────────────────────────────")
    if not dupes:
        print("  None found. ✓")
    else:
        for norm_val, key_pairs in sorted(dupes.items(), key=lambda x: -len(x[1])):
            display_val = key_pairs[0][1]
            print(f"\n  Value: {repr(display_val)}")
            print(f"  ({len(key_pairs)} keys share this value)")
            for key, _ in key_pairs:
                entry = strings[key]
                extraction = entry.get("extractionState", "extracted_with_value")
                comment = entry.get("comment", "")
                flag = ""
                if extraction == "stale":
                    flag = "  ⚠ STALE"
                elif extraction == "manual":
                    flag = "  (manual)"
                print(f"    • {repr(key)}{flag}")
                if comment:
                    print(f"      # {comment}")

    # Stale keys
    print(f"\n── STALE KEYS ({len(stale_keys)}) ─────────────────────────────────────────")
    if not stale_keys:
        print("  None. ✓")
    else:
        for key in sorted(stale_keys):
            entry = strings[key]
            locs = entry.get("localizations", {})
            src_unit = locs.get(source, {}).get("stringUnit", {})
            value = src_unit.get("value", key)
            locale_count = len(locs)
            comment = entry.get("comment", "")
            print(f"  • {repr(key)}")
            print(f"    value: {repr(value[:80])}")
            print(f"    locales: {locale_count}  {'# ' + comment if comment else ''}")

    # Summary
    print(f"\n── SUMMARY ───────────────────────────────────────────────────────────")
    dupe_key_count = sum(len(v) for v in dupes.values())
    print(f"  Duplicate groups : {len(dupes)}  ({dupe_key_count} keys involved)")
    print(f"  Stale keys       : {len(stale_keys)}")
    print(f"  Keys with issues : {dupe_key_count + len(stale_keys) - len(dupes)}")
    if dupes:
        print(f"\n  Tip: For each duplicate group, pick one canonical key and delete the rest.")
        print(f"       Use Xcode's Find & Replace to update call sites.")
    if stale_keys:
        print(f"\n  Tip: Stale keys = Xcode can't find them in source anymore.")
        print(f"       Either delete them or set extractionState = 'manual' to silence the warning.")
    print("=" * 70)


if __name__ == "__main__":
    main()
