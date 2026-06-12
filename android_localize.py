#!/usr/bin/env python3
"""
Android strings.xml localization script using DeepL.

Discovers missing locale files for each project in PROJECTS, translates
the base English strings to each target locale via DeepL, and writes
valid Android resource XML.

If the DeepL quota is depleted the script exits cleanly with instructions
to translate by hand and do a second Claude pass for accuracy and intent.

Usage:
  python3 android_localize.py                        # create missing locale files
  python3 android_localize.py --patch                # also patch missing keys into existing files
  python3 android_localize.py Spark                  # limit to one project
  python3 android_localize.py --api-key YOUR_KEY     # override the hardcoded key

Auth: --api-key flag, or edit DEEPL_API_KEY below.
Free tier: 500,000 characters/month.
"""

import xml.etree.ElementTree as ET
import urllib.request
import urllib.parse
import urllib.error
import json
import re
import os
import shutil
import time
import sys
import argparse

# ─── Config ──────────────────────────────────────────────────────────────────

_KEYS_FILE = os.path.expanduser("~/gitworks/me/.tastyjam-keys.env")

def _load_key(name: str) -> str | None:
    """Read a KEY=value entry from the central keys file. Returns None if not found."""
    if not os.path.exists(_KEYS_FILE):
        return None
    with open(_KEYS_FILE) as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

# Set FORCE=True to overwrite existing (possibly bad) locale files.
# Default False — never silently nuke a non-English locale file.
FORCE = False

PROJECTS = {
    "Spark": "/Users/adrian/gitworks/me/SparkAndroid/app/src/main/res",
    "Klick": "/Users/adrian/gitworks/me/KlickAndroid/app/src/main/res",
}

# (android_folder_suffix, deepl_target_lang, copy_from)
#   deepl_target_lang : DeepL language code — None means skip DeepL (copy only)
#   copy_from         : None  → call DeepL
#                       ""    → clone the English base
#                       "fr"  → clone the already-written fr locale (saves quota)
LOCALES = [
    ("en-rCA",  None,      ""),      # clone English base
    ("en-rUS",  None,      ""),      # clone English base
    ("fr",      "FR",      None),    # translate via DeepL
    ("fr-rCA",  None,      "fr"),    # clone fr — same language, saves a DeepL call
    ("ja",      "JA",      None),
    ("ko",      "KO",      None),
    ("de",      "DE",      None),
    ("es-rMX",  "ES-419",  None),
    ("pt-rBR",  "PT-BR",   None),
]

# Languages that support the formality parameter in DeepL (KO notably absent).
_FORMALITY_SUPPORTED = {
    "DE", "ES", "ES-419", "FR", "IT", "JA", "NL", "PL",
    "PT", "PT-BR", "PT-PT", "RU",
}

# ─── Exceptions ──────────────────────────────────────────────────────────────

class QuotaExhaustedError(Exception):
    """Raised when DeepL returns 456 Quota Exceeded."""

# ─── Android resource escaping ────────────────────────────────────────────────

def unescape_android(text: str) -> str:
    """Android resource escaping → plain text (for sending to DeepL)."""
    return text.replace("\\'", "'").replace("\\n", "\n").replace('\\"', '"')

def escape_android(text: str) -> str:
    """Plain text → Android resource escaping (idempotent: safe to call twice)."""
    normalised = text.replace("\\'", "'")
    return normalised.replace("'", "\\'")

# ─── Format-specifier protection ─────────────────────────────────────────────
# Wrap Android specifiers (%1$s, %2$d, %s, %d, etc.) in <x id="N"> tags
# before sending to DeepL so they come back untouched.

_SPEC_RE = re.compile(
    r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@]'
    r'|\d+\$[a-zA-Z@]+)'
)

def _protect(text: str) -> str:
    parts = _SPEC_RE.split(text)
    out, idx = [], 0
    for part in parts:
        if _SPEC_RE.fullmatch(part):
            out.append(f'<x id="{idx}">{part}</x>')
            idx += 1
        else:
            out.append(part)
    return ''.join(out)

def _unprotect(text: str) -> str:
    return re.sub(r'<x id="\d+">(.*?)</x>', r'\1', text)

# ─── DeepL helpers ────────────────────────────────────────────────────────────

def _base_url(api_key: str) -> str:
    return "https://translator.jendrian.ca"


