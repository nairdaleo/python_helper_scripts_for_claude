#!/usr/bin/env python3
"""
xcstrings_fix_with_deepl.py
Fill in the missing language for strings that only have one language in .xcstrings.

Usage:
    python3 xcstrings_fix_with_deepl.py --xcstrings path/to/Localizable.xcstrings \
        --api-key YOUR_DEEPL_KEY \
        [--source-lang EN] \
        [--target-lang ES] \
        [--source-locale en] \
        [--target-locale es-MX] \
        [--dry-run]

What it does:
  - Strings with ONLY the target locale: translates back to source locale.
  - Strings with ONLY the source locale: translates forward to target locale
    (marked as 'needs-review' in the output so they can be verified).
  - Strings with both locales: left unchanged.
"""

import argparse
import re
import json
import sys
import time
import urllib.request
import urllib.parse


# Format specifier regex — matches %lld, %@, %1$lld, etc.
_SPEC_RE = re.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])')


def _protect(s):
    """Wrap format specifiers in XML tags so DeepL leaves them untouched.
    Like a C macro that protects tokens before the preprocessor runs."""
    parts = _SPEC_RE.split(s)
    out, idx = [], 0
    for part in parts:
        if _SPEC_RE.fullmatch(part):
            out.append(f'<x id="{idx}">{part}</x>'); idx += 1
        else:
            out.append(part)
    return ''.join(out)


def _unprotect(s):
    """Remove XML wrapper tags, restoring original specifiers."""
    return re.sub(r'<x id="\d+">(.*?)</x>', r'\1', s)


