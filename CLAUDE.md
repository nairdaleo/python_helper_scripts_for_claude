# Python Helper Scripts — Localization (xcstrings + DeepL)

Reusable Python scripts for managing Apple `.xcstrings` localization files.
DeepL free API for machine translation. No third-party packages — stdlib only. Python 3.8+.

---

## Setup

No `pip install` needed.

### DeepL API key

Adrian's DeepL free-tier key: `93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx`

Pass via `--api-key`. Free tier: 500,000 characters/month.
Check usage at https://www.deepl.com/account/usage

### Language codes

| xcstrings locale | DeepL target_lang | Notes |
|---|---|---|
| `es-MX` | `ES-419` | Latin American Spanish — use this, not `ES` |
| `en` | `EN` | Source |
| `pt-BR` | `PT-BR` | Brazilian Portuguese |
| `fr` | `FR` | French |
| `de` | `DE` | German |
| `ja` | `JA` | Japanese |

**Always use `ES-419` for Mexican Spanish**, not `ES`. DeepL doesn't support `ES-MX` directly.

### Formality

All scripts default to `--formality less` — this gives `tú` form (informal) in Spanish,
which is correct for Mexican app UI. `usted` form (`--formality more`) is wrong for this context.

---

## Workflow

Recommended order when localizing or after adding new strings:

```
1. xcstrings_validate_specifiers.py  ← run FIRST and LAST — catches build-breaking bugs
2. xcstrings_audit.py                ← see coverage stats
3. xcstrings_add_missing.py          ← add brand-new strings with DeepL
4. xcstrings_fix_with_deepl.py       ← fill gaps in existing strings
5. xcstrings_verify.py               ← spot-check translation quality
6. xcstrings_validate_specifiers.py  ← run again before committing
```

---

## Scripts

