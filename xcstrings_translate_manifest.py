#!/usr/bin/env python3
"""
xcstrings_translate_manifest.py

Translate ONLY the keys listed in a manifest file to one or more locales,
in a single pass. Skips any (key, locale) pair that already has a value.

This fills a gap in the existing helper-script suite:
- xcstrings_add_missing.py — only one locale at a time, errors if the
  key already exists in the catalog.
- xcstrings_fix_with_deepl.py — fills gaps for ALL keys in the catalog
  (no manifest filter), which churns through hundreds of irrelevant
  strings if you only care about a small set you just added.

Use this when:
- You shipped a feature that added N new strings and Xcode already
  extracted them into the catalog (no localizations populated yet).
- You want all target locales translated in one shot.

Format specifiers (%@, %lld, etc.) are wrapped in <x id="N"> tags before
being sent to DeepL so they survive translation untouched. Mirrors the
protection in xcstrings_add_missing.py.

Manifest format — same shape as xcstrings_add_missing.py expects:
    MISSING_STRINGS = [
        ("Key text", "Comment for translators", None),
        ("Key text 2", "Comment", "explicit translation override"),
    ]

The third tuple element (explicit translation) is read but currently
unused by this script — DeepL is always called for unset locales. Use
xcstrings_add_missing.py for explicit-override workflow instead.

Usage:
    python3 xcstrings_translate_manifest.py \\
        --xcstrings path/to/Localizable.xcstrings \\
        --manifest path/to/strings.py \\
        --api-key DEEPL_KEY \\
        --locales fr-FR=FR fr-CA=FR de-DE=DE ja=JA ko=KO pt-BR=PT-BR \\
        [--source-lang EN] \\
        [--formality less] \\
        [--dry-run]

Locale arguments are space-separated `xcstrings_locale=DEEPL_TARGET_LANG`
pairs. Example: `fr-CA=FR` means "store under the fr-CA key in the
xcstrings catalog, translate via DeepL's FR target". Apply your own
manual Québécois pass after if needed.

Formality default is "less" (informal "tu"/"du"/"tú"). DeepL only
supports formality on a subset of languages; the script auto-skips
the parameter for languages where it's unsupported (KO, JA when
unsupported, etc. — see _FORMALITY_SUPPORTED below). Pass --formality
to override, or pass --formality "" to omit entirely.

Exit codes: 0 on success (even if some pairs failed individually);
1 on argument or manifest errors.
"""

import argparse
import importlib.util
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEEPL_FREE_URL = "https://api-free.deepl.com/v2/translate"
DEEPL_PAID_URL = "https://api.deepl.com/v2/translate"

# DeepL languages that accept the `formality` parameter as of 2026.
# Korean (KO) is notably absent — sending formality to KO returns 403.
# Mirror of the table in xcstrings_add_missing.py.
_FORMALITY_SUPPORTED = {
    "DE", "ES", "ES-419", "FR", "IT", "JA", "NL", "PL",
    "PT", "PT-BR", "PT-PT", "RU",
}

# Matches Apple xcstrings format specifiers that need protection.
_SPEC_RE = re.compile(
    r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])'
)


def _protect(s):
    """Wrap each format specifier in <x id=N> so DeepL leaves it alone."""
    parts = _SPEC_RE.split(s)
    out, idx = [], 0
    for part in parts:
        if _SPEC_RE.fullmatch(part):
            out.append(f'<x id="{idx}">{part}</x>')
            idx += 1
        else:
            out.append(part)
    return "".join(out)


def _unprotect(s):
    return re.sub(r'<x id="\d+">(.*?)</x>', r"\1", s)


def deepl_translate(text, api_key, source_lang, target_lang, formality, endpoint):
    protected = _protect(text)
    data = {
        "text": protected,
        "source_lang": source_lang.upper(),
        "target_lang": target_lang.upper(),
        "tag_handling": "xml",
    }
    if formality and target_lang.upper() in _FORMALITY_SUPPORTED:
        data["formality"] = formality

    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    return _unprotect(result["translations"][0]["text"])


