"""ŞOK site haritasi kesfi.

Ana sayfadaki ic linkleri toplayip hangilerinin urun listeleme sayfasi
oldugunu bulur. Adres tahmin etmek yerine siteden ogreniyoruz.

Kullanim:
    python tools/discover_sok.py
    python tools/discover_sok.py --test 6      # en iyi 6 adayi test et
"""
from __future__ import annotations

import re
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup

from src.config import USER_AGENT

BASE = "https://www.sokmarket.com.tr"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

PRICE_RE = re.compile(r"\d[\d.,\s]{0,10}(?:₺|TL\b)", re.I)

# Temel gida ile ilgili gorunen yollar one ciksin
FOOD_HINTS = ("gida", "sut", "kahvalti", "et-", "tavuk", "meyve", "sebze",
              "icecek", "atistir", "bakliyat", "temel", "market", "indirim",
              "firsat", "aktuel", "kampanya", "urun")
SKIP_HINTS = ("giris", "uyelik", "sepet", "hesab", "sozlesme", "iletisim",
              "kariyer", "yardim", "kvkk", "gizlilik", "cerez", "app",
              "facebook", "instagram", "twitter", "youtube", "apple", "google")


def internal_links(html: str, base: str) -> Counter:
    soup = BeautifulSoup(html, "lxml")
    paths: Counter = Counter()
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        parsed = urlparse(url)
        if parsed.netloc and "sokmarket.com.tr" not in parsed.netloc:
            continue
        path = parsed.path.rstrip("/")
        if not path or path == "/":
            continue
        if any(s in path.lower() for s in SKIP_HINTS):
            continue
        paths[path] += 1
    return paths


def score(path: str) -> int:
    low = path.lower()
    s = sum(3 for h in FOOD_HINTS if h in low)
    # ".../c-1234" gibi kategori kodu tasiyan yollar iyi adaydir
    if re.search(r"-c-?\d+$", low) or re.search(r"/c/\d+", low):
        s += 5
    s -= low.count("/")          # sig yollari tercih et
    return s


def fetch(url: str) -> tuple[int, str]:
    resp = requests.get(url, headers=HEADERS, timeout=25)
    return resp.status_code, resp.text


def test_page(path: str) -> None:
    url = BASE + path
    try:
        status, html = fetch(url)
    except Exception as exc:
        print(f"  {path[:58]:<58} HATA {type(exc).__name__}")
        return
    text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
    prices = PRICE_RE.findall(text)
    verdict = ("URUN LISTESI" if status == 200 and len(prices) >= 15
               else "az fiyat" if status == 200 else f"HTTP {status}")
    ornek = ", ".join(p.strip() for p in prices[:3])
    print(f"  {path[:58]:<58} {status} | {len(prices):>3} fiyat | {verdict}"
          + (f" | {ornek}" if ornek else ""))


def main() -> None:
    print("=" * 78)
    print("ŞOK ana sayfasindan ic linkler toplaniyor")
    print("=" * 78)
    status, html = fetch(BASE + "/")
    print(f"  ana sayfa: HTTP {status} | {len(html):,} karakter")

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    Path("data/raw/sok_home.html").write_text(html, encoding="utf-8")

    paths = internal_links(html, BASE)
    print(f"  {len(paths)} farkli ic yol bulundu\n")

    ranked = sorted(paths, key=lambda p: (-score(p), p))
    print("  --- en umit verici 25 yol ---")
    for p in ranked[:25]:
        print(f"    [{score(p):>3}] {p}")

    n = 5
    if "--test" in sys.argv:
        try:
            n = int(sys.argv[sys.argv.index("--test") + 1])
        except (IndexError, ValueError):
            n = 5

    print(f"\n  --- ilk {n} aday test ediliyor ---")
    for p in ranked[:n]:
        test_page(p)
        time.sleep(1.5)

    print("\n" + "-" * 78)
    print("'URUN LISTESI' cikan yolu bana gonder; parser'i ona gore yazalim.")


if __name__ == "__main__":
    main()
