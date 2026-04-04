#!/usr/bin/env python3
"""
xcstrings_validate_specifiers.py
Validate that format specifiers in translations match the source string.

Catches:
  - percent-la instead of percent-lld  (broke Chlorophyll build)
  - percent-1-dollar-llds  (specifier fused with Spanish suffix)
  - percent-llas  (DeepL merged length modifier with article 'las')
  - Mismatched specifier count/type between source and target

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

# Swift/ObjC-valid format specifiers
# Excludes %a/%A (hex float — valid C but not used in Swift format strings)
SWIFT_VALID_RE = re.compile(
    r'%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEgfscpSFEG@]'
)

# After stripping valid specifiers, any remaining % sequences are suspicious
LEFTOVER_SPEC_RE = re.compile(
    r'%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)*[a-zA-Z@]+'
)

# Valid specifier immediately followed by letters = fused suffix
# e.g. %1$lld + "s" from "las" -> __OK__s
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

    if not all_issues:
        print(f"All {len(strings)} strings clean.")
        print("=" * 70)
        sys.exit(0)

    print(f"{len(all_issues)} string(s) with specifier issues:\n")
    for key, issues in all_issues:
        print(f"KEY: {repr(key)}")
        for issue in issues:
            print(issue)
        print()

    print("=" * 70)
    print(f"FAILED — {len(all_issues)} issue(s).")
    print()
    print("Common causes:")
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
