#!/usr/bin/env python3
"""
xcstrings_translate_all.py
Translate all missing locales in ONE pass — reads once, writes once.
Prevents the clobbering bug that happens when running xcstrings_fix_with_deepl.py
sequentially (each run overwrites the previous locale's work).

Usage:
    python3 xcstrings_translate_all.py \
        --xcstrings path/to/Localizable.xcstrings \
        --api-key YOUR_DEEPL_KEY \
        --source-locale en \
        --source-lang EN \
        --locales es-MX:ES-419 de:DE fr:FR fr-CA:FR pt-BR:PT-BR ja:JA \
        [--formality less] \
        [--dry-run]

IMPORTANT: Close Xcode before running this script.
"""
import argparse, json, re, sys, time
import urllib.request, urllib.parse

_SPEC_RE = re.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@])')

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
    return re.sub(r'<x id="\d+">(.*?)</x>', r'\1', s)

def deepl_translate(text, api_key, source_lang, target_lang, formality='less'):
    if not text or not text.strip():
        return text
    # Escape bare & to &amp; — required by DeepL's XML tag handler
    protected = _protect(text).replace('&', '&amp;')
    params = urllib.parse.urlencode({
        'text': protected,
        'source_lang': source_lang,
        'target_lang': target_lang,
        'tag_handling': 'xml',
        'ignore_tags': 'x',
        'formality': formality,
    }).encode()
    base = 'https://api-free.deepl.com' if api_key.endswith(':fx') else 'https://api.deepl.com'
    req = urllib.request.Request(
        f'{base}/v2/translate', data=params, method='POST',
        headers={'Authorization': f'DeepL-Auth-Key {api_key}'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            translated = result['translations'][0]['text']
            # Unescape &amp; back to & (we escaped it before sending)
            translated = translated.replace('&amp;', '&')
            return _unprotect(translated)
    except Exception as e:
        print(f'    DeepL error: {e}', file=sys.stderr)
        return None

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--xcstrings', required=True)
    p.add_argument('--api-key', required=True)
    p.add_argument('--source-locale', default='en')
    p.add_argument('--source-lang', default='EN')
    p.add_argument('--locales', nargs='+', required=True,
                   help='locale:DEEPL_LANG pairs, e.g. es-MX:ES-419 de:DE fr:FR')
    p.add_argument('--formality', default='less')
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()

    # Parse locale pairs
    locale_pairs = []
    for pair in args.locales:
        if ':' in pair:
            locale, lang = pair.split(':', 1)
        else:
            locale, lang = pair, pair.upper()
        locale_pairs.append((locale, lang))

    # Copy locales (e.g. fr-CA copies from fr instead of calling DeepL)
    copy_map = {'fr-CA': 'fr'}  # target_locale -> source_locale to copy from

    print(f'Reading {args.xcstrings}')
    with open(args.xcstrings, encoding='utf-8') as f:
        data = json.load(f)

    strings = data['strings']
    source_locale = args.source_locale

    # For each target locale, find keys that need translation
    for locale, deepl_lang in locale_pairs:
        copy_from = copy_map.get(locale)

        needs_translation = {
            k: v for k, v in strings.items()
            if source_locale in v.get('localizations', {})
            and locale not in v.get('localizations', {})
            and v.get('shouldTranslate', True)  # respect Xcode's "Don't Translate" flag
        }

        if not needs_translation:
            print(f'\n── {locale}: nothing to do ✅')
            continue

        if copy_from:
            print(f'\n── {locale}: copying {len(needs_translation)} strings from {copy_from}')
            copied = 0
            for key, entry in needs_translation.items():
                src = strings[key].get('localizations', {}).get(copy_from)
                if src:
                    strings[key]['localizations'][locale] = {
                        'stringUnit': {'state': 'translated', 'value': src['stringUnit']['value']}
                    }
                    copied += 1
            print(f'  Copied: {copied}')
            continue

        print(f'\n── {locale} ({deepl_lang}): translating {len(needs_translation)} strings')
        ok, fail = 0, 0
        for i, (key, entry) in enumerate(needs_translation.items(), 1):
            src_val = entry['localizations'][source_locale].get('stringUnit', {}).get('value', key)
            translated = deepl_translate(src_val, args.api_key, args.source_lang, deepl_lang, args.formality)
            if translated:
                strings[key]['localizations'][locale] = {
                    'stringUnit': {'state': 'translated', 'value': translated}
                }
                ok += 1
                if i % 20 == 0:
                    print(f'  [{i}/{len(needs_translation)}] ...')
            else:
                fail += 1
                print(f'  FAILED: {repr(key)[:60]}')
            if i < len(needs_translation):
                time.sleep(0.05)
        print(f'  Done: {ok} translated, {fail} failed')

    # Final count
    # Keys with shouldTranslate=false are intentionally untranslated — exclude from denominator
    from collections import Counter
    dont_translate_count = sum(1 for v in strings.values() if not v.get('shouldTranslate', True))
    effective_total = len(strings) - dont_translate_count

    counts = Counter()
    for v in strings.values():
        if not v.get('shouldTranslate', True):
            continue  # skip dont-translate keys from counts too
        for loc in v.get('localizations', {}).keys():
            counts[loc] += 1

    print('\n── Summary ──')
    if dont_translate_count:
        print(f'  ({dont_translate_count} keys marked "Don\'t Translate" excluded)')
    for loc, n in sorted(counts.items()):
        print(f'  {loc}: {n}/{effective_total}')

    if not args.dry_run:
        with open(args.xcstrings, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write('\n')
        print(f'\nWritten to {args.xcstrings}')
    else:
        print('\n(dry-run — not written)')

if __name__ == '__main__':
    main()
