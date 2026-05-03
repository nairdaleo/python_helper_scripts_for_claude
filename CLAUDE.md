# Python Helper Scripts — Localization (xcstrings + DeepL) & Play Store Helpers

## Play Store Tags

The complete, authoritative list of available Google Play Store tags is at:
`play_store_helpers/play-store-tags.csv`

This is the only set of tags available in the Play Store console — do not suggest tags outside this list. Format is `tag name, category`. Spark (Health & Fitness app) relevant tags: **Activity tracker**, **Health & fitness**, **Meditation**, **Productivity**, **Self-help**, **Sleep**, **Workout**.

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
| `fr` | `FR` | French (France) |
| `fr-CA` | `FR` | Canadian French — DeepL has no fr-CA target; translate as `FR` then do a manual Québécois pass (see below) |
| `de` | `DE` | German |
| `ja` | `JA` | Japanese |
| `ko` | `KO` | Korean — no formality support in DeepL |

**Always use `ES-419` for Mexican Spanish**, not `ES`. DeepL doesn't support `ES-MX` directly.

### Formality

All scripts default to `--formality less` — this gives `tú` form (informal) in Spanish,
which is correct for Mexican app UI. `usted` form (`--formality more`) is wrong for this context.

DeepL formality support by language:
- `ES-419`, `DE`, `FR`, `PT-BR`, `JA` — formality supported, use `less`
- `KO` — **no formality support**; omit the `formality` param entirely or DeepL returns 403

### fr-CA (Québécois French) — manual pass required

DeepL produces France French (`FR`) for both `fr` and `fr-CA`. After machine translation,
do a manual Québécois vocabulary pass. Key substitutions:

| France French | Québécois French | Notes |
|---|---|---|
| appli | app | Québécois universally say "app" |
| salle de sport | gym | Everyone says "gym" in QC |
| balaye (swipe) | swipe | Anglicism is accepted and expected |
| Fixe un objectif | Mets-toi un objectif | More natural QC phrasing |
| En franchissant l'objectif | Quand tu l'atteins | Natural QC |
| collecte / recueille | ramasse | QC idiom for "collect data" |
| arrive | débarque | Punchy QC idiom for a product launch/arrival |
| tu as | t'as | Conversational elision, normal in QC copy |
| papier | bout de papier | More natural in context "track on a piece of paper" |
| Zéro pub | Zéro pub / traçage / nuage | Punchier register for QC audience |

**Tone:** QC marketing copy is more direct and casual than France French. The `tu` form
is mandatory throughout. Anglicisms like "swipe", "app", "gym", "widget", "tap" are
all correct and expected — don't replace them with calques.

**fr-FR formality note:** Use `--formality more` (vous) for France French store copy —
the polite register is conventional for FR app store listings. Use `--formality less`
(tu) for fr-CA, which is informal by convention.

---

## Workflow

**ALWAYS run the probe before any DeepL session.** It costs < 100 characters, takes ~2 s,
and catches a dead key, a quota surprise, or a specifier bug before you waste 10 000 chars.

```
0. xcstrings_deepl_probe.py          ← run BEFORE anything else — verifies key + quota + specifiers
1. xcstrings_validate_specifiers.py  ← run FIRST and LAST — catches build-breaking bugs
2. xcstrings_audit.py                ← see coverage stats
3. xcstrings_add_missing.py          ← add brand-new strings with DeepL
4. xcstrings_fix_with_deepl.py       ← fill gaps in existing strings
5. xcstrings_verify.py               ← spot-check translation quality
6. xcstrings_validate_specifiers.py  ← run again before committing
```

---

## Scripts

### `xcstrings_deepl_probe.py` 🔍 run before every session

**When to use**: Before any DeepL translation batch — always, no exceptions.
Verifies the API key is live, checks remaining character quota, and sends a short
test string (with a `%lld` specifier) to every target language to confirm both
connectivity and specifier protection are working correctly.

