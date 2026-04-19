#!/usr/bin/env python3
"""
xcstrings_audit.py
Report language coverage statistics for an .xcstrings file.
Optionally scans Swift/ObjC source to detect stale (unreferenced) keys.

Usage:
    python3 xcstrings_audit.py --xcstrings path/to/Localizable.xcstrings \
        [--source-locale en] \
        [--target-locale es-MX] \
        [--show-missing] \
        [--show-needs-review] \
        [--source-root path/to/swift/sources]   # enables stale-key detection

Stale-key detection:
  Reads all .swift/.m files under --source-root and flags keys that appear
  in no source file. Handles three reference styles:
    1. Literal key:    "My Key"  /  String(localized: "My Key")
    2. SwiftUI interp: Text("\\(val) suffix")  ->  key "%@ suffix"
    3. Dynamic prefix: LocalizedStringKey("prefix." + variable)
                       -> all keys sharing that prefix are kept

  Keys marked shouldTranslate=false are always skipped.
"""

import argparse
import json
import os
import re
import sys


# ---------------------------------------------------------------------------
# Stale-key helpers
# ---------------------------------------------------------------------------

SPEC_RE = re.compile(r'%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEgfscpSFEG@]')


def _load_corpus(source_root):
    """Read all .swift / .m files under source_root into one string."""
    chunks = []
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in {'.git', 'DerivedData', 'build', '.build'}]
        for fn in files:
            if fn.endswith(('.swift', '.m')):
                try:
                    with open(os.path.join(root, fn), encoding='utf-8', errors='replace') as fh:
                        chunks.append(fh.read())
                except OSError:
                    pass
    return '\n'.join(chunks)


def _extract_dynamic_prefixes(corpus):
    """
    Find patterns like:
        LocalizedStringKey("theme.tagline." + x)
        String(localized: "ns." + variable)
    and return the literal prefix strings so we can whitelist all keys
    that start with them.
    """
    # Match quoted-string + concatenation (common Swift pattern)
    pattern = re.compile(r'"([^"]+)"\s*\+')
    return [m.group(1) for m in pattern.finditer(corpus)]


def classify_stale(keys, strings, corpus):
    """
    Return (stale, dynamic_kept) lists.
    - stale: translateable keys with no detectable source reference
    - dynamic_kept: keys kept because of dynamic-prefix or interpolation match
    """
    dynamic_prefixes = _extract_dynamic_prefixes(corpus)

    stale = []
    dynamic_kept = []

    for key in keys:
        # 1. Literal match
        if key in corpus:
            continue

        # 2. Dynamic prefix match
        if any(key.startswith(p) for p in dynamic_prefixes if len(p) >= 3):
            dynamic_kept.append(key)
            continue

        # 3. SwiftUI interpolation: key has specifiers, check if all
        #    significant literal fragments appear in source
        if SPEC_RE.search(key):
            parts = SPEC_RE.split(key)
            significant = [p for p in parts if len(p.strip()) >= 4]
            if significant and all(p in corpus for p in significant):
                dynamic_kept.append(key)
                continue

        stale.append(key)

    return stale, dynamic_kept


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
    parser.add_argument("--source-root", default=None,
                        help="Root of Swift/ObjC source tree — enables stale-key detection")
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
    print(f"  Neither (shouldTranslate=false): {len(neither)}")
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

    # --- Stale key detection ---
    if args.source_root:
        print()
        print("─" * 70)
        print("STALE KEY DETECTION")
        print(f"  Source root: {args.source_root}")
        print("─" * 70)

        skip_keys = {k for k, v in strings.items() if v.get('shouldTranslate') is False}
        translateable = [k for k in strings if k not in skip_keys]

        corpus = _load_corpus(args.source_root)
        swift_files = sum(
            1 for root, _, files in os.walk(args.source_root)
            for fn in files if fn.endswith(('.swift', '.m'))
        )
        print(f"  Scanned {swift_files} source file(s), {len(corpus):,} chars\n")

        stale, dynamic_kept = classify_stale(translateable, strings, corpus)

        if stale:
            print(f"  Stale keys — no source reference found ({len(stale)}):")
            for k in sorted(stale):
                locs = strings[k].get('localizations', {})
                en_val = locs.get('en', {}).get('stringUnit', {}).get('value', '')
                print(f"    {repr(k)}")
                if en_val and en_val != k:
                    print(f"      en: {repr(en_val[:70])}")
            print()
            print("  Fix: delete these keys from .xcstrings, or re-wire source references.")
        else:
            print(f"  No stale keys found. All {len(translateable)} translateable keys are referenced.")

        print(f"\n  Dynamic/interpolated keys kept: {len(dynamic_kept)}")

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
