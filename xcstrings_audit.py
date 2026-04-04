#!/usr/bin/env python3
"""
xcstrings_audit.py
Report language coverage statistics for an .xcstrings file.

Usage:
    python3 xcstrings_audit.py --xcstrings path/to/Localizable.xcstrings \
        [--source-locale en] \
        [--target-locale es-MX] \
        [--show-missing] \
        [--show-needs-review]

Output includes:
  - Total string count
  - How many have both source + target translations
  - How many have only one language
  - Optionally lists keys that are missing translations
  - Lists keys marked 'needs-review' (machine-translated)
"""

import argparse
import json


def main():
    parser = argparse.ArgumentParser(description="Audit language coverage in .xcstrings")
    parser.add_argument("--xcstrings", required=True, help="Path to Localizable.xcstrings")
    parser.add_argument("--source-locale", default="en",
                        help="xcstrings locale key for source language (default: en)")
    parser.add_argument("--target-locale", default="es-MX",
                        help="xcstrings locale key for target language (default: es-MX)")
    parser.add_argument("--show-missing", action="store_true",
                        help="Print keys missing the target translation")
    parser.add_argument("--show-needs-review", action="store_true",
                        help="Print keys marked needs-review in target locale")
    args = parser.parse_args()

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    source = args.source_locale
    target = args.target_locale
    strings = data["strings"]

    has_both = []
    source_only = []
    target_only = []
    neither = []
    needs_review = []

    for key, entry in strings.items():
        locs = entry.get("localizations", {})
        has_source = source in locs
        has_target = target in locs

        if has_source and has_target:
            has_both.append(key)
            state = locs[target].get("stringUnit", {}).get("state", "")
            if state == "needs-review":
                needs_review.append(key)
        elif has_source:
            source_only.append(key)
        elif has_target:
            target_only.append(key)
        else:
            neither.append(key)

    total = len(strings)
    coverage_pct = len(has_both) * 100 / total if total else 0

    print("=" * 70)
    print("XCSTRINGS LOCALIZATION AUDIT")
    print(f"  File          : {args.xcstrings}")
    print(f"  Source locale : {source}")
    print(f"  Target locale : {target}")
    print("=" * 70)
    print(f"\nTotal strings: {total}")
    print(f"\nLanguage coverage:")
    print(f"  Both {source} + {target}: {len(has_both)} ({coverage_pct:.1f}%)")
    print(f"  {source} only          : {len(source_only)}")
    print(f"  {target} only          : {len(target_only)}")
    print(f"  Neither                : {len(neither)}")
    print(f"\nTranslation quality:")
    print(f"  Needs review (machine-translated): {len(needs_review)}")
    print(f"  Human-verified                   : {len(has_both) - len(needs_review)}")

    if args.show_missing and source_only:
        print(f"\nMissing {target} translation ({len(source_only)} strings):")
        for key in source_only:
            src_val = strings[key]["localizations"][source].get("stringUnit", {}).get("value", "")
            print(f"  {repr(key)}")
            if src_val:
                print(f"    {source}: {repr(src_val[:80])}")

    if args.show_needs_review and needs_review:
        print(f"\nNeeds review — {target} ({len(needs_review)} strings):")
        for key in needs_review:
            locs = strings[key]["localizations"]
            src_val = locs.get(source, {}).get("stringUnit", {}).get("value", "")
            tgt_val = locs.get(target, {}).get("stringUnit", {}).get("value", "")
            print(f"  {repr(key)}")
            if src_val:
                print(f"    {source}     : {repr(src_val[:70])}")
            if tgt_val:
                print(f"    {target}: {repr(tgt_val[:70])}")

    print()
    if len(has_both) == total:
        print("All strings have both languages.")
    else:
        missing_count = len(source_only) + len(target_only)
        print(f"{missing_count} string(s) are incomplete.")
        print("Run xcstrings_fix_with_deepl.py to fill in the gaps.")
    print("=" * 70)


if __name__ == "__main__":
    main()
