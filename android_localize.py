#!/usr/bin/env python3
"""
Android strings.xml localization script using the Anthropic Messages API.

Replaces the previous DeepL implementation (DeepL free quota proved too small
for repeated full-app passes). Same I/O contract: discovers missing locale
files for each project in PROJECTS, translates the base English strings to
each target locale, writes valid Android resource XML.

Auth: ANTHROPIC_API_KEY env var. Errors out clearly if missing.

Usage:
  python3 android_localize.py            # create missing locale files only
  python3 android_localize.py --patch    # also patch missing keys into existing files
  python3 android_localize.py Spark      # limit to one project (name substring match)

Cost: Claude Haiku at ~$0.25 / $1.25 per million in/out tokens — a full
6-locale x ~300-string pass on Spark runs to about $0.10 - $0.30.

Why Haiku and not Sonnet: short translation tasks that need to preserve
format specifiers and a casual tone don't benefit from the bigger model;
Haiku gets it right and costs ~10x less.
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

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
ANTHROPIC_VERSION = "2023-06-01"

# Set FORCE=True to overwrite existing (possibly bad) locale files.
# Default False — never silently nuke a non-English locale file back to
# English. Flip deliberately.
FORCE = False

PROJECTS = {
    "Spark": "/Users/adrian/gitworks/me/SparkAndroid/app/src/main/res",
    "Klick": "/Users/adrian/gitworks/me/KlickAndroid/app/src/main/res",
}

# Target locales: (android_folder_suffix, target_language_name, copy_from_base)
# - target_language_name is what we tell Claude (a natural-language label
#   reads better than ISO codes for the model and avoids dialect confusion).
# - copy_from_base=True → skip translation, just clone the English base.
LOCALES = [
    ("en-rCA",  None,                                True),
    ("en-rUS",  None,                                True),
    ("fr",      "French (France)",                   False),
    ("fr-rCA",  "French (France)",                   False),  # Quebecois
                                                              # divergence is
                                                              # rare in app UI;
                                                              # FR copy is fine.
    ("ja",      "Japanese",                          False),
    ("ko",      "Korean",                            False),
    ("de",      "German",                            False),
    ("es-rMX",  "Mexican Spanish (Latin American)",  False),
    ("pt-rBR",  "Brazilian Portuguese",              False),
]

# Project tone — this is Spark, an ADHD habit tracker. We tell Claude this
# explicitly so the translations land warm and shame-free, never clinical.
PROJECT_TONE = (
    "You are translating UI strings for Spark, an ADHD-friendly habit "
    "tracker. The voice is warm, casual, and shame-free — never clinical, "
    "punitive, or formal. Use second-person informal address (tu / du / "
    "tú / etc.) where the target language distinguishes formality."
)

# ─── Android resource escaping ────────────────────────────────────────────────

def unescape_android(text: str) -> str:
    """Android escaped text → plain text for translation."""
    return text.replace("\\'", "'").replace("\\n", "\n").replace("\\\"", '"')

def escape_android(text: str) -> str:
    """Plain text → Android resource escaping (idempotent).
    Normalises first (strip any existing \\') then re-escapes, so calling
    this multiple times on the same string is safe."""
    normalised = text.replace("\\'", "'")
    return normalised.replace("'", "\\'")

# ─── Format-specifier protection ─────────────────────────────────────────────
# Wrap Android format specifiers (%1$s, %2$d, etc.) in <x id="N">...</x>
# tags before sending to Claude, so the model never reformats / translates
# them. Same approach the DeepL version used; Claude is told to preserve
# tags verbatim.

_SPEC_RE = re.compile(r'(%(?:\d+\$)?(?:hh|h|ll|l|q|z|t|j)?[diouxXeEfFgGaAcsSp@]|\d+\$[a-zA-Z@]+)')

def _protect(text: str) -> str:
    """Wrap format specifiers in <x> tags."""
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
    """Restore format specifiers from <x> tags."""
    return re.sub(r'<x id="\d+">(.*?)</x>', r'\1', text)


# ─── Anthropic API (batch, JSON-array protocol) ──────────────────────────────

def _anthropic_request(system: str, user: str, max_tokens: int = 8192) -> str:
    """POST /v1/messages and return the model's text response.
    Pure urllib so the script keeps zero deps."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY env var not set.\n"
              "  Get a key at https://console.anthropic.com/settings/keys\n"
              "  then: export ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(2)

    body = json.dumps({
        "model": ANTHROPIC_MODEL,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }).encode("utf-8")

    req = urllib.request.Request(ANTHROPIC_URL, data=body, method="POST")
    req.add_header("x-api-key", api_key)
    req.add_header("anthropic-version", ANTHROPIC_VERSION)
    req.add_header("content-type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
        # Response shape: {"content": [{"type":"text","text":"..."}], ...}
        parts = [b.get("text", "") for b in payload.get("content", [])
                 if b.get("type") == "text"]
        return "".join(parts)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        print(f"    ⚠️  Anthropic HTTP {e.code}: {body_text[:400]}", file=sys.stderr)
        raise


def _strip_code_fence(text: str) -> str:
    """Remove ```json … ``` or ``` … ``` wrappers if Claude added one."""
    s = text.strip()
    m = re.match(r"^```(?:json)?\s*\n(.*?)\n```\s*$", s, flags=re.DOTALL)
    return m.group(1) if m else s


def claude_translate_batch(texts: list, target_language: str) -> list:
    """Translate a batch of strings via Claude. Returns same-order list.
    Same signature as the old deepl_translate_batch so the rest of the
    script doesn't change."""
    if not texts:
        return []

    prepared = [_protect(unescape_android(t)) for t in texts]

    system = (
        f"{PROJECT_TONE}\n\n"
        f"You are a professional Android UI localizer. Translate the input "
        f"strings to {target_language}.\n\n"
        f"RULES:\n"
        f"1. Preserve every <x id=\"N\">...</x> tag VERBATIM. Do not "
        f"translate the content inside these tags. Do not change the order "
        f"of multiple tags within a single string.\n"
        f"2. Preserve all format specifiers (e.g. %1$s, %2$d, %@) "
        f"verbatim if any leak through outside the tags.\n"
        f"3. Output exactly a JSON array of strings, same length as the "
        f"input array, in the same order. No keys, no commentary, no "
        f"markdown fences, no leading or trailing prose.\n"
        f"4. If a string is a single technical token (e.g. \"OK\", "
        f"\"Pro\"), use the conventional translation in {target_language} "
        f"(many such tokens stay as-is in target locale).\n"
        f"5. Keep punctuation conventions of {target_language} "
        f"(e.g. French uses « », spaces before ! ? ; :).\n"
    )

    user = (
        f"Translate the following {len(prepared)} Android UI strings to "
        f"{target_language}. Return a JSON array of {len(prepared)} "
        f"translated strings in the same order.\n\n"
        f"INPUT:\n{json.dumps(prepared, ensure_ascii=False)}"
    )

    raw = _anthropic_request(system, user)
    raw = _strip_code_fence(raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"    ⚠️  Claude returned non-JSON response (first 400 chars):\n"
              f"        {raw[:400]!r}", file=sys.stderr)
        # Defensive fallback: return originals so we don't write garbage,
        # but raise so the caller can decide whether to skip the file.
        raise

    if not isinstance(parsed, list) or len(parsed) != len(prepared):
        print(f"    ⚠️  Claude returned wrong shape: type={type(parsed).__name__}, "
              f"len={len(parsed) if isinstance(parsed, list) else 'n/a'}, "
              f"expected list of {len(prepared)}", file=sys.stderr)
        raise ValueError("translation response shape mismatch")

    return [_unprotect(t) for t in parsed]


# Back-compat alias so the rest of the script doesn't need editing.
deepl_translate_batch = claude_translate_batch


# ─── XML parsing ─────────────────────────────────────────────────────────────

def read_base_strings(path: str) -> list:
    """Parse base strings.xml → list of (tag, name, translatable, items).
    items = [(quantity_or_None, text_value)]
    """
    tree = ET.parse(path)
    root = tree.getroot()
    entries = []
    for child in root:
        if child.tag == "string":
            is_translatable = child.get("translatable", "true").lower() != "false"
            text = child.text or ""
            entries.append(("string", child.get("name"), is_translatable, [(None, text)]))
        elif child.tag == "plurals":
            is_translatable = child.get("translatable", "true").lower() != "false"
            items = [(item.get("quantity"), item.text or "") for item in child if item.tag == "item"]
            entries.append(("plurals", child.get("name"), is_translatable, items))
    return entries

# ─── XML writing ──────────────────────────────────────────────────────────────

def _xml_escape(text: str) -> str:
    """Escape for XML text content (NOT Android resource escaping)."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def write_strings_xml(path: str, entries: list):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["<?xml version='1.0' encoding='utf-8'?>", "<resources>"]
    for tag, name, translatable, items in entries:
        if tag == "string":
            _, text = items[0]
            safe = _xml_escape(escape_android(text))
            lines.append(f'    <string name="{name}">{safe}</string>')
        elif tag == "plurals":
            lines.append(f'    <plurals name="{name}">')
            for quantity, text in items:
                safe = _xml_escape(escape_android(text))
                lines.append(f'        <item quantity="{quantity}">{safe}</item>')
            lines.append('    </plurals>')
    lines.append("</resources>")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

def copy_strings_xml(src: str, dst: str):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)

def read_existing_keys(path: str) -> set:
    """Return the set of string/plurals names already present in a locale file."""
    if not os.path.exists(path):
        return set()
    try:
        tree = ET.parse(path)
        return {child.get("name") for child in tree.getroot()
                if child.tag in ("string", "plurals")}
    except ET.ParseError:
        return set()

def patch_locale_file(path: str, new_entries: list):
    """Append new <string>/<plurals> entries before </resources> in an existing file."""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    lines = []
    for tag, name, _translatable, items in new_entries:
        if tag == "string":
            _, text = items[0]
            lines.append(f'    <string name="{name}">{_xml_escape(escape_android(text))}</string>')
        elif tag == "plurals":
            lines.append(f'    <plurals name="{name}">')
            for quantity, text in items:
                lines.append(f'        <item quantity="{quantity}">{_xml_escape(escape_android(text))}</item>')
            lines.append('    </plurals>')
    block = "\n".join(lines)
    new_content = content.replace("</resources>", f"{block}\n</resources>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_content)


# ─── Translation logic ────────────────────────────────────────────────────────

def translate_entries(entries: list, target_language: str) -> list:
    """Translate all translatable strings; return new entries list."""
    batch_keys = []   # (entry_idx, item_idx)
    batch_texts = []

    for ei, (tag, name, is_translatable, items) in enumerate(entries):
        if not is_translatable:
            continue
        for ii, (quantity, text) in enumerate(items):
            if text.strip():
                batch_keys.append((ei, ii))
                batch_texts.append(text)

    if not batch_texts:
        return entries

    print(f"      Translating {len(batch_texts)} strings to {target_language}...")

    # Batch in chunks. Claude can swallow much larger payloads than DeepL,
    # but smaller chunks keep failures recoverable: a single bad batch
    # only loses ~50 strings instead of the whole locale.
    CHUNK = 50
    translated_all = []
    for start in range(0, len(batch_texts), CHUNK):
        chunk = batch_texts[start:start + CHUNK]
        result = claude_translate_batch(chunk, target_language)
        translated_all.extend(result)
        if start + CHUNK < len(batch_texts):
            time.sleep(0.3)  # gentle pacing

    # Map results back
    translate_map = {}
    for (ei, ii), translated in zip(batch_keys, translated_all):
        translate_map.setdefault(ei, {})[ii] = escape_android(translated)

    new_entries = []
    for ei, (tag, name, is_translatable, items) in enumerate(entries):
        if ei in translate_map:
            new_items = []
            for ii, (quantity, text) in enumerate(items):
                new_text = translate_map[ei].get(ii, text)
                new_items.append((quantity, new_text))
            new_entries.append((tag, name, is_translatable, new_items))
        else:
            new_entries.append((tag, name, is_translatable, items))

    return new_entries

# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Localize Android strings.xml via Claude (Anthropic API).")
    parser.add_argument("project", nargs="?", default=None,
                        help="Limit to projects whose name contains this substring (case-insensitive).")
    parser.add_argument("--patch", action="store_true",
                        help="Patch missing keys into existing locale files instead of skipping them.")
    args = parser.parse_args()

    patch_mode = args.patch
    project_filter = args.project.lower() if args.project else None

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
        print(f"  Base: {len(base_entries)} entries "
              f"({sum(1 for _,_,t,_ in base_entries if t)} translatable)")

        for folder_suffix, target_language, copy_only in LOCALES:
            dst_dir = os.path.join(res_dir, f"values-{folder_suffix}")
            dst_path = os.path.join(dst_dir, "strings.xml")

            already_exists = os.path.exists(dst_path)

            if copy_only:
                if not already_exists:
                    print(f"  📋  {folder_suffix:12s} — copying base en...")
                    copy_strings_xml(base_path, dst_path)
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                elif FORCE:
                    print(f"  📋  {folder_suffix:12s} — overwriting base en (FORCE)...")
                    copy_strings_xml(base_path, dst_path)
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                elif patch_mode:
                    existing_keys = read_existing_keys(dst_path)
                    missing = [e for e in base_entries
                               if e[1] not in existing_keys and e[2]]
                    if missing:
                        print(f"  📋  {folder_suffix:12s} — patching {len(missing)} missing key(s) (copy)...")
                        patch_locale_file(dst_path, missing)
                        print(f"  ✅  {folder_suffix:12s} — done")
                        total_patched += len(missing)
                    else:
                        print(f"  ✅  {folder_suffix:12s} — up to date")
                else:
                    print(f"  ✅  {folder_suffix:12s} — already exists, skipping")
                continue

            if not already_exists:
                print(f"  🌍  {folder_suffix:12s} — translating ({target_language})...")
                try:
                    translated = translate_entries(base_entries, target_language)
                    write_strings_xml(dst_path, translated)
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                except Exception as e:
                    print(f"  ⏸️  {folder_suffix:12s} — skipped ({e})")
            elif FORCE:
                print(f"  🌍  {folder_suffix:12s} — retranslating (FORCE, {target_language})...")
                try:
                    translated = translate_entries(base_entries, target_language)
                    write_strings_xml(dst_path, translated)
                    print(f"  ✅  {folder_suffix:12s} — done")
                    total_created += 1
                except Exception as e:
                    print(f"  ⏸️  {folder_suffix:12s} — skipped ({e})")
            elif patch_mode:
                existing_keys = read_existing_keys(dst_path)
                missing = [e for e in base_entries
                           if e[1] not in existing_keys and e[2]]
                if missing:
                    print(f"  🌍  {folder_suffix:12s} — patching {len(missing)} missing key(s) ({target_language})...")
                    try:
                        translated_missing = translate_entries(missing, target_language)
                        patch_locale_file(dst_path, translated_missing)
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


if __name__ == "__main__":
    main()
