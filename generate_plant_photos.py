#!/usr/bin/env python3
"""
generate_plant_photos.py — Generate Chlorophyll sample plant photos via Leonardo.ai API.

Usage:
    python3 generate_plant_photos.py --api-key <KEY>
    python3 generate_plant_photos.py  # reads LEONARDO_API_KEY from env

Generates 7 plants × 4 photos each = 28 images.
Each plant is assigned to one of three settings:
  - home                : cozy indoor home interior
  - home_garden         : outdoor covered patio / home garden
  - professional        : commercial greenhouse / nursery growing operation

Output: chlorophyll-shared/sample-data-assets/<plant_slug>_<suffix>.jpg
  thumb  — tight portrait, hero thumbnail
  01     — wide environment / establishing shot
  02     — close-up macro of leaves / texture detail
  03     — care-in-action moment (watering, checking, tending)

Model: Leonardo Kino XL + PhotoReal v2 + alchemy
Resolution: 1024×1024 (square — matches both iOS photoData and Android photoData)
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

API_BASE    = "https://cloud.leonardo.ai/api/rest/v1"
MODEL_ID    = "aa77f04e-3eec-4034-9c07-d0f619684628"   # Leonardo Kino XL (PhotoReal v2)
IMG_SIZE    = 1024
POLL_SLEEP  = 4    # seconds between status polls
POLL_MAX    = 30   # max polls before timeout (~2 min)
REQ_DELAY   = 2    # seconds between generation requests (rate-limit courtesy)

NEGATIVE = (
    "cartoon, illustration, painting, drawing, sketch, anime, 3d render, "
    "text, watermark, logo, signature, border, frame, collage, split image, "
    "people, faces, hands (except caring gesture), ugly, deformed, blurry"
)

# ── Plant catalogue ───────────────────────────────────────────────────────────

# setting_ctx: shared environment description injected into every prompt for that plant
SETTINGS = {
    "home": (
        "in a warm cozy home interior, large windows letting in soft natural light, "
        "wooden furniture and neutral tones in the background, home décor visible, "
        "potted plant on a table or shelf"
    ),
    "home_garden": (
        "on a sun-drenched covered patio or home garden, terracotta and ceramic pots, "
        "dappled outdoor light, brick or wooden decking, other greenery nearby, "
        "relaxed residential garden atmosphere"
    ),
    "professional": (
        "inside a clean commercial greenhouse or professional plant nursery, "
        "long rows of plants under diffused glass-ceiling light or professional grow lights, "
        "neat growing tables, professional horticulture setting, organized and bright"
    ),
}

PLANTS = [
    {
        "slug":    "monstera",
        "name":    "Monstera",
        "species": "Monstera deliciosa",
        "setting": "home",
        "detail":  "large fenestrated split leaves, aerial roots, mature specimen",
    },
    {
        "slug":    "pothos",
        "name":    "Golden Pothos",
        "species": "Epipremnum aureum",
        "setting": "home",
        "detail":  "trailing golden-green heart-shaped variegated leaves, vines cascading from shelf",
    },
    {
        "slug":    "peace_lily",
        "name":    "Peace Lily",
        "species": "Spathiphyllum wallisii",
        "setting": "home",
        "detail":  "glossy dark-green leaves, elegant white spathes in bloom",
    },
    {
        "slug":    "fiddle_leaf",
        "name":    "Fiddle Leaf Fig",
        "species": "Ficus lyrata",
        "setting": "home_garden",
        "detail":  "large wavy violin-shaped leaves, tall slender trunk, deep green foliage",
    },
    {
        "slug":    "zz_plant",
        "name":    "ZZ Plant",
        "species": "Zamioculcas zamiifolia",
        "setting": "home_garden",
        "detail":  "waxy deep-green oval leaflets on arching stems, compact tidy form",
    },
    {
        "slug":    "snake_plant",
        "name":    "Snake Plant",
        "species": "Dracaena trifasciata",
        "setting": "home_garden",
        "detail":  "tall upright sword-shaped leaves with yellow-edged green banding pattern",
    },
    {
        "slug":    "barrel_cactus",
        "name":    "Barrel Cactus",
        "species": "Ferocactus wislizeni",
        "setting": "professional",
        "detail":  "round ribbed barrel shape, prominent reddish spines, desert succulent",
    },
    {
        "slug":    "tolumnia",
        "name":    "Tolumnia",
        "species": "Tolumnia jairak goldfish",
        "setting": "home",
        "detail":  "miniature equitant orchid, tiny 4cm fan of leaves, clusters of bright orange and brown-spotted flowers on thin spikes, mounted on cork bark or in a tiny clay pot",
    },
    {
        "slug":    "neofinetia",
        "name":    "Neofinetia",
        "species": "Vanda falcata",
        "setting": "home",
        "detail":  "small elegant Japanese orchid, compact rosette of narrow curved leaves, pure white fragrant flowers with long nectar spurs, often mounted on a wooden plaque or in a decorative ceramic pot",
    },
    {
        "slug":    "brassia",
        "name":    "Brassia",
        "species": "Brassia arcuigera",
        "setting": "home",
        "detail":  "spider orchid with dramatic long spidery petals and sepals, yellow-green with brown spotting, large pseudobulbs, arching spike of 8-12 flowers",
    },
    {
        "slug":    "cattleya",
        "name":    "Cattleya",
        "species": "Cattleya tangerine mini sun",
        "setting": "home",
        "detail":  "miniature Cattleya orchid, vivid neon-orange ruffled flowers, compact pseudobulbs, blooming with 2-3 flowers from a papery sheath, warm rich colour",
    },
]

# Photo variants: (suffix, role description for the prompt)
VARIANTS = [
    (
        "thumb",
        "close portrait shot, plant filling most of the frame, "
        "a pair of hands gently misting or watering the plant with care, "
        "warm inviting light, sharp focus on plant, soft background bokeh, "
        "hero thumbnail composition",
    ),
    (
        "01",
        "wide establishing shot showing the plant in its full environment, "
        "plant is the clear focal point but surroundings tell the setting story, "
        "natural lifestyle photography",
    ),
    (
        "02",
        "extreme close-up macro of the leaves and stem texture, "
        "showcasing health, colour, and fine surface detail, "
        "water droplets catching light, shallow depth of field",
    ),
    (
        "03",
        "care-in-action moment — hands checking soil moisture with a finger, "
        "or tilting a watering can, or examining new growth, "
        "candid and natural, warm caring atmosphere",
    ),
]

# ── Leonardo API helpers ──────────────────────────────────────────────────────

def _request(method: str, path: str, api_key: str, payload: dict | None = None) -> dict:
    url  = f"{API_BASE}{path}"
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code} from {url}: {body[:300]}", file=sys.stderr)
        raise


def start_generation(api_key: str, prompt: str) -> str:
    """Kick off a generation and return the generation ID."""
    payload = {
        "height":           IMG_SIZE,
        "width":            IMG_SIZE,
        "modelId":          MODEL_ID,
        "prompt":           prompt,
        "negative_prompt":  NEGATIVE,
        "num_images":       1,
        "photoReal":        True,
        "photoRealVersion": "v2",
        "alchemy":          True,
        "presetStyle":      "PHOTOGRAPHY",
    }
    resp = _request("POST", "/generations", api_key, payload)
    return resp["sdGenerationJob"]["generationId"]


def poll_generation(api_key: str, gen_id: str) -> str:
    """Poll until COMPLETE; return the image URL."""
    for attempt in range(POLL_MAX):
        time.sleep(POLL_SLEEP)
        resp   = _request("GET", f"/generations/{gen_id}", api_key)
        job    = resp.get("generations_by_pk", {})
        status = job.get("status", "PENDING")
        if status == "COMPLETE":
            images = job.get("generated_images", [])
            if not images:
                raise RuntimeError(f"Generation {gen_id} complete but no images returned")
            return images[0]["url"]
        if status == "FAILED":
            raise RuntimeError(f"Generation {gen_id} failed")
        print(f"    [{attempt+1}/{POLL_MAX}] status={status} …", flush=True)
    raise TimeoutError(f"Generation {gen_id} did not complete within {POLL_MAX * POLL_SLEEP}s")


def download_image(url: str, dest: Path) -> None:
    """Download image from URL and save to dest."""
    req = urllib.request.Request(url, headers={"User-Agent": "chlorophyll-seeder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        dest.write_bytes(resp.read())

# ── Prompt builder ────────────────────────────────────────────────────────────

def build_prompt(plant: dict, variant_desc: str) -> str:
    setting_ctx = SETTINGS[plant["setting"]]
    return (
        f"Realistic plant care photography, {plant['name']} ({plant['species']}), "
        f"{plant['detail']}, {setting_ctx}. "
        f"{variant_desc}. "
        "Natural lighting, photorealistic, high-quality DSLR photography, "
        "no filters, no text."
    )

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate Chlorophyll sample plant photos via Leonardo.ai")
    parser.add_argument("--api-key", default=os.environ.get("LEONARDO_API_KEY"),
                        help="Leonardo.ai API key (or set LEONARDO_API_KEY env var)")
    parser.add_argument("--output-dir",
                        default=str(Path(__file__).parent / "shared-contract" / "sample-data-assets"),
                        help="Directory to save generated images")
    parser.add_argument("--skip-existing", action="store_true", default=True,
                        help="Skip generation if output file already exists (default: True)")
    parser.add_argument("--slugs", nargs="+", metavar="SLUG",
                        help="Only generate these plant slugs (e.g. --slugs tolumnia cattleya)")
    args = parser.parse_args()

    if not args.api_key:
        print("Error: No API key provided. Use --api-key or set LEONARDO_API_KEY.", file=sys.stderr)
        sys.exit(1)

    plants = PLANTS
    if args.slugs:
        plants = [p for p in PLANTS if p["slug"] in args.slugs]
        if not plants:
            print(f"Error: no plants matched slugs {args.slugs}", file=sys.stderr)
            sys.exit(1)
        print(f"Filtering to {len(plants)} plant(s): {', '.join(p['slug'] for p in plants)}\n")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output directory: {out_dir}")
    print(f"Model: Kino XL (PhotoReal v2, alchemy)\n")

    total   = len(plants) * len(VARIANTS)
    done    = 0
    skipped = 0
    failed  = []

    for plant in plants:
        print(f"🌿  {plant['name']} ({plant['species']}) — {plant['setting'].replace('_', ' ')}")
        for suffix, variant_desc in VARIANTS:
            filename = f"{plant['slug']}_{suffix}.jpg"
            dest     = out_dir / filename
            done    += 1

            if args.skip_existing and dest.exists():
                print(f"  ✓ {filename} (exists, skipping)")
                skipped += 1
                continue

            prompt = build_prompt(plant, variant_desc)
            print(f"  → {filename}")
            print(f"    Prompt: {prompt[:120]}…")

            try:
                gen_id = start_generation(args.api_key, prompt)
                print(f"    Generation ID: {gen_id}")
                img_url = poll_generation(args.api_key, gen_id)
                download_image(img_url, dest)
                print(f"    ✓ Saved {filename} ({dest.stat().st_size // 1024} KB)")
            except Exception as exc:
                print(f"    ✗ FAILED: {exc}", file=sys.stderr)
                failed.append(filename)

            if done < total:
                time.sleep(REQ_DELAY)

        print()

    print("─" * 60)
    print(f"Done. {total - len(failed) - skipped} generated, {skipped} skipped, {len(failed)} failed.")
    if failed:
        print(f"Failed: {', '.join(failed)}")
        print("Re-run to retry failed images (--skip-existing is on by default).")
    else:
        print(f"All images saved to {out_dir}")


if __name__ == "__main__":
    main()
