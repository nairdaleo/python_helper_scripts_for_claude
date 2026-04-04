#!/usr/bin/env python3
"""
xcstrings_verify.py
Cross-check existing translations against DeepL to catch errors or drift.

Usage:
    python3 xcstrings_verify.py --xcstrings path/to/Localizable.xcstrings \
        --api-key YOUR_DEEPL_KEY \
        [--source-locale en] \
        [--target-locale es-MX] \
        [--source-lang EN] \
        [--target-lang ES] \
        [--sample 20] \
        [--threshold 0.5]

How it works:
  Takes up to --sample strings that have both source and target translations.
  For each, it asks DeepL to translate the source afresh, then compares word-level
  overlap between our stored translation and the DeepL suggestion. Strings below
  --threshold similarity are reported as potential issues (they may just be stylistic
  differences, not errors).

Exit codes:
  0  All sampled translations match DeepL at or above the threshold.
  1  Some translations had low similarity — worth reviewing.
"""

import argparse
import json
import sys
import time
import urllib.request
import urllib.parse


import re as _re3
_SPEC_RE3 = _re3.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])')
def _p3(s):
    parts = _SPEC_RE3.split(s); out, i = [], 0
    for part in parts:
        if _SPEC_RE3.fullmatch(part): out.append(f'<x id="{i}">{part}</x>'); i += 1
        else: out.append(part)
    return ''.join(out)
def _u3(s): return _re3.sub(r'<x id="\d+">(.*?)</x>', r'\1', s)


def deepl_translate(text, api_key, source_lang, target_lang, formality="less"):
    protected = _p3(text)
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
            return _u3(result["translations"][0]["text"])
    except Exception:
        return None


def word_similarity(a, b):
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 1.0
    return len(words_a & words_b) / len(words_a | words_b)


def main():
    parser = argparse.ArgumentParser(
        description="Verify .xcstrings translations against DeepL"
    )
    parser.add_argument("--xcstrings", required=True, help="Path to Localizable.xcstrings")
    parser.add_argument("--api-key", required=True, help="DeepL API key")
    parser.add_argument("--source-locale", default="en",
                        help="xcstrings locale key for source language (default: en)")
    parser.add_argument("--target-locale", default="es-MX",
                        help="xcstrings locale key for target language (default: es-MX)")
    parser.add_argument("--source-lang", default="EN",
                        help="DeepL source language code (default: EN)")
    parser.add_argument("--target-lang", default="ES",
                        help="DeepL target language code (default: ES)")
    parser.add_argument("--sample", type=int, default=20,
                        help="Number of strings to verify (default: 20)")
    parser.add_argument("--threshold", type=float, default=0.5,
                        help="Minimum word-overlap similarity to pass (default: 0.5)")
    args = parser.parse_args()

    with open(args.xcstrings, "r", encoding="utf-8") as f:
        data = json.load(f)

    source = args.source_locale
    target = args.target_locale

    # Collect strings that have both languages
    candidates = []
    for key, entry in data["strings"].items():
        locs = entry.get("localizations", {})
        if source in locs and target in locs:
            src_val = locs[source].get("stringUnit", {}).get("value", "")
            tgt_val = locs[target].get("stringUnit", {}).get("value", "")
            if src_val and tgt_val:
                candidates.append((key, src_val, tgt_val))

    sample_size = min(args.sample, len(candidates))

    print("=" * 70)
    print("XCSTRINGS TRANSLATION VERIFICATION")
    print(f"  File          : {args.xcstrings}")
    print(f"  Source locale : {source}")
    print(f"  Target locale : {target}")
    print(f"  Sample size   : {sample_size} / {len(candidates)} eligible strings")
    print(f"  Threshold     : {args.threshold:.0%} word overlap")
    print("=" * 70)
    print()

    issues = []
    verified = 0
    failed_api = 0

    for i, (key, src_text, our_translation) in enumerate(candidates[:sample_size], 1):
        short_key = repr(key)[:50]
        print(f"[{i:2}/{sample_size}] {short_key}")

        deepl_translation = deepl_translate(src_text, args.api_key, args.source_lang, args.target_lang)

        if deepl_translation is None:
            print(f"         API error — skipping")
            failed_api += 1
            continue

        similarity = word_similarity(our_translation, deepl_translation)

        if similarity >= args.threshold:
            print(f"         {similarity:.0%} match — OK")
            verified += 1
        else:
            print(f"         {similarity:.0%} match — REVIEW")
            print(f"         Ours : {our_translation[:70]}")
            print(f"         DeepL: {deepl_translation[:70]}")
            issues.append({
                "key": key,
                "source": src_text,
                "ours": our_translation,
                "deepl": deepl_translation,
                "similarity": similarity,
            })

        if i < sample_size:
            time.sleep(0.15)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Verified   : {verified}/{sample_size}")
    print(f"Issues     : {len(issues)}")
    print(f"API errors : {failed_api}")

    if issues:
        print(f"\nStrings to review ({len(issues)}):")
        for issue in issues:
            print(f"\n  Key   : {issue['key']}")
            print(f"  {source}: {issue['source'][:70]}")
            print(f"  Ours  : {issue['ours'][:70]}")
            print(f"  DeepL : {issue['deepl'][:70]}")
            print(f"  Sim   : {issue['similarity']:.0%}")
        print()
        print("Note: Low similarity may be intentional (idiom, style, brevity).")
        print("Review manually before deciding to update.")
        sys.exit(1)
    else:
        print(f"\nAll sampled translations look good.")
    print("=" * 70)


if __name__ == "__main__":
    main()
