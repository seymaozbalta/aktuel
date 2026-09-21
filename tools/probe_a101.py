"""A101 erisim testi.

Iki soruya cevap arar:
  1) robots.txt bu sayfalara erisime ne diyor?
  2) 403 basit bir baslik kontrolunden mi geliyor, yoksa gercek bir
     bot korumasindan mi (WAF)?

Kullanim:
    python tools/probe_a101.py
"""
from __future__ import annotations

import sys
import time
import urllib.robotparser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from src.config import USER_AGENT

TARGETS = [
    "https://www.a101.com.tr/afisler-haftanin-yildizlari",
    "https://www.a101.com.tr/afisler",
]
ROBOTS = "https://www.a101.com.tr/robots.txt"

CHROME_FULL = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Sec-CH-UA": '"Chromium";v="126", "Not:A-Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
    "Connection": "keep-alive",
}

VARIANTS: dict[str, dict[str, str]] = {
    "1) baslik yok": {},
    "2) sadece User-Agent": {"User-Agent": USER_AGENT},
    "3) tam tarayici basliklari": CHROME_FULL,
}

# WAF imzasi tasiyan yanit basliklari
WAF_HEADERS = ("cf-ray", "cf-mitigated", "server", "x-akamai-transformed",
               "x-sucuri-id", "x-cdn", "x-iinfo", "set-cookie")


def check_robots() -> None:
    print("=" * 74)
    print("ROBOTS.TXT")
    print("=" * 74)
    try:
        resp = requests.get(ROBOTS, headers=CHROME_FULL, timeout=20)
    except Exception as exc:
        print(f"  alinamadi: {exc}")
        return
    print(f"  HTTP {resp.status_code}")
    if resp.status_code != 200:
        print("  robots.txt okunamadi")
        return

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    Path("data/raw/a101_robots.txt").write_text(resp.text, encoding="utf-8")
    print("  kaydedildi: data/raw/a101_robots.txt")
    print("  --- icerik (ilk 40 satir) ---")
    for line in resp.text.splitlines()[:40]:
        print(f"  | {line}")

    rp = urllib.robotparser.RobotFileParser()
    rp.parse(resp.text.splitlines())
    print("\n  --- hedef sayfalar icin karar ---")
    for url in TARGETS:
        allowed = rp.can_fetch(USER_AGENT, url)
        print(f"  {'IZIN VAR ' if allowed else 'YASAK    '} {url}")
    delay = rp.crawl_delay(USER_AGENT)
    if delay:
        print(f"  Crawl-delay: {delay} saniye")


def probe() -> None:
    print("\n" + "=" * 74)
    print("ENGEL TURU")
    print("=" * 74)
    url = TARGETS[0]
    for label, headers in VARIANTS.items():
        try:
            resp = requests.get(url, headers=headers, timeout=20)
        except Exception as exc:
            print(f"  {label:<32} HATA: {exc}")
            continue

        size = len(resp.text)
        # Gercek sayfa geldiyse urun linki gorunur
        has_products = "afis" in resp.text and size > 50_000
        print(f"  {label:<32} HTTP {resp.status_code} | {size:>8,} karakter"
              f" | urun icerigi: {'VAR' if has_products else 'yok'}")
        time.sleep(2)

    print("\n  --- son yanitin dikkat ceken basliklari ---")
    try:
        resp = requests.get(url, headers=CHROME_FULL, timeout=20)
        for key in WAF_HEADERS:
            if key in resp.headers:
                value = resp.headers[key]
                print(f"    {key}: {value[:120]}")
        snippet = " ".join(resp.text.split())[:500]
        print(f"\n  --- engelleme sayfasindan ornek metin ---\n    {snippet}")
    except Exception as exc:
        print(f"    alinamadi: {exc}")


if __name__ == "__main__":
    check_robots()
    probe()
    print("\n" + "-" * 74)
    print("Yorum:")
    print("  robots.txt YASAK diyorsa  -> bu sayfayi kazimiyoruz, baska market")
    print("  3. varyant 200 donduyse   -> sorun sadece eksik baslikti, devam")
    print("  hepsi 403 ise             -> aktif bot korumasi var")
