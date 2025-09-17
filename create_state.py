#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)
STATE_FILE = CACHE_DIR / "storage_state.json"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=100)
        context = await browser.new_context()
        page = await context.new_page()

        print("🌍 TTC açılıyor... Captcha çöz, ardından mutlaka bir item araması yap (fiyat tablosu açılmalı).")
        await page.goto("https://us.tamrieltradecentre.com/pc/Trade", wait_until="domcontentloaded")

        # Kullanıcıya manuel çözmesi için süre tanıyoruz
        while True:
            cookies = await context.cookies()
            cookie_names = [c["name"] for c in cookies]
            print("🍪 Aktif cookie’ler:", cookie_names)

            # ✅ cf_clearance zorunluluğu kaldırıldı
            if await page.query_selector("table.trade-list-table tbody tr"):
                await context.storage_state(path=str(STATE_FILE))
                print(f"💾 {STATE_FILE.name} güncellendi ({len(cookies)} cookie kaydedildi).")
                break

            await page.wait_for_timeout(5000)  # tekrar dene

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
