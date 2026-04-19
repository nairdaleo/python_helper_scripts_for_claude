#!/usr/bin/env python3
"""
xcstrings_validate_specifiers.py
Validate that format specifiers in translations match the source string.
Also detects Swift symbol-generation collisions between keys.

Catches:
  - percent-la instead of percent-lld  (broke Chlorophyll build)
  - percent-1-dollar-llds  (specifier fused with Spanish suffix)
  - percent-llas  (DeepL merged length modifier with article 'las')
  - Mismatched specifier count/type between source and target
  - Two keys that Xcode would generate the same Swift symbol for

Usage:
    python3 xcstrings_validate_specifiers.py \
        --xcstrings path/to/Localizable.xcstrings \
        [--source-locale en] \
        [--target-locale es-MX]

Exit 0 = clean. Exit 1 = issues found (build will likely fail).
"""

import argparse
import json
import re
import sys
from collections import defaultdict

# Swift/ObjC-valid format specifiers
SWIFT_VALID_RE = re.compile(
    r'%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEgfscpSFEG@]'
)

# After stripping valid specifiers, any remaining % sequences are suspicious
LEFTOVER_SPEC_RE = re.compile(
    r'%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)*[a-zA-Z@]+'
)

# Valid specifier immediately followed by letters = fused suffix
FUSED_SUFFIX_RE = re.compile(r'__OK__([a-zA-Z]+)')


def find_specifier_issues(value):
    """Return (suspicious_specs, fused_suffixes). Both empty = clean."""
    stripped = SWIFT_VALID_RE.sub('__OK__', value)
    suspicious = LEFTOVER_SPEC_RE.findall(stripped)
    fused = FUSED_SUFFIX_RE.findall(stripped)
    return suspicious, fused


def normalize_specs(specs):
    """Strip positional index for order-independent comparison."""
    return sorted(re.sub(r'\d+\$', '', s) for s in specs)


def check_pair(source_val, target_val):
    """Return list of issue strings for this source/target pair."""
    issues = []
    for val, label in [(source_val, 'source'), (target_val, 'target')]:
        suspicious, fused = find_specifier_issues(val)
        if suspicious:
            issues.append(
                f"  Invalid specifier in {label}: {suspicious}\n"
                f"    {repr(val[:100])}"
            )
        if fused:
            issues.append(
                f"  Fused suffix in {label}: suffix={fused}\n"
                f"    {repr(val[:100])}"
            )
    src_specs = SWIFT_VALID_RE.findall(source_val)
    tgt_specs = SWIFT_VALID_RE.findall(target_val)
    if normalize_specs(src_specs) != normalize_specs(tgt_specs):
        issues.append(
            f"  Specifier mismatch:\n"
            f"    source: {src_specs}  {repr(source_val[:80])}\n"
            f"    target: {tgt_specs}  {repr(target_val[:80])}"
        )
    return issues


def key_to_swift_symbol(key):
    """
    Approximate Xcode's Swift symbol generation from a string key.
    Splits on non-alphanumeric runs and camelCases the result.
      "Share Photo…"   -> "sharePhoto"
      "Share Photo Screen" -> "sharePhotoScreen"
      "cancel"         -> "cancel"
    """
    words = [w for w in re.split(r'[^a-zA-Z0-9]+', key) if w]
    if not words:
        return ''
    symbol = words[0][0].lower() + words[0][1:] if len(words[0]) > 1 else words[0].lower()
    for word in words[1:]:
        symbol += word[0].upper() + word[1:] if len(word) > 1 else word.upper()
    return symbol


def find_symbol_collisions(strings):
    """
    Return list of (symbol, [key1, key2, ...]) for keys that map to the
    same Swift symbol. Skips shouldTranslate=false entries.
    """
    symbol_map = defaultdict(list)
    for key, entry in strings.items():
        if entry.get('shouldTranslate') is False:
            continue
        sym = key_to_swift_symbol(key)
        if sym:
            symbol_map[sym].append(key)
    return [(sym, keys) for sym, keys in symbol_map.items() if len(keys) > 1]


def main():
    parser = argparse.ArgumentParser(
        description="Validate format specifier consistency in .xcstrings"
    )
    parser.add_argument("--xcstrings", required=True)
    parser.add_argument("--source-locale", default="en")
    parser.add_argument("--target-locale", default="es-MX")
    args = parser.parse_args()

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    source = args.source_locale
    target = args.target_locale
    strings = data["strings"]

    print("=" * 70)
    print("XCSTRINGS FORMAT SPECIFIER VALIDATION")
    print(f"  File   : {args.xcstrings}")
    print(f"  Source : {source}    Target : {target}")
    print("=" * 70)
    print()

    # --- Specifier checks ---
    all_issues = []
    for key, entry in strings.items():
        locs = entry.get("localizations", {})
        src = locs.get(source, {}).get("stringUnit", {}).get("value", "")
        tgt = locs.get(target, {}).get("stringUnit", {}).get("value", "")
        if not src or not tgt:
            continue
        issues = check_pair(src, tgt)
        if issues:
            all_issues.append((key, issues))

    # --- Symbol collision check (locale-agnostic, runs once) ---
    collisions = find_symbol_collisions(strings)
    total_failures = len(all_issues) + len(collisions)

    if not total_failures:
        print(f"All {len(strings)} strings clean.")
        print("=" * 70)
        sys.exit(0)

    if all_issues:
        print(f"{len(all_issues)} string(s) with specifier issues:\n")
        for key, issues in all_issues:
            print(f"KEY: {repr(key)}")
            for issue in issues:
                print(issue)
            print()

    if collisions:
        print(f"{len(collisions)} Swift symbol collision(s):\n")
        for sym, keys in collisions:
            print(f"  Symbol '.{sym}' generated by {len(keys)} keys:")
            for k in keys:
                print(f"    {repr(k)}")
        print()
        print("Fix: rename one key in source + .xcstrings (keep EN display value),")
        print("     or add a suffix that produces a unique symbol.")

    print()
    print("=" * 70)
    print(f"FAILED — {total_failures} issue(s).")
    if all_issues:
        print()
        print("Common specifier causes:")
        print("  %llas  — DeepL merged ll with Spanish article 'las'")
        print("  %1$la  — wrong terminal; should be %1$lld")
        print("  fused  — specifier immediately followed by 's'/'as' from adjacent word")
        print()
        print("Fix: re-run xcstrings_fix_with_deepl.py (uses tag_handling=xml to protect")
        print("     specifiers), or manually patch in .xcstrings JSON.")
    print("=" * 70)
    sys.exit(1)


if __name__ == "__main__":
    main()