```bash
python3 xcstrings_deepl_probe.py \
    --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
    --target-langs ES-419 DE FR JA KO PT-BR \
    --min-chars 10000
```

Key flags:
- `--target-langs` — space-separated DeepL language codes to probe (default: `ES-419`)
- `--min-chars` — abort threshold; default 10 000. Lower it if your batch is known-small.

Exit code 0 = all clear. Exit code 1 = do not proceed.

What it checks:
1. **API key validity** — hits `/v2/usage`; catches 403 (bad key) and 456 (quota exceeded) immediately
2. **Remaining quota** — prints used/limit/remaining; fails if remaining < `--min-chars`
3. **Test translation per language** — translates `"Hello, world! Ready in %lld seconds."` to each
   target language and verifies `%lld` survived intact (catches specifier corruption before it
   poisons a large batch)

Typical output (all clear):

```
======================================================================
DEEPL PRE-FLIGHT PROBE
  API endpoint : https://api-free.deepl.com
  Target langs : ES-419, DE, FR
  Min quota    : 10,000 chars required
======================================================================

[1/2] Checking usage quota...
  Used      :     12,345 / 500,000 chars  (2.5%)
  Remaining :    487,655 chars
  ✓ Quota OK

[2/2] Test translation → ES-419, DE, FR...
  Source: 'Hello, world! Ready in %lld seconds.'
  ✓ ES-419   '¡Hola, mundo! Listo en %lld segundos.'
  ✓ DE       'Hallo, Welt! Fertig in %lld Sekunden.'
  ✓ FR       'Bonjour, monde ! Prêt dans %lld secondes.'

======================================================================
RESULT: ✓ All checks passed — safe to start your session.
======================================================================
```

---

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

---

## Respecting "Don't Translate" in Xcode

When a key is marked "Don't Translate" in Xcode's string catalog editor, Xcode writes `"shouldTranslate": false` to the xcstrings entry.

Both `xcstrings_translate_all.py` and `xcstrings_fix_with_deepl.py` now skip keys where `shouldTranslate` is `false`. No action needed — it's automatic.

Example entry:
```json
"Settings": {
  "shouldTranslate": false,
  "localizations": {
    "en": { "stringUnit": { "state": "translated", "value": "Settings" } }
  }
}
```

**Note:** Xcode only writes the flag to disk during a full localization sync. If you just marked a key in the editor, close and reopen Xcode (or trigger a build) to flush the flag before running the translation scripts.

---

## `xcstrings_translate_manifest.py` — manifest × all locales in one pass

Fills the gap between the two existing translators:

- `xcstrings_add_missing.py` — one locale at a time, fails on existing keys.
- `xcstrings_fix_with_deepl.py` — all keys in the catalog, no manifest filter (churns through hundreds of unrelated strings).
- `xcstrings_translate_manifest.py` — only the keys in your manifest, but to *every* locale you list, in one run.

Use this when you've shipped a feature and Xcode has already extracted the new strings into the catalog (so they exist as keys with no localizations populated). The same `MISSING_STRINGS` manifest format used by `xcstrings_add_missing.py`.

```bash
python3 xcstrings_translate_manifest.py \
    --xcstrings Spark/Localizable.xcstrings \
    --manifest spark_v15_strings.py \
    --api-key 93d18a72-a06f-4521-a3a2-5aad7ca79ce3:fx \
    --locales fr-FR=FR fr-CA=FR de-DE=DE ja=JA ko=KO pt-BR=PT-BR \
    --formality less
```

Locales are space-separated `xcstrings_locale=DEEPL_LANG` pairs. Formality is auto-skipped for languages where DeepL doesn't support it (KO especially). Use `--dry-run` to preview without writing.

---

## Common DeepL mistranslations Claude should watch for

DeepL is excellent for sentences with context, terrible for short ambiguous UI strings. After every translation pass, audit these specific patterns and override with explicit translations where needed.

### 1. `trial` → translated as legal/court trial in every locale

When the source word "trial" appears alone (e.g. *"What happens after the trial?"*), DeepL invariably picks the legal-courtroom homonym instead of the free-trial period sense.

