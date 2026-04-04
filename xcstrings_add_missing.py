#!/usr/bin/env python3
"""
xcstrings_add_missing.py
Add missing localized strings to an .xcstrings file with DeepL translations.

Usage:
    python3 xcstrings_add_missing.py --xcstrings path/to/Localizable.xcstrings \
        --api-key YOUR_DEEPL_KEY \
        --strings-file strings_to_add.py \
        [--target-lang ES] \
        [--dry-run]

The --strings-file should be a Python file that defines a list called MISSING_STRINGS:
    MISSING_STRINGS = [
        ("Key text", "Comment for translators", None),            # None = use DeepL
        ("Another string", "Context comment", "explicit_trans"),  # explicit override
    ]

When Claude uses this script:
1. Read all Swift source files to find user-facing strings not yet in xcstrings.
2. Create a strings_to_add.py file with the MISSING_STRINGS list.
3. Run this script to add them with DeepL translations.
"""

import argparse
import importlib.util
import json
import sys
import time
import urllib.request
import urllib.parse


import re as _re

_SPEC_RE2 = _re.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])')

def _protect2(s):
    parts = _SPEC_RE2.split(s)
    out, idx = [], 0
    for part in parts:
        if _SPEC_RE2.fullmatch(part):
            out.append(f'<x id="{idx}">{part}</x>'); idx += 1
        else:
            out.append(part)
    return ''.join(out)

def _unprotect2(s):
    return _re.sub(r'<x id="\d+">(.*?)</x>', r'\1', s)


def translate(text, api_key, source_lang, target_lang, formality="less"):
    """Translate text using DeepL. Returns translated string or None on error.
    Wraps format specifiers in XML tags so DeepL leaves them untouched."""
    protected = _protect2(text)
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
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            return _unprotect2(result["translations"][0]["text"])
    except Exception as e:
        print(f"  DeepL error: {e}")
        return None


def load_strings_file(path):
    """Load MISSING_STRINGS from a Python file."""
    spec = importlib.util.spec_from_file_location("strings_module", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "MISSING_STRINGS"):
        print(f"ERROR: {path} must define a list called MISSING_STRINGS")
        sys.exit(1)
    return mod.MISSING_STRINGS


def main():
    parser = argparse.ArgumentParser(description="Add missing strings to .xcstrings with DeepL")
    parser.add_argument("--xcstrings", required=True, help="Path to Localizable.xcstrings")
    parser.add_argument("--api-key", required=True, help="DeepL API key")
    parser.add_argument("--strings-file", required=True,
                        help="Python file defining MISSING_STRINGS list")
    parser.add_argument("--source-lang", default="EN", help="Source language code (default: EN)")
    parser.add_argument("--target-lang", default="ES",
                        help="Target language code for DeepL (default: ES). Use ES-419 for Latin American Spanish. "
                             "Use the xcstrings locale key for --locale separately if needed.")
    parser.add_argument("--locale", default=None,
                        help="xcstrings locale key to write translations under "
                             "(e.g. 'es-MX'). Defaults to lowercase target-lang.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would be added without modifying the file")
    args = parser.parse_args()

    locale_key = args.locale or args.target_lang.lower()

    missing_strings = load_strings_file(args.strings_file)

    print("=" * 70)
    print("ADDING MISSING LOCALIZED STRINGS WITH DEEPL")
    print(f"  xcstrings : {args.xcstrings}")
    print(f"  locale    : {locale_key}")
    print(f"  strings   : {len(missing_strings)} entries")
    if args.dry_run:
        print("  DRY RUN — no files will be modified")
    print("=" * 70)

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    existing_keys = set(data["strings"].keys())
    added = 0
    skipped = 0
    failed = 0

    for item in missing_strings:
        if len(item) == 3:
            key, comment, explicit_translation = item
        else:
            print(f"ERROR: Each entry must be a 3-tuple (key, comment, translation_or_None): {item}")
            sys.exit(1)

        if key in existing_keys:
            print(f"  SKIP (exists): {repr(key)}")
            skipped += 1
            continue

        print(f"  Translating : {repr(key)}", end=" ", flush=True)

        if explicit_translation:
            translated = explicit_translation
            print(f"→ (explicit) {repr(translated)}")
        else:
            translated = translate(key, args.api_key, args.source_lang, args.target_lang)
            if translated is None:
                print("→ FAILED")
                failed += 1
                continue
            print(f"→ {repr(translated)}")
            time.sleep(0.15)  # rate limit

        if not args.dry_run:
            data["strings"][key] = {
                "comment": comment,
                "localizations": {
                    locale_key: {
                        "stringUnit": {
                            "state": "translated",
                            "value": translated,
                        }
                    }
                },
            }
        added += 1

    if not args.dry_run:
        # Sort keys alphabetically (xcstrings convention)
        data["strings"] = dict(sorted(data["strings"].items(), key=lambda x: x[0].lower()))

        with open(args.xcstrings, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print()
    print("=" * 70)
    print(f"  Added  : {added}")
    print(f"  Skipped: {skipped} (already existed)")
    print(f"  Failed : {failed}")
    print(f"  Total strings now: {len(data['strings'])}")
    print("=" * 70)


if __name__ == "__main__":
    main()