def check_quota(api_key: str) -> tuple[int, int, int]:
    """Return (used, limit, remaining). Exits on auth failure."""
    url = f"{_base_url(api_key)}/v2/usage"
    req = urllib.request.Request(
        url, headers={"Authorization": f"DeepL-Auth-Key {api_key}", "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""), "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", ""), "User-Agent": "tj-translate/1.0"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        used  = data["character_count"]
        limit = data["character_limit"]
        return used, limit, limit - used
    except urllib.error.HTTPError as e:
        if e.code == 403:
            print("ERROR: DeepL API key rejected (403). Check DEEPL_API_KEY.", file=sys.stderr)
        elif e.code == 456:
            print("ERROR: DeepL quota exceeded (456).", file=sys.stderr)
        else:
            print(f"ERROR: DeepL usage endpoint returned HTTP {e.code}.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: Could not reach DeepL: {e}", file=sys.stderr)
        sys.exit(1)


def deepl_translate(text: str, api_key: str, target_lang: str,
                    formality: str = "less",
                    max_retries: int = 5,
                    initial_backoff: float = 2.0) -> str:
    """Translate one string via DeepL. Protects format specifiers.
    Raises QuotaExhaustedError on 456. Returns the translated string."""
    if not text or not text.strip():
        return text

    # Unescape Android escaping, protect specifiers, escape bare & for DeepL XML
    protected = _protect(unescape_android(text)).replace("&", "&amp;")

    params_dict = {
        "text":         protected,
        "source_lang":  "EN",
        "target_lang":  target_lang,
        "tag_handling": "xml",
        "ignore_tags":  "x",
    }
    if target_lang.upper() in _FORMALITY_SUPPORTED:
        params_dict["formality"] = formality

    params = urllib.parse.urlencode(params_dict).encode()
    req = urllib.request.Request(
        f"{_base_url(api_key)}/v2/translate",
        data=params,
        headers={"Authorization": f"DeepL-Auth-Key {api_key}", "CF-Access-Client-Id": os.environ.get("CF_ACCESS_CLIENT_ID", ""), "CF-Access-Client-Secret": os.environ.get("CF_ACCESS_CLIENT_SECRET", ""), "User-Agent": "tj-translate/1.0"},
    )

    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            return _unprotect(result["translations"][0]["text"])
        except urllib.error.HTTPError as e:
            if e.code == 456:
                raise QuotaExhaustedError("DeepL monthly quota exhausted (456)")
            if e.code in (429, 503, 529) and attempt < max_retries - 1:
                wait = initial_backoff * (2 ** attempt)
                print(f"    DeepL {e.code} — retrying in {wait:.0f}s "
                      f"(attempt {attempt + 1}/{max_retries})")
                time.sleep(wait)
                continue
            print(f"    DeepL HTTP {e.code}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"    DeepL error: {e}", file=sys.stderr)
            return None

    print(f"    DeepL: gave up after {max_retries} retries", file=sys.stderr)
    return None

# ─── XML parsing ─────────────────────────────────────────────────────────────

def read_base_strings(path: str) -> list:
    """Parse strings.xml → list of (tag, name, translatable, items).
    items = [(quantity_or_None, text_value)]
    """
    tree = ET.parse(path)
    root = tree.getroot()
    entries = []
    for child in root:
        if child.tag == "string":
            translatable = child.get("translatable", "true").lower() != "false"
            entries.append(("string", child.get("name"), translatable,
                            [(None, child.text or "")]))
        elif child.tag == "plurals":
            translatable = child.get("translatable", "true").lower() != "false"
            items = [(item.get("quantity"), item.text or "")
                     for item in child if item.tag == "item"]
            entries.append(("plurals", child.get("name"), translatable, items))
    return entries

# ─── XML writing ──────────────────────────────────────────────────────────────

def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def write_strings_xml(path: str, entries: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["<?xml version='1.0' encoding='utf-8'?>", "<resources>"]
    for tag, name, _translatable, items in entries:
        if tag == "string":
            _, text = items[0]
            lines.append(f'    <string name="{name}">'
                         f'{_xml_escape(escape_android(text))}</string>')
        elif tag == "plurals":
            lines.append(f'    <plurals name="{name}">')
            for quantity, text in items:
                lines.append(f'        <item quantity="{quantity}">'
                              f'{_xml_escape(escape_android(text))}</item>')
            lines.append('    </plurals>')
    lines.append("</resources>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def copy_strings_xml(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)

def read_existing_keys(path: str) -> set:
    if not os.path.exists(path):
        return set()
    try:
        tree = ET.parse(path)
        return {child.get("name") for child in tree.getroot()
                if child.tag in ("string", "plurals")}
    except ET.ParseError:
        return set()

def patch_locale_file(path: str, new_entries: list):
    """Append translated entries before </resources> in an existing file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = []
    for tag, name, _translatable, items in new_entries:
        if tag == "string":
            _, text = items[0]
            lines.append(f'    <string name="{name}">'
                         f'{_xml_escape(escape_android(text))}</string>')
        elif tag == "plurals":
            lines.append(f'    <plurals name="{name}">')
            for quantity, text in items:
                lines.append(f'        <item quantity="{quantity}">'
                              f'{_xml_escape(escape_android(text))}</item>')
            lines.append('    </plurals>')
    block = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.replace("</resources>", f"{block}\n</resources>"))

# ─── Translation logic ────────────────────────────────────────────────────────

def translate_entries(entries: list, target_lang: str, api_key: str) -> list:
    """Translate all translatable strings via DeepL. Returns new entries list.

    Stops early and prints a hand-translation notice if quota is exhausted
    mid-run. Untranslated strings are kept as English so the file is still
    valid XML.
    """
    # Build a flat work list: (entry_idx, item_idx)
    work = [
        (ei, ii)
        for ei, (tag, name, translatable, items) in enumerate(entries)
        if translatable
        for ii, (_, text) in enumerate(items)
        if text.strip()
    ]

    if not work:
        return entries

    print(f"      Translating {len(work)} strings to {target_lang}...")

    translate_map: dict[tuple[int, int], str] = {}
    ok = fail = 0

    for n, (ei, ii) in enumerate(work, 1):
        _, _, _, items = entries[ei]
        _, text = items[ii]

        try:
            result = deepl_translate(text, api_key, target_lang)
        except QuotaExhaustedError:
            remaining_count = len(work) - n + 1
            fail += remaining_count
            print(
                f"\n  ✗ DeepL quota exhausted after {ok} strings "
                f"({remaining_count} remaining untranslated).\n"
                "    → Translate the remaining strings by hand.\n"
                "    → Then do a Claude review pass for accuracy and intent "
                "before committing.",
                file=sys.stderr,
            )
            break

        if result is not None:
            translate_map[(ei, ii)] = result
            ok += 1
        else:
            fail += 1

        if n % 50 == 0:
            print(f"      [{n}/{len(work)}] ...")

        time.sleep(0.3)  # stay well within DeepL rate limits

    print(f"      Done: {ok} translated, {fail} failed/skipped")

    # Rebuild entries: translated strings replace the original text;
    # failures keep the original Android-escaped English value.
    new_entries = []
    for ei, (tag, name, translatable, items) in enumerate(entries):
        new_items = []
        for ii, (quantity, text) in enumerate(items):
            translated = translate_map.get((ei, ii))
            # escape_android on the DeepL result; idempotent so safe for
            # fallback English text that is already Android-escaped.
            new_items.append((quantity, escape_android(translated) if translated else text))
        new_entries.append((tag, name, translatable, new_items))
    return new_entries

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Localize Android strings.xml via DeepL."
    )
    parser.add_argument("project", nargs="?", default=None,
                        help="Limit to projects whose name contains this substring "
                             "(case-insensitive).")
    parser.add_argument("--patch", action="store_true",
                        help="Patch missing keys into existing locale files.")
    parser.add_argument("--api-key", default=None,
                        help="DeepL API key. Defaults to DEEPL_API_KEY in "
                             "~/gitworks/me/.tastyjam-keys.env.")
    args = parser.parse_args()

    api_key = args.api_key or _load_key("DEEPL_API_KEY")
    if not api_key:
        print(
            "ERROR: DeepL API key not found.\n"
            f"  Add  DEEPL_API_KEY=<key>  to {_KEYS_FILE}\n"
            "  or pass --api-key KEY on the command line.",
            file=sys.stderr,
        )
        sys.exit(1)
    patch_mode    = args.patch
    project_filter = args.project.lower() if args.project else None

    # ── DeepL quota pre-flight ────────────────────────────────────────────────
    used, limit, remaining = check_quota(api_key)
    used_pct = used * 100 / limit if limit else 0
    print(f"DeepL quota: {used:,} / {limit:,} used ({used_pct:.1f}%)  —  "
          f"{remaining:,} chars remaining")

    if remaining == 0:
        print(
            "\n✗ DeepL quota is depleted for this month.\n"
            "  → Translate strings by hand.\n"
            "  → Then do a Claude review pass for accuracy, tone, and intent "
            "before committing.",
            file=sys.stderr,
        )
        sys.exit(0)  # not an error — just nothing to do right now

    if used_pct >= 90:
        print(f"  ⚠ Warning: {used_pct:.0f}% of monthly quota consumed — "
              "watch for mid-run exhaustion.")
    print()

    total_created = 0
    total_patched = 0

    for project_name, res_dir in PROJECTS.items():
        if project_filter and project_filter not in project_name.lower():
            continue

        base_path = os.path.join(res_dir, "values", "strings.xml")
        if not os.path.exists(base_path):
            print(f"⚠️  {project_name}: base strings.xml not found at {base_path}")
            continue

        print(f"\n{'='*60}")
        print(f"  {project_name}")
        print(f"{'='*60}")

        base_entries = read_base_strings(base_path)
        translatable_count = sum(1 for _, _, t, _ in base_entries if t)
        print(f"  Base: {len(base_entries)} entries ({translatable_count} translatable)")

        # Track written locale paths so copy_from can reference them.
        written: dict[str, str] = {}  # folder_suffix → absolute path

        for folder_suffix, deepl_lang, copy_from in LOCALES:
            dst_dir  = os.path.join(res_dir, f"values-{folder_suffix}")
            dst_path = os.path.join(dst_dir, "strings.xml")
            already_exists = os.path.exists(dst_path)

            # ── Copy locales (no DeepL call) ─────────────────────────────────
            if copy_from is not None:
                if copy_from == "":
                    src_path = base_path                          # English base
                else:
                    src_path = written.get(copy_from) or os.path.join(
                        res_dir, f"values-{copy_from}", "strings.xml")

                if not already_exists:
                    label = copy_from or "en"
                    print(f"  📋  {folder_suffix:12s} — copying from {label}...")
                    copy_strings_xml(src_path, dst_path)
                    written[folder_suffix] = dst_path
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                elif FORCE:
                    label = copy_from or "en"
                    print(f"  📋  {folder_suffix:12s} — overwriting from {label} (FORCE)...")
                    copy_strings_xml(src_path, dst_path)
                    written[folder_suffix] = dst_path
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                elif patch_mode:
                    existing_keys = read_existing_keys(dst_path)
                    missing = [e for e in base_entries
                               if e[1] not in existing_keys and e[2]]
                    if missing:
                        print(f"  📋  {folder_suffix:12s} — patching "
                              f"{len(missing)} key(s) (copy)...")
                        patch_locale_file(dst_path, missing)
                        written[folder_suffix] = dst_path
                        print(f"  ✅  {folder_suffix:12s} — done")
                        total_patched += len(missing)
                    else:
                        print(f"  ✅  {folder_suffix:12s} — up to date")
                else:
                    print(f"  ✅  {folder_suffix:12s} — already exists, skipping")
                continue

            # ── Translate locales (DeepL) ─────────────────────────────────────
            if not already_exists:
                print(f"  🌍  {folder_suffix:12s} — translating ({deepl_lang})...")
                try:
                    translated = translate_entries(base_entries, deepl_lang, api_key)
                    write_strings_xml(dst_path, translated)
                    written[folder_suffix] = dst_path
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                except Exception as e:
                    print(f"  ⏸️  {folder_suffix:12s} — skipped ({e})")

            elif FORCE:
                print(f"  🌍  {folder_suffix:12s} — retranslating (FORCE, {deepl_lang})...")
                try:
                    translated = translate_entries(base_entries, deepl_lang, api_key)
                    write_strings_xml(dst_path, translated)
                    written[folder_suffix] = dst_path
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                except Exception as e:
                    print(f"  ⏸️  {folder_suffix:12s} — skipped ({e})")

            elif patch_mode:
                existing_keys = read_existing_keys(dst_path)
                missing = [e for e in base_entries
                           if e[1] not in existing_keys and e[2]]
                if missing:
                    print(f"  🌍  {folder_suffix:12s} — patching "
                          f"{len(missing)} key(s) ({deepl_lang})...")
                    try:
                        translated_missing = translate_entries(
                            missing, deepl_lang, api_key)
                        patch_locale_file(dst_path, translated_missing)
                        written[folder_suffix] = dst_path
                        print(f"  ✅  {folder_suffix:12s} — done")
                        total_patched += len(missing)
                    except Exception as e:
                        print(f"  ⏸️  {folder_suffix:12s} — skipped ({e})")
                else:
                    print(f"  ✅  {folder_suffix:12s} — up to date")

            else:
                print(f"  ✅  {folder_suffix:12s} — already exists, skipping")

    summary = f"Created: {total_created}"
    if patch_mode:
        summary += f", Patched: {total_patched} key(s)"
    print(f"\n✨ Done! {summary}.")
    print("  → Ask Claude to review output for tone, intent, and apostrophe escaping.")


if __name__ == "__main__":
    main()
