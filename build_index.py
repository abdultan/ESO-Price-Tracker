#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import json
import zipfile
from pathlib import Path

# TTC PriceTable zip dosyasının adı
ZIP_FILE = "PriceTable.zip"
# Bot us sunucusunu kullanıyor, bu yüzden dosya adını us yapıyoruz
OUT_FILE = Path("cache/ttc_item_index_us.json")

def parse_lua(text: str):
    """
    ItemLookUpTable_EN.lua içinden item_name -> item_id eşlemesi çıkarır
    """
    items = {}
    # Pattern: ["dram of health"] = {[450]=14,},
    pattern = re.compile(r'\["([^"]+)"\]\s*=\s*\{\[\d+\]\s*=\s*(\d+)', re.IGNORECASE)
    for m in pattern.finditer(text):
        name = m.group(1).strip()
        item_id = int(m.group(2))
        items[name.lower()] = item_id
    return items

def build_index():
    OUT_FILE.parent.mkdir(exist_ok=True)
    all_items = {}

    with zipfile.ZipFile(ZIP_FILE, "r") as z:
        for name in z.namelist():
            if not name.endswith("ItemLookUpTable_EN.lua"):
                continue
            print(f"📂 {name} işleniyor...")
            content = z.read(name).decode("utf-8", errors="ignore")
            part = parse_lua(content)
            all_items.update(part)

    print(f"✅ {len(all_items)} İngilizce item bulundu.")
    OUT_FILE.write_text(
        json.dumps({"map": all_items}, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )
    print(f"💾 JSON kaydedildi: {OUT_FILE}")

if __name__ == "__main__":
    build_index()
