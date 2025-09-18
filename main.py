#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
main.py — ESO Price Tracker (Playwright + storage_state captcha bypass)
- İlk defa: /test komutu headful açar; captcha'yı manuel çöz -> cache/storage_state.json kaydedilir.
- Sonraki çalıştırmalarda headless + storage_state reuse ile otomatik çalışır.
"""

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote
from telegram.request import HTTPXRequest
from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# -------------------------
# Config / env
# -------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
TTC_REGION = os.getenv("TTC_REGION", "eu").strip().lower()  # default eu
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "300"))  # seconds
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "600"))  # seconds
PROXIES = [p.strip() for p in os.getenv("PROXIES", "").split(",") if p.strip()]

if not BOT_TOKEN:
    raise SystemExit("❌ BOT_TOKEN .env içinde olmalı.")

# logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.DEBUG  # prod'da INFO yapabilirsin
)
log = logging.getLogger("ESOPriceBot")

# paths
BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"
CACHE_DIR.mkdir(exist_ok=True)
ITEM_INDEX_JSON = CACHE_DIR / f"ttc_item_index_{TTC_REGION}.json"
STORAGE_STATE = CACHE_DIR / "storage_state.json"

# -------------------------
# Utils
# -------------------------
def esc_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

def fmt_gold(n: int) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except Exception:
        return str(n)

def now_ts() -> int:
    return int(time.time())

# -------------------------
# Database
# -------------------------
class Database:
    def __init__(self, path: str = "eso_price_tracker.db"):
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path) as con:
            cur = con.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    item_name TEXT NOT NULL,
                    threshold_price INTEGER NOT NULL,
                    current_price INTEGER DEFAULT 0,
                    last_check INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1,
                    created_at INTEGER DEFAULT (strftime('%s','now'))
                )
                """
            )
            # --- migration: yeni kolonlar yoksa ekle ---
            cur.execute("PRAGMA table_info(alerts)")
            cols = {r[1] for r in cur.fetchall()}
            if "last_notified_price" not in cols:
                cur.execute("ALTER TABLE alerts ADD COLUMN last_notified_price INTEGER")
            if "last_notified_at" not in cols:
                cur.execute("ALTER TABLE alerts ADD COLUMN last_notified_at INTEGER DEFAULT 0")
            con.commit()

    def add(self, user_id: int, username: str, item: str, price: int):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT INTO alerts (user_id, username, item_name, threshold_price) VALUES (?,?,?,?)",
                (user_id, username, item, price),
            )
            con.commit()

    def list_user(self, user_id: int) -> List[Dict]:
        with sqlite3.connect(self.path) as con:
            cur = con.cursor()
            cur.execute(
                 """SELECT id, item_name, threshold_price, current_price,
                          last_check, last_notified_price, last_notified_at
                   FROM alerts
                   WHERE user_id=? AND is_active=1
                   ORDER BY created_at DESC""",
                (user_id,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    def set_notified(self, alert_id: int, price: int):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "UPDATE alerts SET last_notified_price=?, last_notified_at=? WHERE id=?",
                (price, now_ts(), alert_id),
            )
            con.commit()

    def all_active(self) -> List[Dict]:
        with sqlite3.connect(self.path) as con:
            cur = con.cursor()
            cur.execute(
                """SELECT id, user_id, username, item_name, threshold_price,
                          current_price, last_check, last_notified_price, last_notified_at
                   FROM alerts WHERE is_active=1"""
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def set_price(self, alert_id: int, price: int):
        with sqlite3.connect(self.path) as con:
            con.execute(
                "UPDATE alerts SET current_price=?, last_check=? WHERE id=?",
                (price, now_ts(), alert_id),
            )
            con.commit()

    def deactivate(self, alert_id: int, user_id: int) -> bool:
        with sqlite3.connect(self.path) as con:
            cur = con.cursor()
            cur.execute(
                "UPDATE alerts SET is_active=0 WHERE id=? AND user_id=?",
                (alert_id, user_id),
            )
            con.commit()
            return cur.rowcount > 0

# -------------------------
# PriceResult + TTC
# -------------------------
@dataclass
class PriceResult:
    item_id: Optional[int]
    price: Optional[int]
    guild: Optional[str]
    location: Optional[str]
    link: str
    source: str  # "listing" | "fallback" | "captcha" | "error"

class TTC:
    def __init__(self, region: str = "us"):
        self.region = region
        self.base = f"https://{region}.tamrieltradecentre.com"
        self.item_index: Dict[str, int] = {}

        # paylaşımlı playwright/browser (headless)
        self.playwright = None
        self.browser = None

        try:
            if ITEM_INDEX_JSON.exists():
                obj = json.loads(ITEM_INDEX_JSON.read_text(encoding="utf-8"))
                self.item_index = obj.get("map", {})
                log.info(f"✅ {len(self.item_index)} item index yüklendi.")
            else:
                log.info("ℹ️ Item index dosyası bulunamadı (devam).")
        except Exception as e:
            log.warning("Item index yüklenemedi: %s", e)

    async def init(self):
        """Headless chromium tek sefer açılır ve reuse edilir."""
        if not self.playwright:
            self.playwright = await async_playwright().start()
        if not self.browser:
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )

    async def shutdown(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None

    async def resolve_item_id(self, item_name: str) -> Optional[int]:
        key = re.sub(r'\s+', ' ', item_name).strip().lower()
        return self.item_index.get(key)

    def _parse_price(self, price_text: str) -> Optional[int]:
        """Fiyat metnini sayıya çevirir - birim fiyatı alır."""
        try:
            if not price_text:
                return None
            # TTC formatı: "1.000 \nX\n5\n=\n5.000" -> ilk satır birim fiyat
            lines = [line.strip() for line in price_text.strip().split('\n') if line.strip()]
            if not lines:
                return None
            unit_price_line = lines[0]

            # boşluklu binlik ayıracı vs. normalize et
            clean_text = re.sub(r'[^\d\.,\s]', '', unit_price_line).replace(' ', '')
            if not clean_text:
                return None

            possible_prices = []
            if '.' in clean_text and ',' not in clean_text:
                if clean_text.count('.') == 1:
                    parts = clean_text.split('.')
                    if len(parts[1]) == 3:  # 1.000
                        price_str = clean_text.replace('.', '')
                        possible_prices.append(int(price_str))
                    else:  # ondalık
                        possible_prices.append(int(float(clean_text)))
                else:
                    price_str = clean_text.replace('.', '')
                    possible_prices.append(int(price_str))
            elif ',' in clean_text and '.' not in clean_text:
                price_str = clean_text.replace(',', '')
                possible_prices.append(int(price_str))
            elif '.' in clean_text and ',' in clean_text:
                price_str = clean_text.replace('.', '').replace(',', '.')
                possible_prices.append(int(float(price_str)))
            else:
                possible_prices.append(int(clean_text))

            for price in possible_prices:
                if price > 0:
                    return price
            return None
        except Exception as e:
            log.warning(f"Fiyat parse hatası ('{price_text}'): {e}")
            return None

    async def _new_context(self, headless: bool):
        """Headless için paylaşımlı browser, headful için geçici browser/context döndürür."""
        if headless:
            await self.init()
            browser = self.browser
            temp_browser = None
        else:
            # captcha çözümü için geçici headful browser
            if not self.playwright:
                self.playwright = await async_playwright().start()
            temp_browser = await self.playwright.chromium.launch(
                headless=False,
                slow_mo=150,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            browser = temp_browser

        context = await browser.new_context(
            storage_state=str(STORAGE_STATE) if STORAGE_STATE.exists() else None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800}
        )
        return browser, context

    async def fetch_price(self, item_name: str, headless: bool = True) -> PriceResult:
        await self.init()
        item_id = await self.resolve_item_id(item_name)

        base_url = f"{self.base}/pc/Trade/SearchResult?"
        params = [
            f"ItemNamePattern={quote(item_name)}",
            "TradeType=Sell",
            "SortBy=Price",
            "Order=asc",
            "lang=en-US",
        ]
        if item_id:
            params.insert(0, f"ItemID={item_id}")
        url = base_url + "&".join(params)

        price, guild, loc, source = None, None, None, "fallback"
        context = None
        page = None

        try:
            context = await self.browser.new_context(
                storage_state=str(STORAGE_STATE) if STORAGE_STATE.exists() else None,
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800}
            )
            page = await context.new_page()

            log.info("🌍 TTC açılıyor: %s", url)
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2000)

            # --- Ad blocker modal kontrolü ---
            try:
                ad_btn = await page.query_selector("text=Continue with first party ads")
                if ad_btn:
                    log.info("⚠️ Ad blocker modal bulundu, kapatılıyor...")
                    await ad_btn.click()
                    await page.wait_for_timeout(2000)
                else:
                    # alternatif kontrol
                    await page.evaluate("""
                        const modal = document.querySelector('.modal-dialog');
                        if (modal) {
                            const btn = modal.querySelector('button, .btn-primary');
                            if (btn) btn.click();
                        }
                    """)
            except Exception as e:
                log.debug("Ad modal bulunamadı: %s", e)

            # --- Captcha kontrolü ---
            captcha_modal = await page.query_selector("#captcha-modal")
            if captcha_modal and await captcha_modal.is_visible():
                log.warning("⚠️ Captcha çıktı! Manuel çözüm gerekiyor.")
                return PriceResult(item_id, None, None, None, url, "captcha")

            # --- Tablo bekle ---
            await page.wait_for_selector("table.trade-list-table tbody", timeout=20000)
            rows = await page.query_selector_all("table.trade-list-table tbody tr.cursor-pointer")

            if not rows:
                log.warning("Hiç ürün satırı bulunamadı: %s", item_name)
            else:
                lowest_price = None
                best_row = None

                # ilk 15 satırı tara
                for i, row in enumerate(rows[:15]):
                    try:
                        price_cell = await row.query_selector("td.gold-amount.bold") or \
                                     await row.query_selector("td[class*='gold-amount']") or \
                                     await row.query_selector("td:nth-child(4)")
                        if price_cell:
                            price_text = await price_cell.inner_text()
                            current_price = self._parse_price(price_text)
                            if current_price and current_price > 0:
                                if lowest_price is None or current_price < lowest_price:
                                    lowest_price = current_price
                                    best_row = row
                    except Exception as e:
                        log.debug(f"Satır {i+1} işlenirken hata: {e}")
                        continue

                if best_row and lowest_price:
                    price = lowest_price
                    source = "listing"
                    try:
                        cells = await best_row.query_selector_all("td")
                        if len(cells) >= 3:
                            guild = (await cells[1].inner_text()).strip()
                            loc = (await cells[2].inner_text()).strip()
                        log.info("✅ En düşük fiyat bulundu: %s = %dg", item_name, price)
                    except Exception as e:
                        log.warning("Guild/location çekilemedi: %s", e)
                        guild = guild or "Bilinmiyor"
                        loc = loc or "Bilinmiyor"

        except Exception as e:
            log.error("fetch_price genel hatası: %s", e)
            source = "error"

        finally:
            try:
                if page:
                    await page.close()
                if context:
                    await context.close()
            except Exception as e:
                log.warning("Cleanup hatası: %s", e)

        return PriceResult(item_id, price, guild, loc, url, source)