| Locale | Wrong (DeepL default) | Right (free-trial sense) |
|---|---|---|
| es-MX | "después del juicio" | "después de la prueba gratuita" |
| fr-FR / fr-CA | "après le procès" | "après l'essai gratuit" |
| de-DE | "nach dem Prozess" | "nach der Testphase" |
| ja | "裁判が終わったら" | "無料トライアル終了後" |
| ko | "재판이 끝나면" | "무료 체험이 끝나면" |
| pt-BR | "depois do julgamento" | "depois do teste gratuito" |

**Fix:** explicit override in the manifest, OR phrase the English source as *"What happens after the free trial?"* — the word "free" is enough context for DeepL to pick the right sense.

### 2. `save` → translated as save-a-file, not save-money

`· save $30` becomes `· guardar $30` (file-save) instead of `· ahorras $30` (money-save) in nearly every locale.

| Locale | Wrong (file save) | Right (money save) |
|---|---|---|
| es-MX | "guardar" | "ahorras" |
| fr-FR / fr-CA | "enregistrer" | "économise" |
| de-DE | "speichern" | "spare" |
| ja | "%@を保存" | "%@お得" |
| ko | "%@ 저장" | "%@ 절약" |
| pt-BR | "salvar" | "economize" |

**Fix:** explicit override, OR phrase the English source as *"save $30"* in a longer context like *"$2.50/mo · you save $30"* — adding a subject/verb gives DeepL enough disambiguation.

### 3. Brand-style verbs kept verbatim (English)

Short two-word product CTAs like `Restore Pro`, `Reset Tally`, `Open Spark` — DeepL has no concept of the brand and often returns the source string unchanged across all locales because it can't decide which word is the verb.

**Fix:** explicit override per locale, OR use a fuller phrase in the source: `Restore Pro features` instead of `Restore Pro` — DeepL then translates the verb correctly.

### 4. pt-BR output sometimes leaks Continental Portuguese

DeepL's `PT-BR` target generally produces Brazilian Portuguese, but on longer or rarer constructions it can fall back to PT-PT forms. Words/forms to search-and-replace in pt-BR output:

| PT-PT (wrong for BR) | PT-BR (right) |
|---|---|
| `teu` / `tua` (your) | `seu` / `sua` |
| `podes` | `você pode` |
| `pagaste` | `pagou` |
| `tu fizeres` | `você fizer` |
| `tu te ...` (reflexive tu) | `você se ...` |

**Fix:** post-pass search/replace, or explicit override on the offending strings.

### 5. ko: `사용자의` (the user's) reads awkwardly in product UX

DeepL inserts `사용자의` (literal "the user's") wherever the English source has "your" as a possessive on a list of items. In Korean product UX it's idiomatic to drop the second-person possessive entirely on lists.

**Fix:** in the polish pass, remove the leading `사용자의` from KO translations of phrases like *"Your habits, streaks, and history all stay..."* — `습관, 연속 기록, 이용 내역은 모두 그대로 유지됩니다` reads more naturally.

Also: DeepL sometimes renders English `then` as `그 다음` (casual "next thing") in billing copy. Replace with `이후` for a more neutral register.

---

## Recommended translation workflow (updated)

```
0. xcstrings_deepl_probe.py          ← verify key + quota + specifier protection
1. xcstrings_validate_specifiers.py  ← catch %la-style bugs before AND after
2. xcstrings_audit.py                ← see coverage stats per locale
3. xcstrings_add_missing.py          ← add brand-new keys for ONE locale
   OR
   xcstrings_translate_manifest.py   ← translate ONLY manifest keys to ALL locales
4. xcstrings_fix_with_deepl.py       ← fill ALL remaining gaps in the catalog
5. Manual override pass              ← fix the 5 systemic mistranslations above
6. xcstrings_verify.py               ← spot-check translation quality
7. xcstrings_validate_specifiers.py  ← final check before committing
```