def load_manifest(path):
    spec = importlib.util.spec_from_file_location("manifest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "MISSING_STRINGS"):
        sys.exit(f"ERROR: {path} must define MISSING_STRINGS list")
    return [k for (k, _comment, _explicit) in mod.MISSING_STRINGS]


def parse_locale_pairs(items):
    pairs = []
    for item in items:
        if "=" not in item:
            sys.exit(f"ERROR: locale spec '{item}' must be 'xcstrings_locale=DEEPL_LANG'")
        loc, lang = item.split("=", 1)
        loc, lang = loc.strip(), lang.strip()
        if not loc or not lang:
            sys.exit(f"ERROR: empty locale or DeepL lang in '{item}'")
        pairs.append((loc, lang))
    return pairs


def main():
    p = argparse.ArgumentParser(
        description="Translate manifest keys to N locales via DeepL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1] if "Usage:" in __doc__ else "",
    )
    p.add_argument("--xcstrings", required=True, type=Path,
                   help="Path to the .xcstrings catalog to update in place.")
    p.add_argument("--manifest", required=True, type=Path,
                   help="Python file defining MISSING_STRINGS list.")
    p.add_argument("--api-key", required=True,
                   help="DeepL API key. Use DEEPL_API_KEY env var if you prefer.")
    p.add_argument("--locales", required=True, nargs="+",
                   help="Space-separated 'xcstrings_locale=DEEPL_LANG' pairs.")
    p.add_argument("--source-lang", default="EN",
                   help="DeepL source language. Default: EN.")
    p.add_argument("--formality", default="less",
                   choices=["less", "more", "default", ""],
                   help="DeepL formality. 'less'=tu/du/tú, 'more'=vous/Sie/usted. "
                        "Empty string disables. Auto-skipped for unsupported langs.")
    p.add_argument("--paid-tier", action="store_true",
                   help="Use DeepL Pro endpoint instead of the free one.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would change, do not write the catalog.")
    args = p.parse_args()

    if not args.xcstrings.exists():
        sys.exit(f"ERROR: {args.xcstrings} does not exist.")
    if not args.manifest.exists():
        sys.exit(f"ERROR: {args.manifest} does not exist.")

    keys = load_manifest(args.manifest)
    locale_pairs = parse_locale_pairs(args.locales)
    endpoint = DEEPL_PAID_URL if args.paid_tier else DEEPL_FREE_URL

    print(f"Manifest    : {args.manifest} ({len(keys)} keys)")
    print(f"Catalog     : {args.xcstrings}")
    print(f"Locales     : {', '.join(f'{a}({b})' for a,b in locale_pairs)}")
    print(f"Source      : {args.source_lang}")
    print(f"Formality   : {args.formality or '(disabled)'}")
    if args.dry_run:
        print(f"DRY RUN — no writes will happen.\n")
    else:
        print()

    with args.xcstrings.open() as f:
        catalog = json.load(f)

    total_added = 0
    total_failed = 0

    for locale, deepl_lang in locale_pairs:
        added = 0
        failed = 0
        print(f"=== {locale} (DeepL {deepl_lang}) ===")
        for key in keys:
            entry = catalog["strings"].get(key)
            if not entry:
                print(f"  ⚠️  Missing key in catalog: {key!r}")
                continue
            locs = entry.setdefault("localizations", {})
            existing = locs.get(locale, {}).get("stringUnit", {}).get("value")
            if existing:
                continue
            try:
                translated = deepl_translate(
                    key,
                    api_key=args.api_key,
                    source_lang=args.source_lang,
                    target_lang=deepl_lang,
                    formality=args.formality,
                    endpoint=endpoint,
                )
            except Exception as e:
                print(f"  ❌ {key[:60]!r}: {e}")
                failed += 1
                continue
            if not args.dry_run:
                locs[locale] = {
                    "stringUnit": {"state": "translated", "value": translated}
                }
            added += 1
            preview = translated[:80] + ("…" if len(translated) > 80 else "")
            tag = "[dry]" if args.dry_run else " ✓ "
            print(f"  {tag} {key[:50]!r} → {preview!r}")
        print(f"  Added {added}, failed {failed} for {locale}\n")
        total_added += added
        total_failed += failed

    if not args.dry_run:
        with args.xcstrings.open("w") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
            f.write("\n")

    print("=" * 60)
    print(f"Total translations added : {total_added}")
    print(f"Total failures           : {total_failed}")
    if args.dry_run:
        print("(DRY RUN — catalog not modified)")
    print("=" * 60)


if __name__ == "__main__":
    main()