# -------------------------
# Bot
# -------------------------
class Bot:
    COOLDOWN = ALERT_COOLDOWN

    def __init__(self):
        self.db = Database()
        self.ttc = TTC(TTC_REGION)

    def _server_text(self) -> str:
        return "Avrupa (EU)" if TTC_REGION == "eu" else "Kuzey Amerika (NA)"

    def _alert_card(self, a: Dict) -> Tuple[str, InlineKeyboardMarkup]:
        # Son kontrol zamanını hesapla
        last_check = a.get("last_check", 0)
        if last_check:
            time_diff = int(time.time()) - last_check
            if time_diff < 60:
                time_str = "az önce"
            elif time_diff < 3600:
                time_str = f"{time_diff // 60} dakika önce"
            else:
                time_str = f"{time_diff // 3600} saat önce"
        else:
            time_str = "henüz kontrol edilmedi"

        # Durum analizi
        current_price = a.get("current_price", 0)
        threshold = a["threshold_price"]

        if current_price and current_price <= threshold:
            status_emoji = "🔥"
            status = "FIRSAT VAR!"
        elif current_price:
            diff_percent = ((current_price - threshold) / threshold) * 100
            if diff_percent <= 20:
                status_emoji = "⚡"
                status = "yaklaşıyor"
            else:
                status_emoji = "📊"
                status = "normal"
        else:
            status_emoji = "❓"
            status = "bilinmiyor"

        # Kart metni
        title = f"{status_emoji} <b>{esc_html(a['item_name'])}</b>\n"
        body = f"🎯 <b>Eşik:</b> {fmt_gold(threshold)}g ve altı\n"

        if current_price:
            body += f"💰 <b>Son fiyat:</b> {fmt_gold(current_price)}g ({status})\n"
            if current_price > threshold:
                diff = current_price - threshold
                body += f"📈 Eşiğe kalan: {fmt_gold(diff)}g\n"
        else:
            body += "💰 <b>Son fiyat:</b> <i>henüz sorgulanmadı</i>\n"

        # 🔔 son bildirilen fiyat ekle
        last_notified = a.get("last_notified_price")
        if last_notified:
            body += f"🔔 <b>Son bildirilen:</b> {fmt_gold(last_notified)}g\n"

        body += f"⏱ <b>Son kontrol:</b> {time_str}\n"

        # Butonlar
        kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🔄 Şimdi Kontrol", callback_data=f"check_{a['id']}"),
                InlineKeyboardButton("🗑 Sil", callback_data=f"del_{a['id']}")
            ]
        ])

        return title + body, kb


    async def cmd_start(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        user_name = u.effective_user.first_name or u.effective_user.username or "Tamriel'li"
        text = (
            f"Merhaba <b>{esc_html(user_name)}</b>!\n\n"
            "🎮 <b>ESO Price Tracker</b> - Elder Scrolls Online fiyat takip botuna hoş geldin!\n\n"
            "📱 <b>Nasıl Kullanılır:</b>\n"
            "1️⃣ <code>/add Dragon Rheum 5000</code> - Yeni alarm ekle\n"
            "2️⃣ Bot her 5 dakikada kontrol eder\n"
            "3️⃣ Fiyat düştüğünde bildirim alırsın\n\n"
            "⚡ <b>Hızlı Ekleme:</b> Mesaj olarak gönder\n"
            "<code>Kuta | 8000</code>\n\n"
            "🔧 <b>Diğer Komutlar:</b>\n"
            "• <code>/list</code> - Alarmlarını gör\n"
            "• <code>/test Dreugh Wax</code> - Anlık fiyat sorgula\n"
            "• <code>/help</code> - Detaylı yardım\n\n"
            f"🎯 Bot {self._server_text()} serverından fiyat çeker ve birim fiyatları takip eder.\n\n"
            "Hadi ilk alarmını ekle!"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📋 Popüler Itemler", callback_data="popular_items")],
            [InlineKeyboardButton("❓ Nasıl Kullanılır?", callback_data="how_to_use")],
            [InlineKeyboardButton("⚙️ İpuçları", callback_data="tips")]
        ])

        await u.message.reply_html(text, reply_markup=kb)

    async def cmd_help(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        text = (
            "📚 <b>Detaylı Kullanım Kılavuzu</b>\n\n"
            "🎯 <b>Alarm Ekleme:</b>\n"
            "• <code>/add Dreugh Wax 50000</code>\n"
            "• Mesaj: <code>Kuta | 8000</code>\n"
            "• Fiyatları nokta/virgül olmadan yazın (50000 ✅, 50.000 ❌)\n\n"
            "📊 <b>Fiyat Kontrolü:</b>\n"
            "• <code>/test Dreugh Wax</code> - Anlık fiyat sorgula (captcha çıkarsa çözebilmen için tarayıcı açar)\n"
            "• <code>/checknow</code> - Tüm alarmları zorla kontrol et\n\n"
            "📋 <b>Alarm Yönetimi:</b>\n"
            "• <code>/list</code> ile alarmlarını gör\n"
            "• Her alarmın yanında 'Şimdi Kontrol Et' ve 'Sil' butonları var\n\n"
            "⚙️ <b>Bot Özellikleri:</b>\n"
            "• Otomatik 5 dakikada bir kontrol\n"
            "• Captcha bypass sistemi (storage_state)\n"
            "• Çoklu kullanıcı desteği\n"
            "• Spam koruması (10 dk cooldown)\n\n"
            "❓ <b>Sorun mu var?</b>\n"
            "• Captcha çıkarsa <code>/test ItemAdı</code> komutu ile manual çöz\n"
            "• Item bulunamazsa tam adını kontrol et\n"
            "• Çok fazla alarm ekleme (max 10-15 öneriyoruz)\n\n"
            "💬 İyi alışveriş!"
        )
        await u.message.reply_html(text)

    async def cmd_add(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if len(c.args) < 2:
            example_text = (
                "❌ <b>Eksik bilgi!</b>\n\n"
                "✅ <b>Doğru kullanım:</b>\n"
                "• <code>/add Dreugh Wax 50000</code>\n"
                "• <code>/add Kuta 8000</code>\n"
                "• <code>/add Perfect Roe 150000</code>\n\n"
                "💡 <b>İpucu:</b> Veya mesaj olarak gönder:\n"
                "<code>Dreugh Wax | 50000</code>\n\n"
                "🎯 Bot belirlediğin fiyat veya altında item bulduğunda sana haber verecek!"
            )
            return await u.message.reply_html(example_text)

        *name_parts, price = c.args
        item = " ".join(name_parts).strip()

        if len(item) < 2:
            return await u.message.reply_html("❌ Item adı en az 2 karakter olmalı!")

        try:
            thr = int(str(price).replace(".", "").replace(",", ""))
            if thr <= 0:
                return await u.message.reply_html("❌ Fiyat 0'dan büyük olmalı!")
        except Exception:
            return await u.message.reply_html(
                "❌ Fiyat sayı olmalı!\n\n"
                "✅ <b>Doğru:</b> <code>/add Dreugh Wax 50000</code>\n"
                "❌ <b>Yanlış:</b> <code>/add Dreugh Wax elli bin</code>\n\n"
                "💡 Sadece rakam kullan (50000, 150000 gibi)"
            )

        # Kullanıcının alarm sayısını kontrol et
        existing_alerts = self.db.list_user(u.effective_user.id)
        if len(existing_alerts) >= 15:
            return await u.message.reply_html(
                "⚠️ En fazla 15 alarm ekleyebilirsin!\n\n"
                "🗑️ Önce bazı alarmları sil: <code>/list</code>\n\n"
                "💡 Çok alarm eklemek yerine önemli olanları seç!"
            )

        # Aynı item kontrolü
        for alert in existing_alerts:
            if alert['item_name'].lower() == item.lower():
                return await u.message.reply_html(
                    f"⚠️ <b>{esc_html(item)}</b> için zaten alarm var!\n\n"
                    f"📊 Mevcut eşik: <b>{fmt_gold(alert['threshold_price'])}g</b>\n\n"
                    "💡 Önce eskisini sil: <code>/list</code>"
                )

        self.db.add(u.effective_user.id, u.effective_user.username or "", item, thr)

        success_text = (
            "✅ <b>Alarm başarıyla eklendi!</b>\n\n"
            f"🎯 <b>Item:</b> {esc_html(item)}\n"
            f"💰 <b>Hedef fiyat:</b> {fmt_gold(thr)}g ve altı\n"
            f"⏰ <b>Kontrol sıklığı:</b> Her 5 dakika\n"
            f"🌍 <b>Server:</b> {self._server_text()}\n\n"
            "🔔 Fiyat düştüğünde hemen bildirim alacaksın!\n\n"
            "💡 İstersen şimdi test edebilirsin:"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🧪 {item} Test Et", callback_data=f"test_{item}")],
            [InlineKeyboardButton("📋 Tüm Alarmlar", callback_data="list_alerts")],
            [InlineKeyboardButton("➕ Başka Alarm Ekle", callback_data="add_more")]
        ])

        await u.message.reply_html(success_text, reply_markup=kb)

    async def cmd_list(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        arr = self.db.list_user(u.effective_user.id)
        if not arr:
            text = (
                "📭 <b>Hiç alarm yok!</b>\n\n"
                "💡 Yeni alarm eklemek için:\n"
                "• <code>/add Dreugh Wax 50000</code>\n"
                "• Veya mesaj: <code>Kuta | 8000</code>"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Nasıl Alarm Eklerim?", callback_data="help_add")]
            ])
            return await u.message.reply_html(text, reply_markup=kb)

        header_text = (
            f"📋 <b>Alarmların ({len(arr)} adet)</b>\n\n"
            "Her alarm için en son kontrol edilen fiyat gösteriliyor:"
        )
        await u.message.reply_html(header_text)

        for a in arr:
            msg, kb = self._alert_card(a)
            await u.message.reply_html(msg, reply_markup=kb)

    async def cmd_test(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not c.args:
            text = (
                "❌ <b>Item adı belirtmedin!</b>\n\n"
                "✅ <b>Doğru kullanım:</b>\n"
                "• <code>/test Dreugh Wax</code>\n"
                "• <code>/test Kuta</code>\n"
                "• <code>/test Aetherial Dust</code>\n\n"
                "💡 Bu komut itemin güncel fiyatını kontrol eder. Captcha çıkarsa tarayıcı açılır, çözebilirsin."
            )
            return await u.message.reply_html(text)

        item = " ".join(c.args)

        loading_msg = await u.message.reply_html(
            f"🔍 <b>{esc_html(item)}</b> kontrol ediliyor...\n\n"
            "⏳ Bu işlem 10-30 saniye sürebilir\n"
            "🤖 Captcha çıkarsa tarayıcı açılır (manuel çözüm)"
        )

        try:
            # /test sırasında headless=False => headful açıp storage_state güncellenebilir
            res = await self.ttc.fetch_price(item, headless=False)

            try:
                await loading_msg.delete()
            except:
                pass

            if res.source == "captcha":
                text = (
                    "⚠️ <b>Captcha Gerekli</b>\n\n"
                    f"🎯 <b>Item:</b> {esc_html(item)}\n"
                    "🔧 <b>Durum:</b> Tarayıcıda captcha'yı çöz ve tekrar dene\n\n"
                    "💡 Çözdükten sonra tekrar:\n"
                    f"<code>/test {esc_html(item)}</code>"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Tekrar Dene", callback_data=f"test_{item}")]
                ])
                return await u.message.reply_html(text, reply_markup=kb)

            if res.price:
                status_emoji = "✅"
                price_line = f"💰 <b>{fmt_gold(res.price)}g</b>"
                debug_info = f"\n🔧 <i>Debug: Kaynak fiyat parsing başarılı</i>"
            else:
                status_emoji = "⚠️"
                price_line = "💰 <i>Fiyat bulunamadı</i>"
                debug_info = f"\n🔧 <i>Debug: Fiyat parse edilemedi veya bulunamadı</i>"

            time_str = time.strftime("%H:%M", time.localtime())

            text = (
                f"{status_emoji} <b>Fiyat Kontrolü</b>\n\n"
                f"🎯 <b>Item:</b> {esc_html(item)}\n"
                f"{price_line}\n"
                f"🏪 <b>Satıcı:</b> {esc_html(res.guild or 'Bilinmiyor')}\n"
                f"📍 <b>Lokasyon:</b> {esc_html(res.location or 'Bilinmiyor')}\n"
                f"⏰ <b>Kontrol:</b> {time_str}\n\n"
                f"📊 <b>Kaynak:</b> {'TTC Gerçek Veri' if res.source == 'listing' else res.source}"
                f"{debug_info}"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 TTC'de Görüntüle", url=res.link)],
                [InlineKeyboardButton("➕ Bu Item İçin Alarm Ekle", callback_data=f"add_from_test_{item}")]
            ])

            await u.message.reply_html(text, reply_markup=kb, disable_web_page_preview=False)

        except Exception as e:
            try:
                await loading_msg.delete()
            except:
                pass
            log.error(f"Test komutu hatası: {e}")
            await u.message.reply_html(
                f"❌ <b>Hata oluştu!</b>\n\n"
                f"🎯 <b>Item:</b> {esc_html(item)}\n"
                f"🔧 <b>Hata:</b> {esc_html(str(e)[:100])}\n\n"
                "💡 Tekrar deneyin veya item adını kontrol edin."
            )

    async def cmd_checknow(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        user_alerts = self.db.list_user(u.effective_user.id)
        if not user_alerts:
            return await u.message.reply_html(
                "📭 <b>Kontrol edilecek alarm yok!</b>\n\n"
                "💡 Önce bir alarm ekle: <code>/add Dreugh Wax 50000</code>"
            )

        status_msg = await u.message.reply_html(
            f"🔄 <b>{len(user_alerts)} alarm kontrol ediliyor...</b>\n\n"
            "⏳ Bu işlem birkaç dakika sürebilir"
        )

        checked_count = 0
        found_deals = 0

        try:
            for alert in user_alerts:
                try:
                    await asyncio.sleep(random.uniform(1, 3))
                    res = await self.ttc.fetch_price(alert["item_name"], headless=True)
                    checked_count += 1

                    if res.price is not None:
                        self.db.set_price(alert["id"], res.price)

                    if res.price is not None and res.price <= alert["threshold_price"]:
                        found_deals += 1
                        deal_text = (
                            "🔥 <b>SÜPER FIRSAT BULDU!</b>\n\n"
                            f"🎯 <b>Item:</b> {esc_html(alert['item_name'])}\n"
                            f"💰 <b>Fiyat:</b> {fmt_gold(res.price)}g\n"
                            f"🎯 <b>Eşiğin:</b> {fmt_gold(alert['threshold_price'])}g\n"
                            f"🏪 <b>Satıcı:</b> {esc_html(res.guild or 'Bilinmiyor')}\n"
                            f"📍 <b>Lokasyon:</b> {esc_html(res.location or 'Bilinmiyor')}\n\n"
                            "⚡ Hemen satın almak için TTC'ye git!"
                        )

                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 TTC'de Satın Al", url=res.link)]
                        ])

                        await u.message.reply_html(deal_text, reply_markup=kb)

                    if checked_count % 3 == 0:
                        await status_msg.edit_text(
                            f"🔄 <b>İlerleme:</b> {checked_count}/{len(user_alerts)}\n\n"
                            f"✅ Kontrol edilen: {checked_count}\n"
                            f"🔥 Bulunan fırsat: {found_deals}\n\n"
                            "⏳ Devam ediyor..."
                        )

                except Exception as e:
                    log.warning(f"Manuel kontrol hatası ({alert['item_name']}): {e}")
                    continue

            final_text = (
                "✅ <b>Manuel Kontrol Tamamlandı!</b>\n\n"
                f"📊 <b>Özet:</b>\n"
                f"• Kontrol edilen: {checked_count}/{len(user_alerts)}\n"
                f"• Bulunan fırsat: {found_deals}\n\n"
                f"⏰ <b>Durum:</b> {'Fırsatlar yukarıda!' if found_deals > 0 else 'Şu anda uygun fiyat yok'}\n\n"
                "🔄 Bot otomatik kontrole devam ediyor."
            )

            await status_msg.edit_text(final_text)

        except Exception as e:
            await status_msg.edit_text(
                f"❌ <b>Kontrol sırasında hata oluştu!</b>\n\n"
                f"🔧 Hata: {esc_html(str(e)[:100])}\n"
                f"📊 Kontrol edilen: {checked_count}/{len(user_alerts)}"
            )

    async def on_cb(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        q = u.callback_query
        await q.answer()
        data = q.data or ""

        if data.startswith("del_"):
            alert_id = int(data.split("_")[1])
            ok = self.db.deactivate(alert_id, q.from_user.id)
            if ok:
                return await q.edit_message_text(
                    "✅ <b>Alarm silindi!</b>\n\n"
                    "💡 Yeni alarm eklemek için:\n"
                    "<code>/add ItemAdı FiyatEşiği</code>\n\n"
                    "Veya mesaj olarak: <code>ItemAdı | Fiyat</code>"
                )
            else:
                return await q.edit_message_text("❌ Alarm silinemedi veya bulunamadı.")

        elif data.startswith("check_"):
            alert_id = int(data.split("_")[1])
            for a in self.db.list_user(q.from_user.id):
                if a["id"] == alert_id:
                    await q.edit_message_text(
                        f"🔍 <b>{esc_html(a['item_name'])}</b> kontrol ediliyor...\n\n"
                        "⏳ Bu işlem 10-30 saniye sürebilir\n"
                        f"🌍 {self._server_text()} serverından fiyat çekiliyor..."
                    )

                    try:
                        res = await self.ttc.fetch_price(a["item_name"], headless=True)

                        if res.price is not None:
                            self.db.set_price(alert_id, res.price)

                        time_str = time.strftime("%H:%M", time.localtime())

                        if res.price:
                            price_line = f"💰 <b>{fmt_gold(res.price)}g</b> (birim fiyat)"
                            if res.price <= a["threshold_price"]:
                                price_line += "\n🔥 <b>HEDEF FİYATIN ALTINDA!</b>"
                        else:
                            price_line = "💰 <i>Fiyat alınamadı</i>"

                        result_text = (
                            f"📊 <b>{esc_html(a['item_name'])} - Anlık Kontrol</b>\n\n"
                            f"{price_line}\n"
                            f"🎯 <b>Hedef fiyat:</b> {fmt_gold(a['threshold_price'])}g\n"
                            f"🏪 <b>Satıcı:</b> {esc_html(res.guild or 'Bilinmiyor')}\n"
                            f"⏰ <b>Kontrol zamanı:</b> {time_str}\n"
                            f"🌍 <b>Server:</b> {self._server_text()}"
                        )

                        kb = InlineKeyboardMarkup([
                            [InlineKeyboardButton("🔗 TTC'de Görüntüle", url=res.link)],
                            [InlineKeyboardButton("🔄 Tekrar Kontrol", callback_data=f"check_{alert_id}")]
                        ])

                        return await q.edit_message_text(result_text, reply_markup=kb)

                    except Exception as e:
                        return await q.edit_message_text(
                            f"❌ <b>Kontrol hatası!</b>\n\n"
                            f"🎯 <b>Item:</b> {esc_html(a['item_name'])}\n"
                            f"🔧 <b>Sorun:</b> {esc_html(str(e)[:50])}...\n\n"
                            "💡 Tekrar dene veya /test komutu kullan"
                        )

        elif data.startswith("test_"):
            item = data.split("test_", 1)[1]
            await q.edit_message_text(f"🔍 {esc_html(item)} test ediliyor...")
            await self.cmd_test_callback(q, item)

        elif data.startswith("add_from_test_"):
            item = data.split("add_from_test_", 1)[1]
            await q.edit_message_text(
                f"➕ <b>{esc_html(item)} için alarm ekleme</b>\n\n"
                "💡 Şu komutu kullan:\n"
                f"<code>/add {esc_html(item)} HEDEF_FİYAT</code>\n\n"
                "<b>Örnek:</b>\n"
                f"<code>/add {esc_html(item)} 50000</code>\n\n"
                "🎯 Bot bu fiyat veya altında bulduğunda sana haber verecek!"
            )

        elif data == "list_alerts":
            await self.cmd_list(Update(update_id=0, message=q.message), c)

        elif data == "popular_items":
            await q.edit_message_text(
                "🔥 <b>Popüler ESO Itemleri</b>\n\n"
                "💎 <b>Upgrade Materials:</b>\n"
                "• Dreugh Wax (30.000-60.000g)\n"
                "• Tempering Alloy (15.000-30.000g)\n"
                "• Kuta (7.000-12.000g)\n"
                "• Rosin (20.000-40.000g)\n\n"
                "🧪 <b>Alchemy:</b>\n"
                "• Cornflower (800-1.500g)\n"
                "• Columbine (600-1.200g)\n"
                "• Perfect Roe (100.000-200.000g)\n\n"
                "⚔️ <b>Other:</b>\n"
                "• Aetherial Dust (80.000-150.000g)\n"
                "• Dragon Rheum (3.000-8.000g)\n\n"
                "💡 Parantez içindeki fiyatlar ortalama aralık"
            )

        elif data == "how_to_use":
            await q.edit_message_text(
                "📚 <b>Nasıl Kullanılır?</b>\n\n"
                "1️⃣ <b>Alarm Ekle:</b>\n"
                "<code>/add Dreugh Wax 45000</code>\n"
                "Veya mesaj olarak: <code>Dreugh Wax | 45000</code>\n\n"
                "2️⃣ <b>Bot Otomatik Çalışır:</b>\n"
                "• Her 5 dakikada kontrol eder\n"
                f"• {self._server_text()} serverından veri çeker\n"
                "• Birim fiyatları takip eder\n\n"
                "3️⃣ <b>Bildirim Alırsın:</b>\n"
                "• Fiyat hedefin altına düştüğünde\n"
                "• Hangi satıcıdan, nerede\n"
                "• Direkt TTC linkiyle\n\n"
                "4️⃣ <b>Yönetim:</b>\n"
                "• <code>/list</code> - Alarmlarını gör\n"
                "• <code>/test ItemAdı</code> - Anlık kontrol\n\n"
                "🎯 Maksimum 15 alarm ekleyebilirsin!"
            )

        elif data == "tips":
            await q.edit_message_text(
                "💡 <b>İpuçları ve Tavsiyeler</b>\n\n"
                "🎯 <b>Fiyat Belirleme:</b>\n"
                "• TTC'de ortalama fiyatı kontrol et\n"
                "• %10-20 altında hedef belirle\n"
                "• Çok düşük hedef koyma (bulunmaz)\n\n"
                "📊 <b>Alarm Yönetimi:</b>\n"
                "• En çok 10-12 alarm kullan\n"
                "• Gereksizleri sil (/list)\n"
                "• Popüler itemleri takip et\n\n"
                "⚡ <b>Hızlı Kullanım:</b>\n"
                "• Mesaj olarak gönder: <code>Kuta | 8000</code>\n"
                "• /test ile anlık kontrol yap\n"
                "• TTC linkine tıklayıp satın al\n\n"
                "🔔 <b>Bildirimler:</b>\n"
                "• Hemen satın al, çabuk tükenir\n"
                "• Aynı item 10dk sonra tekrar kontrol edilir\n\n"
                "❓ Sorun mu var? /help komutu kullan!"
            )

        elif data == "add_more":
            await q.edit_message_text(
                "➕ <b>Yeni Alarm Ekle</b>\n\n"
                "Şu yöntemlerden birini kullan:\n\n"
                "🔸 <b>Komut ile:</b>\n"
                "<code>/add ItemAdı HedefFiyat</code>\n"
                "<i>Örnek: /add Kuta 8000</i>\n\n"
                "🔸 <b>Mesaj ile:</b>\n"
                "<code>ItemAdı | HedefFiyat</code>\n"
                "<i>Örnek: Kuta | 8000</i>\n\n"
                "💡 Item adını TTC'deki gibi İngilizce yaz\n"
                "🎯 Fiyatı gold cinsinden yaz (8000, 50000...)"
            )

        elif data == "help_add":
            await q.edit_message_text(
                "➕ <b>Alarm Ekleme Rehberi</b>\n\n"
                "📝 <b>Doğru Format:</b>\n"
                "• <code>/add Dreugh Wax 50000</code>\n"
                "• <code>/add Kuta 8000</code>\n"
                "• Mesaj: <code>Perfect Roe | 150000</code>\n\n"
                "✅ <b>Kurallar:</b>\n"
                "• Item adı İngilizce olmalı\n"
                "• Fiyat sadece rakam (50000)\n"
                "• Nokta/virgül kullanma\n"
                "• Maksimum 15 alarm\n\n"
                "🎯 <b>İpucu:</b>\n"
                "TTC sitesinde item adını kontrol et,\n"
                "aynı ismi kullan.\n\n"
                "❓ Hala sorun mu var? /help yazın!"
            )

    async def cmd_test_callback(self, query, item: str):
        try:
            res = await self.ttc.fetch_price(item, headless=True)

            if res.price:
                price_line = f"💰 <b>{fmt_gold(res.price)}g</b>"
            else:
                price_line = "💰 <i>Fiyat bulunamadı</i>"

            time_str = time.strftime("%H:%M", time.localtime())

            text = (
                f"✅ <b>Test Sonucu</b>\n\n"
                f"🎯 <b>Item:</b> {esc_html(item)}\n"
                f"{price_line}\n"
                f"🏪 <b>Satıcı:</b> {esc_html(res.guild or 'Bilinmiyor')}\n"
                f"⏰ <b>Kontrol:</b> {time_str}\n"
                f"📡 <b>Kaynak:</b> {res.source}"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔗 TTC'de Görüntüle", url=res.link)]
            ])

            await query.edit_message_text(text, reply_markup=kb)

        except Exception as e:
            await query.edit_message_text(
                f"❌ <b>Test hatası!</b>\n\n"
                f"🎯 <b>Item:</b> {esc_html(item)}\n"
                f"🔧 <b>Hata:</b> {esc_html(str(e)[:50])}..."
            )

    async def job_check_prices(self, c: ContextTypes.DEFAULT_TYPE):
        arr = self.db.all_active()
        log.info("JOB: %d aktif alarm kontrol ediliyor...", len(arr))

        for a in arr:
            last = a.get("last_check", 0) or 0
            if time.time() - last < self.COOLDOWN:
                log.info("⸻ Skip (cooldown): %s", a["item_name"])
                continue

            try:
                await asyncio.sleep(random.uniform(1, 5))
                res = await self.ttc.fetch_price(a["item_name"], headless=True)

                if res.source == "captcha":
                    msg = (
                        f"⚠️ <b>{esc_html(a['item_name'])}</b> için captcha çıktı!\n\n"
                        f"Lütfen <code>/test {esc_html(a['item_name'])}</code> komutu ile tarayıcıda aç ve çöz.\n"
                        "Captcha çözülünce otomatik kontroller tekrar devam edecek."
                    )
                    await c.bot.send_message(
                        chat_id=a["user_id"],
                        text=msg,
                        parse_mode=ParseMode.HTML
                    )
                    log.warning("JOB: captcha tespit edildi (item=%s). manuel /test ile storage_state güncelle.", a["item_name"])
                    continue

                if res.price is not None:
                    self.db.set_price(a["id"], res.price)

                if res.price is not None and res.price <= a["threshold_price"]:
                    last_notified_price = a.get("last_notified_price")
                    should_notify = (last_notified_price is None) or (res.price < last_notified_price)
                    if should_notify:
                        text = (
                            "🔥 <b>SÜPER FIRSAT!</b>\n\n"
                            f"🎯 <b>Item:</b> {esc_html(a['item_name'])}\n"
                            f"💰 <b>Fiyat:</b> {fmt_gold(res.price)}g\n"
                            f"🎯 <b>Eşiğin:</b> {fmt_gold(a['threshold_price'])}g\n"
                            f"🏪 <b>Satıcı:</b> {esc_html(res.guild or 'Bilinmiyor')}\n"
                            f"📍 <b>Lokasyon:</b> {esc_html(res.location or 'Bilinmiyor')}\n\n"
                            f"⚡ Hemen satın almak için TTC'ye git!\n"
                            f"🔗 <a href='{res.link}'>TTC Listing</a>\n"
                            f"🌍 <b>Server:</b> {self._server_text()}"
                        )

                        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 TTC'de Satın Al", url=res.link)]])
                        await c.bot.send_message(
                            chat_id=a["user_id"],
                            text=text,
                            parse_mode=ParseMode.HTML,
                            reply_markup=kb,
                            disable_web_page_preview=False,
                        )
                        self.db.set_notified(a["id"], res.price)
                    else:
                        log.info(
                            "Skip notify (eşikten düşük ama yeni değil): item=%s price=%d last_notified=%s",
                            a["item_name"], res.price, last_notified_price
                        )
            except Exception as e:
                log.warning("job item hata (%s): %s", a["item_name"], e)

    async def on_message(self, u: Update, c: ContextTypes.DEFAULT_TYPE):
        if not u.message or not u.message.text:
            return
        txt = u.message.text.strip()

        m = re.match(r"^(.*?)\s*\|\s*([0-9\.\,]+)$", txt)
        if not m:
            return

        item = m.group(1).strip()
        price_str = m.group(2).strip()

        if len(item) < 2:
            return await u.message.reply_html("❌ Item adı çok kısa!")

        try:
            thr = int(price_str.replace(".", "").replace(",", ""))
            if thr <= 0:
                return await u.message.reply_html("❌ Fiyat 0'dan büyük olmalı!")
        except Exception:
            return await u.message.reply_html("❌ Fiyat formatı hatalı! Örnek: Dreugh Wax | 50000")

        existing_alerts = self.db.list_user(u.effective_user.id)
        if len(existing_alerts) >= 15:
            return await u.message.reply_html(
                "⚠️ Maksimum 15 alarm ekleyebilirsin!\n"
                "Önce bazı alarmları sil: <code>/list</code>"
            )

        for alert in existing_alerts:
            if alert['item_name'].lower() == item.lower():
                return await u.message.reply_html(
                    f"⚠️ <b>{esc_html(item)}</b> için zaten alarm var!\n"
                    f"Mevcut eşik: <b>{fmt_gold(alert['threshold_price'])}g</b>"
                )

        self.db.add(u.effective_user.id, u.effective_user.username or "", item, thr)

        success_text = (
            "✅ <b>Hızlı alarm eklendi!</b>\n\n"
            f"🎯 <b>Item:</b> {esc_html(item)}\n"
            f"💰 <b>Eşik:</b> {fmt_gold(thr)}g ve altı\n"
            f"🌍 <b>Server:</b> {self._server_text()}\n\n"
            "💡 <b>İpucu:</b> Diğer komutlar için <code>/help</code>"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(f"🧪 {item} Test Et", callback_data=f"test_{item}")],
            [InlineKeyboardButton("📋 Tüm Alarmlarım", callback_data="list_alerts")]
        ])

        await u.message.reply_html(success_text, reply_markup=kb)

    def run(self):
        request = HTTPXRequest(
            connect_timeout=30.0,
            read_timeout=30.0,
            write_timeout=30.0,
            pool_timeout=30.0,
        )

        app = Application.builder().token(BOT_TOKEN).request(request).build()

        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("help", self.cmd_help))
        app.add_handler(CommandHandler("add", self.cmd_add))
        app.add_handler(CommandHandler("list", self.cmd_list))
        app.add_handler(CommandHandler("test", self.cmd_test))
        app.add_handler(CommandHandler("checknow", self.cmd_checknow))
        app.add_handler(CallbackQueryHandler(self.on_cb))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_message))

        app.job_queue.run_repeating(self.job_check_prices, interval=CHECK_INTERVAL, first=30)

        log.info("🤖 Bot başlatılıyor...")
        app.run_polling(close_loop=False)

if __name__ == "__main__":
    Bot().run()
