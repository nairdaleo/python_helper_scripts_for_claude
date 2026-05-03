#!/usr/bin/env python3
"""
xcstrings_deepl_probe.py
Pre-flight check before starting a DeepL translation session.

Checks:
  1. API key is valid (reachable, authenticated)
  2. Remaining character quota (warns if < 10 000 chars)
  3. Test translation to each requested target language

Usage:
    python3 xcstrings_deepl_probe.py \
        --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
        [--target-langs ES-419 DE FR JA KO PT-BR] \
        [--min-chars 5000]

Exit codes:
    0  All checks passed — safe to start the session
    1  One or more checks failed — do NOT proceed

Run this BEFORE xcstrings_translate_all.py, xcstrings_fix_with_deepl.py, or
xcstrings_add_missing.py on any non-trivial batch. It costs < 100 characters
and takes ~2 seconds.
"""

import argparse
import json
import sys
import urllib.request
import urllib.parse
import urllib.error


# ── Sentinel strings for the test translation ─────────────────────────────────
# Short, unambiguous, with a format specifier to verify specifier protection.
_TEST_STRINGS = {
    "EN": "Hello, world! Ready in %lld seconds.",
}
# Expected rough translations — we just verify the specifier survived intact,
# not that the wording is perfect.
_SPEC_MARKER = "%lld"


# ── Languages that support formality in the DeepL API ─────────────────────────
_FORMALITY_SUPPORTED = {
    'DE', 'ES', 'ES-419', 'FR', 'IT', 'JA', 'NL', 'PL',
    'PT', 'PT-BR', 'PT-PT', 'RU',
}

# Format specifier protection (same as other scripts)
import re as _re
_SPEC_RE = _re.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])')

def _protect(s):
    parts = _SPEC_RE.split(s)
    out, idx = [], 0
    for part in parts:
        if _SPEC_RE.fullmatch(part):
            out.append(f'<x id="{idx}">{part}</x>'); idx += 1
        else:
            out.append(part)
    return ''.join(out)

def _unprotect(s):
    return _re.sub(r'<x id="\d+">(.*?)</x>', r'\1', s)


# ── DeepL helpers ─────────────────────────────────────────────────────────────

def _base_url(api_key):
    """Pick free vs paid endpoint based on key suffix."""
    return "https://api-free.deepl.com" if api_key.endswith(":fx") else "https://api.deepl.com"


def check_usage(api_key):
    """Return (character_count, character_limit) or raise on failure."""
    url = f"{_base_url(api_key)}/v2/usage"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    return data["character_count"], data["character_limit"]


def test_translate(api_key, target_lang, source_lang="EN"):
    """Translate the probe string and return (translated_text, specifier_intact).
    specifier_intact is True if %lld survived the round-trip."""
    text = _TEST_STRINGS["EN"]
    protected = _protect(text)
    params_dict = {
        "text": protected,
        "source_lang": source_lang.upper(),
        "target_lang": target_lang.upper(),
        "tag_handling": "xml",
    }
    if target_lang.upper() in _FORMALITY_SUPPORTED:
        params_dict["formality"] = "less"
    params = urllib.parse.urlencode(params_dict).encode()

    req = urllib.request.Request(
        f"{_base_url(api_key)}/v2/translate",
        data=params,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())

    translated = _unprotect(result["translations"][0]["text"])
    specifier_ok = _SPEC_MARKER in translated
    return translated, specifier_ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Pre-flight DeepL probe: check quota + test translation per language"
    )
    parser.add_argument("--api-key", required=True, help="DeepL API key")
    parser.add_argument(
        "--target-langs",
        nargs="+",
        default=["ES-419"],
        metavar="LANG",
        help="DeepL target language codes to probe (default: ES-419). "
             "Space-separated, e.g. --target-langs ES-419 DE FR JA KO PT-BR",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=10_000,
        help="Minimum remaining character quota required to pass (default: 10000). "
             "Set lower if your batch is small.",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("DEEPL PRE-FLIGHT PROBE")
    print(f"  API endpoint : {_base_url(args.api_key)}")
    print(f"  Target langs : {', '.join(args.target_langs)}")
    print(f"  Min quota    : {args.min_chars:,} chars required")
    print("=" * 70)

    failures = []

    # ── 1. Usage / quota ──────────────────────────────────────────────────────
    print("\n[1/2] Checking usage quota...")
    try:
        used, limit = check_usage(args.api_key)
        remaining = limit - used
        used_pct = used * 100 / limit if limit else 0
        print(f"  Used      : {used:>10,} / {limit:,} chars  ({used_pct:.1f}%)")
        print(f"  Remaining : {remaining:>10,} chars")

        if remaining < args.min_chars:
            msg = (f"Quota too low — {remaining:,} chars remaining, "
                   f"need at least {args.min_chars:,}.")
            print(f"  ✗ {msg}")
            failures.append(msg)
        else:
            print(f"  ✓ Quota OK")

        # Advisory warnings
        if limit and used_pct >= 90:
            print(f"  ⚠ WARNING: {used_pct:.0f}% of monthly quota consumed.")
        elif limit and used_pct >= 75:
            print(f"  ⚠ Note: {used_pct:.0f}% of monthly quota consumed.")

    except urllib.error.HTTPError as e:
        if e.code == 403:
            msg = "API key rejected (403 Forbidden). Check the key."
        elif e.code == 456:
            msg = "Quota exceeded (456). Cannot translate this month."
        else:
            msg = f"Usage endpoint returned HTTP {e.code}."
        print(f"  ✗ {msg}")
        failures.append(msg)
        # Can't continue without auth
        _print_summary(failures)
        sys.exit(1)
    except Exception as e:
        msg = f"Could not reach DeepL: {e}"
        print(f"  ✗ {msg}")
        failures.append(msg)
        _print_summary(failures)
        sys.exit(1)

    # ── 2. Test translation per language ──────────────────────────────────────
    print(f"\n[2/2] Test translation → {', '.join(args.target_langs)}...")
    print(f"  Source: {_TEST_STRINGS['EN']!r}")

    for lang in args.target_langs:
        try:
            translated, spec_ok = test_translate(args.api_key, lang)
            if spec_ok:
                print(f"  ✓ {lang:8s}  {translated!r}")
            else:
                msg = (f"{lang}: specifier %lld was corrupted in translation. "
                       f"Got: {translated!r}")
                print(f"  ✗ {lang:8s}  SPECIFIER CORRUPTED — {translated!r}")
                failures.append(msg)
        except urllib.error.HTTPError as e:
            msg = f"{lang}: HTTP {e.code} from DeepL translate endpoint."
            print(f"  ✗ {lang:8s}  HTTP {e.code}")
            failures.append(msg)
        except Exception as e:
            msg = f"{lang}: {e}"
            print(f"  ✗ {lang:8s}  ERROR — {e}")
            failures.append(msg)

    # ── Summary ───────────────────────────────────────────────────────────────
    _print_summary(failures)
    sys.exit(1 if failures else 0)


def _print_summary(failures):
    print()
    print("=" * 70)
    if not failures:
        print("RESULT: ✓ All checks passed — safe to start your session.")
    else:
        print(f"RESULT: ✗ {len(failures)} check(s) failed — DO NOT start the session.")
        for f in failures:
            print(f"  • {f}")
    print("=" * 70)


if __name__ == "__main__":
    main()
