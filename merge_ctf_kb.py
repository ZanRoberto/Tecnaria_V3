import json
import os
from glob import glob

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "static", "data")
OVERLAYS_DIR = os.path.join(DATA_DIR, "overlays")

MASTER_PATH = os.path.join(DATA_DIR, "ctf_system_COMPLETE_GOLD_v3.json")
OUTPUT_PATH = os.path.join(DATA_DIR, "ctf_system_SUPER_GOLD_v1.json")

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def main():
    # 1) Carica MASTER
    master = load_json(MASTER_PATH)
    master_blocks = master.get("blocks", [])
    print(f"MASTER: {len(master_blocks)} blocchi")

    # 2) Carica TUTTE le patch in overlays/
    overlay_paths = sorted(glob(os.path.join(OVERLAYS_DIR, "*.json")))
    overlay_blocks = []

    for p in overlay_paths:
        data = load_json(p)
        blocks = data.get("blocks", [])
        print(f"OVERLAY {os.path.basename(p)}: {len(blocks)} blocchi")
        overlay_blocks.extend(blocks)

    print(f"Totale blocchi overlay: {len(overlay_blocks)}")

    # 3) Merge per ID: overlay sovrascrive master se ha stesso id
    by_id = {}

    # Prima il master
    for b in master_blocks:
        bid = b.get("id")
        if not bid:
            continue
        by_id[bid] = b

    # Poi le overlay: sovrascrivono
    for b in overlay_blocks:
        bid = b.get("id")
        if not bid:
            continue
        by_id[bid] = b

    merged_blocks = list(by_id.values())
    print(f"Totale blocchi MERGED: {len(merged_blocks)}")

    # 4) Ordiniamo per id giusto per avere qualcosa di leggibile
    merged_blocks.sort(key=lambda x: x.get("id", ""))

    # 5) Scriviamo il nuovo SUPER MASTER
    out = {
        "family": "CTF_SYSTEM",
        "version": "SUPER_GOLD_v1",
        "blocks": merged_blocks
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print(f"File scritto: {OUTPUT_PATH}")

if __name__ == "__main__":
    main()

