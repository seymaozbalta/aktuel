"""ŞOK kart yapisi ve sayfalama incelemesi.

Uc sey olcer:
  1) Urun karti HTML'i ve sinif adlari (parser bunlara gore yazilacak)
  2) Sayfa icine gomulu JSON var mi (varsa HTML ayristirmaktan iyidir)
  3) Sayfalama nasil calisiyor (?page=2 gercekten yeni urun getiriyor mu)

Kullanim:
    python tools/inspect_sok.py
    python tools/inspect_sok.py /kahvaltilik-c-890
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup, Tag

from src.config import USER_AGENT

BASE = "https://www.sokmarket.com.tr"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "tr-TR,tr;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

PRODUCT_HREF_RE = re.compile(r"-p-\d+/?$")
PRICE_RE = re.compile(r"\d[\d.,\s]{0,10}(?:₺|TL\b)", re.I)
PAGE_PARAM_CANDIDATES = ("page", "p", "sayfa", "pg")


def fetch(url: str) -> tuple[int, str]:
    r = requests.get(url, headers=HEADERS, timeout=25)
    return r.status_code, r.text


def product_hrefs(soup: BeautifulSoup) -> list[str]:
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if PRODUCT_HREF_RE.search(href) and href not in seen:
            seen.add(href)
            out.append(href)
    return out


def find_card(anchor: Tag) -> Tag:
    """BIM'deki mantigin aynisi: fiyati iceren, tek urunu kapsayan en kucuk kap."""
    node, fallback = anchor, anchor
    for _ in range(10):
        node = node.parent
        if node is None or not isinstance(node, Tag):
            break
        hrefs = {a["href"].split("?")[0] for a in node.find_all("a", href=True)
                 if PRODUCT_HREF_RE.search(a["href"].split("?")[0])}
        if len(hrefs) > 1:
            break
        fallback = node
        if PRICE_RE.search(node.get_text(" ", strip=True)):
            return node
    return fallback


def section(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def inspect_embedded_json(html: str, soup: BeautifulSoup) -> None:
    section("1) GOMULU JSON")
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        print("  __NEXT_DATA__ VAR")
        try:
            data = json.loads(tag.string)
            blob = json.dumps(data, ensure_ascii=False)
            print(f"  boyut: {len(blob):,} karakter")
            for key in ("price", "salePrice", "discountedPrice", "productName", "name"):
                print(f"    '{key}' gecis: {blob.count(chr(34) + key + chr(34))}")
            Path("data/raw/sok_next_data.json").write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print("  kaydedildi: data/raw/sok_next_data.json  (acip bakabilirsin)")
        except json.JSONDecodeError as exc:
            print(f"  JSON okunamadi: {exc}")
    else:
        print("  __NEXT_DATA__ yok")

    n_chunks = html.count("self.__next_f.push")
    print(f"  self.__next_f.push parcasi: {n_chunks}")
    ld = soup.find_all("script", type="application/ld+json")
    print(f"  JSON-LD blogu: {len(ld)}")
    for s in ld[:3]:
        try:
            d = json.loads(s.string or "")
            t = d.get("@type") if isinstance(d, dict) else type(d).__name__
            n = len(d.get("itemListElement", [])) if isinstance(d, dict) else "-"
            print(f"    @type={t}  itemListElement={n}")
        except Exception:
            pass


def inspect_card(soup: BeautifulSoup) -> None:
    section("2) URUN KARTI")
    hrefs = product_hrefs(soup)
    print(f"  urun linki: {len(hrefs)}")
    if not hrefs:
        print("  urun linki bulunamadi!")
        return
    for h in hrefs[:3]:
        print(f"    {h}")

    anchor = soup.find("a", href=re.compile(re.escape(hrefs[0])))
    card = find_card(anchor)

    print("\n  --- ilk kartin HTML'i (ilk 3000 karakter) ---")
    print(card.prettify()[:3000])

    print("\n  --- kart icindeki sinif adlari ---")
    for el in card.find_all(True, class_=True):
        text = el.get_text(" ", strip=True)[:55]
        cls = " ".join(el.get("class", []))[:60]
        print(f"    <{el.name}> .{cls:<60} :: {text!r}")


def inspect_pagination(path: str, soup: BeautifulSoup, first_hrefs: list[str]) -> None:
    section("3) SAYFALAMA")

    # a) HTML icindeki sayfa linkleri
    page_links = set()
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if re.search(r"[?&](page|p|sayfa|pg)=\d+", h):
            page_links.add(h)
    nxt = soup.find("a", rel="next") or soup.find("link", rel="next")
    print(f"  HTML'de sayfa parametreli link: {len(page_links)}")
    for h in sorted(page_links)[:6]:
        print(f"    {h}")
    print(f"  rel=next: {nxt.get('href') if nxt else 'yok'}")

    # b) Toplam urun sayisi ipucu ("245 urun" gibi)
    text = soup.get_text(" ", strip=True)
    m = re.search(r"(\d[\d.]*)\s*(?:ürün|urun|sonuç)", text, re.I)
    print(f"  toplam urun ipucu: {m.group(0) if m else 'bulunamadi'}")

    # c) Parametreleri gercekten dene
    print("\n  --- 2. sayfa denemesi ---")
    first = set(first_hrefs)
    for param in PAGE_PARAM_CANDIDATES:
        url = f"{BASE}{path}?{param}=2"
        try:
            status, html = fetch(url)
        except Exception as exc:
            print(f"    ?{param}=2  HATA {exc}")
            continue
        hrefs = product_hrefs(BeautifulSoup(html, "lxml"))
        new = [h for h in hrefs if h not in first]
        verdict = "CALISIYOR" if len(new) >= 5 else "etkisiz"
        print(f"    ?{param}=2  HTTP {status} | {len(hrefs):>3} urun | "
              f"{len(new):>3} yeni | {verdict}")
        time.sleep(1.5)
        if verdict == "CALISIYOR":
            break


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else "/sut-ve-sut-urunleri-c-460"
    url = urljoin(BASE, path)
    print(f"Hedef: {url}")
    status, html = fetch(url)
    print(f"HTTP {status} | {len(html):,} karakter")

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    Path("data/raw/sok_category.html").write_text(html, encoding="utf-8")

    soup = BeautifulSoup(html, "lxml")
    inspect_embedded_json(html, soup)
    inspect_card(soup)
    inspect_pagination(path, soup, product_hrefs(soup))

    print(f"\n{'-' * 78}\nCiktinin tamamini gonder; ozellikle 2. ve 3. bolum.")


if __name__ == "__main__":
    main()
