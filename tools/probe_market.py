"""Market erisim testi (genel).

Birden fazla zinciri ayni olcutlerle test eder ve hangisinin
kazimaya uygun oldugunu soyler:

  - robots.txt ne diyor?
  - istek 200 mu donuyor, 403 mu?
  - bot korumasi (Cloudflare vb.) var mi?
  - urunler ilk HTML'de mi geliyor (sunucu render), yoksa JS ile mi?

Kullanim:
    python tools/probe_market.py                       # varsayilan liste
    python tools/probe_market.py https://ornek.com/x   # tek adres
"""
from __future__ import annotations

import re
import sys
import time
import urllib.robotparser
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.config import USER_AGENT

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

CANDIDATES: list[tuple[str, str]] = [
    ("BİM (referans)", "https://www.bim.com.tr/categories/100/aktuel-urunler.aspx"),
    ("ŞOK", "https://www.sokmarket.com.tr/"),
    ("ŞOK kampanya", "https://www.sokmarket.com.tr/kampanyalar"),
    ("Migros", "https://www.migros.com.tr/"),
    ("Migros kampanya", "https://www.migros.com.tr/kampanyalar"),
    ("ŞOK aktüel", "https://www.sokmarket.com.tr/aktuel-urunler"),
    ("CarrefourSA", "https://www.carrefoursa.com/"),
]

PRICE_RE = re.compile(r"\d[\d.,\s]{0,10}(?:₺|TL\b)", re.I)
JSON_PRICE_RE = re.compile(r'"(?:price|salePrice|listPrice|currentPrice)"\s*:', re.I)

# DIKKAT: "captcha" kelimesinin sayfada gecmesi engel demek DEGIL.
# Giris formundaki reCAPTCHA script'i de bu kelimeyi icerir.
# Gercek engel: 403/429/503 durumu ya da Cloudflare'in challenge basligi.
BLOCK_TITLES = ("just a moment", "attention required", "access denied",
                "checking your browser")
BLOCK_STATUSES = {401, 403, 429, 503}


def robots_verdict(url: str) -> str:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = requests.get(robots_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return f"robots.txt HTTP {resp.status_code}"
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(resp.text.splitlines())
        allowed = rp.can_fetch(USER_AGENT, url)
        delay = rp.crawl_delay(USER_AGENT)
        verdict = "IZIN VAR" if allowed else "YASAK"
        return f"{verdict}{f' (crawl-delay {delay}s)' if delay else ''}"
    except Exception as exc:
        return f"okunamadi ({type(exc).__name__})"


def probe(label: str, url: str) -> None:
    print(f"\n{'-' * 74}\n{label}\n  {url}")
    print(f"  robots.txt : {robots_verdict(url)}")

    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
    except Exception as exc:
        print(f"  istek      : BASARISIZ ({exc})")
        return

    html = resp.text
    lowered = html.lower()
    server = resp.headers.get("server", "-")
    mitigated = resp.headers.get("cf-mitigated")

    print(f"  HTTP       : {resp.status_code} | {len(html):,} karakter | server: {server}")

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = " ".join(m.group(1).split())[:70]

    reasons = []
    if resp.status_code in BLOCK_STATUSES:
        reasons.append(f"HTTP {resp.status_code}")
    if mitigated:
        reasons.append(f"cf-mitigated: {mitigated}")
    if any(b in title.lower() for b in BLOCK_TITLES):
        reasons.append(f"baslik: {title}")
    blocked = bool(reasons)

    print(f"  sayfa basligi: {title or '-'}")
    print(f"  bot koruma : {'VAR -> ' + ', '.join(reasons) if blocked else 'yok'}")

    # Fiyati HAM HTML'de degil, etiketler temizlendikten sonra ariyoruz.
    # BIM fiyati uc parcaya boluyor ('79,' + '00' + '₺'); ham metinde
    # bitisik degil, bu yuzden ham arama yanlis negatif veriyor.
    try:
        from bs4 import BeautifulSoup
        text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    except Exception:
        text = html
    prices = PRICE_RE.findall(text)
    json_prices = JSON_PRICE_RE.findall(html)

    print(f"  metinde fiyat  : {len(prices)} adet"
          f"{' | ornek: ' + ', '.join(p.strip() for p in prices[:4]) if prices else ''}")
    print(f"  JSON fiyat alani: {len(json_prices)} adet")

    signal = max(len(prices), len(json_prices))
    if blocked:
        print("  >> SONUC   : ERISIM ENGELLI")
    elif signal >= 10:
        kaynak = "HTML metni" if len(prices) >= len(json_prices) else "gomulu JSON"
        print(f"  >> SONUC   : UYGUN ({kaynak}, requests yeterli)")
    elif resp.status_code == 200:
        print("  >> SONUC   : sayfa aciliyor ama fiyat yok -> JS ile geliyor")
    else:
        print(f"  >> SONUC   : HTTP {resp.status_code}")

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"\W+", "_", urlparse(url).netloc + urlparse(url).path).strip("_")
    Path(f"data/raw/probe_{slug[:60]}.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    targets = ([("elle verilen", u) for u in sys.argv[1:]] or CANDIDATES)
    print("=" * 74)
    print("MARKET ERISIM TESTI")
    print("=" * 74)
    for label, url in targets:
        probe(label, url)
        time.sleep(2)
    print(f"\n{'=' * 74}")
    print("UYGUN cikan zincirlerle devam ediyoruz.")
    print("BİM referans olarak listede: onun sonucu UYGUN cikmali,")
    print("cikmiyorsa sorun senin agindadir, sitelerde degil.")