### `xcstrings_validate_specifiers.py` ⚠️ run first and last
**When to use**: Before and after any translation work. Catches format specifier bugs
that cause `xcodebuild` errors (the `%la` bug that broke Chlorophyll's build).

```bash
python3 xcstrings_validate_specifiers.py \
    --xcstrings chlorophyll-ios/Localizable.xcstrings \
    --source-locale en \
    --target-locale es-MX
```

What it catches:
- `%la` instead of `%lld` — DeepL translated `las` (Spanish "the") and fused it with `%ll`
- `%1$llds` — positional specifier with Spanish suffix attached (`lld` + `s` from `las`)
- Mismatched specifier count between source and target
- Any specifier type mismatch (`%d` vs `%@`)

Exit code 1 if any issues found (use in CI). Exit code 0 if clean.

---

### `xcstrings_audit.py`
**When to use**: Quickly see coverage — how many strings have both languages,
how many are missing, how many are machine-translated and need review.

```bash
python3 xcstrings_audit.py \
    --xcstrings Spark/Localizable.xcstrings \
    --target-locale es-MX \
    --show-missing \
    --show-needs-review
```

Key flags: `--show-missing`, `--show-needs-review`

---

### `xcstrings_add_missing.py`
**When to use**: After identifying user-facing strings in Swift source that aren't yet
in xcstrings. Supply a manifest file listing the new strings.

**Step 1** — Create `strings_to_add.py`:
```python
MISSING_STRINGS = [
    # (key, comment_for_translators, explicit_translation_or_None)
    ("Today", "Navigation title for today view", None),          # None = call DeepL
    ("All done!", "Banner when all habits complete", "¡Todo listo!"),  # explicit override
    ("Every %lld day(s)", "Schedule stepper label", None),       # specifiers protected
]
```

**Step 2** — Run:
```bash
python3 xcstrings_add_missing.py \
    --xcstrings Spark/Localizable.xcstrings \
    --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
    --strings-file strings_to_add.py \
    --target-lang ES-419 \
    --locale es-MX
```

Notes:
- Keys already in xcstrings are silently skipped.
- Format specifiers (`%lld`, `%@`, `%1$lld` etc.) are wrapped in XML tags before
  sending to DeepL so they come back untouched.
- Strings with `&` in them will get 400 errors from DeepL's XML parser —
  use explicit translations for those (e.g. `"CSV & PDF Export"` → `"Exportar CSV y PDF"`).
- Use `--dry-run` to preview without writing.
- Keys are sorted alphabetically after writing (xcstrings convention).

---

### `xcstrings_fix_with_deepl.py`
**When to use**: When xcstrings has strings with only one language.
Fills in the gap automatically.

```bash
python3 xcstrings_fix_with_deepl.py \
    --xcstrings Spark/Localizable.xcstrings \
    --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
    --target-lang ES-419 \
    --target-locale es-MX \
    --formality less
```

Behavior:
- Source-only strings → translated to target, marked `needs-review`
- Target-only strings → translated back to source, marked `translated`
- Both present → unchanged
- Use `--dry-run` to preview

---

### `xcstrings_verify.py`
**When to use**: After machine translation to spot potential errors.

```bash
python3 xcstrings_verify.py \
    --xcstrings Spark/Localizable.xcstrings \
    --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
    --target-lang ES-419 \
    --target-locale es-MX \
    --sample 30 \
    --threshold 0.5
```

Uses word-level Jaccard similarity. Low similarity ≠ wrong — idioms will always differ.
Exit code 1 if issues found.

---

## The %la Bug — Lessons Learned (Chlorophyll, April 2026)

The key `'%lld plants need care: %@%@.'` had es-MX value:
`'%1$las plantas necesitan cuidados: %2$@%3$@.'`

What happened: DeepL translated `%1$lld` partially — it saw `lld` as part of `llda` →
the Spanish article `las` got fused with the specifier: `%1$la` then `s plantas`.

Two-stage failure:
1. Initial translate: `%1$la` (invalid specifier type — `a` is not `d`)
2. First fix attempt: `%1$llds` (correct specifier but `s` from `las` still attached)
3. Final fix: `%1$lld plantas` (correct)

**Prevention**: All scripts now use `tag_handling=xml` with `<x id="N">%1$lld</x>` wrapping.
DeepL treats `<x>` tags as opaque tokens and never touches their contents.

**Detection**: Run `xcstrings_validate_specifiers.py` — it catches both `%la` and `%llds` patterns.

---

## Common Patterns for Claude

### Finding untranslated strings in an iOS/macOS project

1. Grep Swift files for `Text("`, `String(localized:`, `.navigationTitle("`, `Label("` etc.
2. Cross-reference against existing xcstrings keys.
3. Build `strings_to_add.py` manifest.
4. Run `xcstrings_add_missing.py`.

```bash
# Quick audit: strings in Swift that look like UI labels
grep -rh 'Text("\|String(localized: "\|navigationTitle("\|Label("' \
    chlorophyll-ios/ --include="*.swift" \
    | grep -oP '(?<=")[^"]+(?=")' | sort -u
```

### Fixing a specific bad translation in-place

```python
import json
path = "chlorophyll-ios/Localizable.xcstrings"
with open(path) as f:
    data = json.load(f)
data["strings"]["Manual-first. No AI. Yours."]["localizations"]["es-MX"]["stringUnit"]["value"] = "Manual, sin IA, tuyo."
with open(path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
```

### Strings that need explicit translations (don't send to DeepL)

- Product names: "Chlorophyll", "Spark", "Klick" — keep in English
- Strings with `&`: DeepL's `tag_handling=xml` treats `&` as malformed XML → 400 error.
  Use explicit translation in the manifest, e.g. `("CSV & PDF Export", "...", "Exportar CSV y PDF")`
- App-specific idioms: "forgiving streaks", "shame-free" — verify DeepL output carefully

### xcstrings format reference

```json
{
  "sourceLanguage": "en",
  "strings": {
    "Every %lld day(s)": {
      "comment": "Schedule stepper label — %lld is a number",
      "localizations": {
        "en":   { "stringUnit": { "state": "translated", "value": "Every %lld day(s)" } },
        "es-MX":{ "stringUnit": { "state": "translated", "value": "Cada %lld día(s)" } }
      }
    }
  },
  "version": "1.1"
}
```

State values: `"translated"` | `"needs-review"` | `"new"`

---

## Noise Key Cleanup (run before translating)

Xcode's string catalog sync periodically re-adds "noise" keys that have no
valid Swift symbol: `%@`, `+%@`, `·`, `🌸`, `🔔`, `"%@"`, `· "%@"` etc.
These come from string interpolation patterns and emoji in Swift source.

**Run this before every translation pass:**

```python
import json, re

path = "path/to/Localizable.xcstrings"
with open(path) as f:
    data = json.load(f)

def swift_identifier(key):
    cleaned = re.sub(r'[^a-zA-Z0-9]', '', key)
    return cleaned[0].lower() + cleaned[1:] if cleaned else None

junk = [k for k in data['strings'] if swift_identifier(k) is None]
for k in junk:
    del data['strings'][k]

with open(path, 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write('\n')
print(f"Removed {len(junk)} noise keys: {junk}")
```

Also watch for symbol collisions (e.g. "No tasks yet" vs "No tasks yet.").
Fix by standardising the source Swift files to use one consistent string.