def deepl_translate(text, api_key, source_lang, target_lang, formality="less"):
    """Translate text using DeepL. Returns translated string or None on error.
    Uses tag_handling=xml to protect format specifiers (%lld, %@, etc.)."""
    protected = _protect(text)
    params = urllib.parse.urlencode({
        "text": protected,
        "source_lang": source_lang.upper(),
        "target_lang": target_lang.upper(),
        "tag_handling": "xml",
        "formality": formality,
    }).encode()

    req = urllib.request.Request(
        "https://api-free.deepl.com/v2/translate",
        data=params,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return _unprotect(result["translations"][0]["text"])
    except Exception:
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Fill missing translations in .xcstrings using DeepL"
    )
    parser.add_argument("--xcstrings", required=True, help="Path to Localizable.xcstrings")
    parser.add_argument("--api-key", required=True, help="DeepL API key")
    parser.add_argument("--source-lang", default="EN",
                        help="DeepL source language code (default: EN)")
    parser.add_argument("--target-lang", default="ES",
                        help="DeepL target language code (default: ES). Use ES-419 for Latin American Spanish (recommended for es-MX)")
    parser.add_argument("--source-locale", default=None,
                        help="xcstrings locale key for source language (default: lowercase source-lang)")
    parser.add_argument("--target-locale", default=None,
                        help="xcstrings locale key for target language (default: lowercase target-lang, "
                             "e.g. 'es-MX')")
    parser.add_argument("--formality", default="less",
                        help="DeepL formality (less/more/default). Use \'less\' for app UI tú-form (default: less)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would change without modifying the file")
    args = parser.parse_args()

    source_locale = args.source_locale or args.source_lang.lower()
    target_locale = args.target_locale or args.target_lang.lower()

    if source_locale == target_locale:
        print(f"ERROR: source-locale and target-locale are both '{source_locale}'.")
        print("This would overwrite the source strings, breaking xcstrings key matching.")
        print("Tip: use --target-locale es-MX --target-lang ES-419 for Spanish.")
        sys.exit(1)

    print("=" * 70)
    print("FIXING LOCALIZATION WITH DEEPL")
    print(f"  xcstrings    : {args.xcstrings}")
    print(f"  source locale: {source_locale} ({args.source_lang})")
    print(f"  target locale: {target_locale} ({args.target_lang})")
    if args.dry_run:
        print("  DRY RUN — no files will be modified")
    print("=" * 70)

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    complete = {}
    source_only = {}
    target_only = {}

    for key, entry in data["strings"].items():
        locs = entry.get("localizations", {})
        has_source = source_locale in locs
        has_target = target_locale in locs

        if has_source and has_target:
            complete[key] = entry
        elif has_source:
            source_only[key] = entry
        elif has_target:
            target_only[key] = entry

    print(f"\nCurrent state:")
    print(f"  Complete ({source_locale} + {target_locale}): {len(complete)}")
    print(f"  {source_locale} only: {len(source_only)}")
    print(f"  {target_locale} only: {len(target_only)}")

    rebuilt = dict(data)  # preserve top-level keys like sourceLanguage, version
    rebuilt["strings"] = {}

    # Pass through complete strings unchanged
    for key, entry in complete.items():
        rebuilt["strings"][key] = entry

    # Fill in source for target-only strings
    print(f"\nTranslating {len(target_only)} {target_locale}-only strings → {source_locale}...")
    for i, (key, entry) in enumerate(target_only.items(), 1):
        target_text = entry["localizations"][target_locale]["stringUnit"]["value"]
        translated = deepl_translate(target_text, args.api_key, args.target_lang, args.source_lang, formality=args.formality)

        if translated:
            new_entry = dict(entry)
            new_entry["localizations"] = {
                source_locale: {"stringUnit": {"state": "translated", "value": translated}},
                target_locale: entry["localizations"][target_locale],
            }
            rebuilt["strings"][key] = new_entry
            print(f"  [{i}] {repr(key)[:60]} → {repr(translated)[:60]}")
        else:
            rebuilt["strings"][key] = entry
            print(f"  [{i}] FAILED: {repr(key)[:60]}")

        if i < len(target_only):
            time.sleep(0.1)

    # Fill in target for source-only strings
    print(f"\nTranslating {len(source_only)} {source_locale}-only strings → {target_locale}...")
    for i, (key, entry) in enumerate(source_only.items(), 1):
        source_text = entry["localizations"][source_locale]["stringUnit"]["value"]
        translated = deepl_translate(source_text, args.api_key, args.source_lang, args.target_lang, formality=args.formality)

        if translated:
            new_entry = dict(entry)
            new_entry["localizations"] = {
                source_locale: entry["localizations"][source_locale],
                target_locale: {"stringUnit": {"state": "needs-review", "value": translated}},
            }
            rebuilt["strings"][key] = new_entry
            print(f"  [{i}] {repr(key)[:60]} → {repr(translated)[:60]}")
        else:
            rebuilt["strings"][key] = entry
            print(f"  [{i}] FAILED: {repr(key)[:60]}")

        if i < len(source_only):
            time.sleep(0.1)

    if not args.dry_run:
        with open(args.xcstrings, "w", encoding="utf-8") as f:
            json.dump(rebuilt, f, indent=2, ensure_ascii=False)
            f.write("\n")

    # Summary
    has_both = sum(
        1 for e in rebuilt["strings"].values()
        if source_locale in e.get("localizations", {}) and target_locale in e.get("localizations", {})
    )
    needs_review = sum(
        1 for e in rebuilt["strings"].values()
        if e.get("localizations", {}).get(target_locale, {}).get("stringUnit", {}).get("state") == "needs-review"
    )

    print(f"\n{'=' * 70}")
    print(f"RESULT")
    print(f"{'=' * 70}")
    print(f"Both {source_locale} and {target_locale}: {has_both}")
    print(f"  Machine-translated (needs-review): {needs_review}")
    print(f"  Human-translated: {has_both - needs_review}")
    print(f"Total: {len(rebuilt['strings'])}")
    if has_both == len(rebuilt["strings"]):
        print(f"\nAll strings now have both languages.")
        if needs_review:
            print(f"Note: {needs_review} entries marked 'needs-review' — verify accuracy.")
    print("=" * 70)


if __name__ == "__main__":
    main()
