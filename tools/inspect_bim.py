"""Selektor dogrulama araci.

BIM sayfasinin yapisini ekrana dokerek parser'in dogru yeri okuyup
okumadigini birkac dakikada kontrol etmeni saglar.

    python tools/inspect_bim.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup

from src.config import BIM_AKTUEL_PATH, BIM_BASE_URL, USER_AGENT
from src.scrapers.bim import PRODUCT_HREF_RE, BimScraper

url = BIM_BASE_URL + BIM_AKTUEL_PATH
html = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20).text
Path("data/raw").mkdir(parents=True, exist_ok=True)
Path("data/raw/inspect.html").write_text(html, encoding="utf-8")
print(f"Ham HTML kaydedildi: data/raw/inspect.html ({len(html):,} karakter)\n")

soup = BeautifulSoup(html, "lxml")

anchors = soup.find_all("a", href=PRODUCT_HREF_RE)
print(f"Urun linki sayisi : {len(anchors)}")
print(f"Katalog sekmeleri : {BimScraper.discover_catalogs(soup)}\n")

if anchors:
    card = BimScraper._find_card(anchors[0])
    print("--- ILK KARTIN HTML'I (ilk 2500 karakter) ---")
    print(card.prettify()[:2500])
    print("\n--- KART ICINDEKI SINIF ADLARI ---")
    for el in card.find_all(True, class_=True):
        text = el.get_text(" ", strip=True)[:60]
        print(f"  <{el.name}> class={el.get('class')} :: {text!r}")
else:
    print("HIC URUN LINKI BULUNAMADI.")
    print("Muhtemel sebep: sayfa yapisi degismis veya istek engellenmis.")
    print("data/raw/inspect.html dosyasini tarayicida acip kontrol et.")
