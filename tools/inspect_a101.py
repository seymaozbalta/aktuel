"""A101 sayfa yapisi kesif araci.

A101 Next.js ile yazilmis: ilk HTML'de urun yok, JavaScript calistiktan
sonra geliyor. Bu arac veriyi HANGI yoldan alabilecegimizi olcer:

  1) Sayfa icine gomulu JSON  (__NEXT_DATA__ / self.__next_f.push)
  2) Ayri bir JSON API adresi
  3) Hicbiri -> Selenium gerekir

Kullanim:
    python tools/inspect_a101.py
    python tools/inspect_a101.py https://www.a101.com.tr/baska-bir-sayfa
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup

from src.config import USER_AGENT

DEFAULT_URLS = [
    "https://www.a101.com.tr/afisler-haftanin-yildizlari",
    "https://www.a101.com.tr/afisler",
]

# Fiyat tasidigini dusundugumuz alan adlari
PRICE_KEYS = {"price", "salePrice", "listPrice", "currentPrice", "discountedPrice",
              "fiyat", "amount", "value", "unitPrice", "campaignPrice"}
NAME_KEYS = {"name", "title", "productName", "urunAdi", "displayName"}

API_HINT_RE = re.compile(r"https?://[\w.-]*a101[\w.-]*/[\w/\-.]*(?:api|graphql)[\w/\-.?=&]*", re.I)
NEXT_F_RE = re.compile(r"self\.__next_f\.push\(\s*(\[.*?\])\s*\)", re.S)


def walk(node, path="", depth=0, found=None):
    """Ic ice JSON icinde fiyat+isim tasiyan nesneleri arar."""
    found = found if found is not None else []
    if depth > 14:
        return found
    if isinstance(node, dict):
        keys = set(node)
        if keys & PRICE_KEYS and keys & NAME_KEYS:
            found.append((path, node))
        for k, v in node.items():
            walk(v, f"{path}.{k}", depth + 1, found)
    elif isinstance(node, list):
        for i, v in enumerate(node[:60]):
            walk(v, f"{path}[{i}]", depth + 1, found)
    return found


def report_candidates(label: str, data) -> bool:
    hits = walk(data)
    if not hits:
        print(f"    {label}: fiyat+isim tasiyan nesne bulunamadi")
        return False
    print(f"    {label}: {len(hits)} aday nesne bulundu")
    path, sample = hits[0]
    print(f"    ornek yol : {path[:110]}")
    preview = {k: v for k, v in list(sample.items())[:10]
               if not isinstance(v, (dict, list))}
    print(f"    ornek veri: {json.dumps(preview, ensure_ascii=False)[:400]}")
    return True


def inspect(url: str) -> None:
    print(f"\n{'=' * 78}\n{url}\n{'=' * 78}")
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "tr-TR,tr;q=0.9"},
            timeout=25,
        )
    except Exception as exc:
        print(f"  ISTEK BASARISIZ: {exc}")
        return

    html = resp.text
    print(f"  HTTP {resp.status_code} | {len(html):,} karakter")

    Path("data/raw").mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"\W+", "_", url.split("a101.com.tr")[-1]).strip("_") or "index"
    out = Path("data/raw") / f"a101_{slug}.html"
    out.write_text(html, encoding="utf-8")
    print(f"  kaydedildi: {out}")

    soup = BeautifulSoup(html, "lxml")
    found_any = False

    # --- 1) __NEXT_DATA__ (Pages Router) ---------------------------------
    tag = soup.find("script", id="__NEXT_DATA__")
    if tag and tag.string:
        print("\n  [1] __NEXT_DATA__ BULUNDU")
        try:
            data = json.loads(tag.string)
            print(f"    ust seviye anahtarlar: {list(data)[:10]}")
            found_any |= report_candidates("icerik", data)
        except json.JSONDecodeError as exc:
            print(f"    JSON okunamadi: {exc}")
    else:
        print("\n  [1] __NEXT_DATA__ yok (App Router kullaniliyor olabilir)")

    # --- 2) self.__next_f.push parcalari (App Router / RSC) --------------
    chunks = NEXT_F_RE.findall(html)
    if chunks:
        print(f"\n  [2] self.__next_f.push: {len(chunks)} parca")
        blob = ""
        for c in chunks:
            try:
                parsed = json.loads(c)
            except json.JSONDecodeError:
                continue
            for part in parsed:
                if isinstance(part, str):
                    blob += part
        print(f"    birlestirilen metin: {len(blob):,} karakter")
        # RSC akisinda JSON nesneleri duz metne gomulu gelir; parantez
        # sayarak tek tek ayikliyoruz
        objs = []
        for m in re.finditer(r'\{"[^"]{1,40}":', blob):
            start = m.start()
            depth, in_str, esc = 0, False, False
            for i in range(start, min(start + 200_000, len(blob))):
                ch = blob[i]
                if esc:
                    esc = False
                    continue
                if ch == "\\":
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                elif not in_str:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            try:
                                objs.append(json.loads(blob[start:i + 1]))
                            except json.JSONDecodeError:
                                pass
                            break
            if len(objs) > 400:
                break
        print(f"    ayiklanabilen JSON nesnesi: {len(objs)}")
        if objs:
            found_any |= report_candidates("RSC akisi", objs)
    else:
        print("\n  [2] self.__next_f.push parcasi yok")

    # --- 3) JSON-LD -------------------------------------------------------
    ld = [s for s in soup.find_all("script", type="application/ld+json") if s.string]
    if ld:
        print(f"\n  [3] JSON-LD: {len(ld)} blok")
        for s in ld[:3]:
            try:
                d = json.loads(s.string)
            except json.JSONDecodeError:
                continue
            t = d.get("@type") if isinstance(d, dict) else None
            print(f"    @type={t}")
            found_any |= report_candidates("JSON-LD", d)
    else:
        print("\n  [3] JSON-LD yok")

    # --- 4) HTML icinde gecen API adresleri -------------------------------
    apis = sorted(set(API_HINT_RE.findall(html)))[:15]
    if apis:
        print(f"\n  [4] HTML icinde gecen olasi API adresleri ({len(apis)}):")
        for a in apis:
            print(f"    {a[:130]}")
    else:
        print("\n  [4] HTML icinde API adresi gorunmuyor")

    # --- 5) Kaba gostergeler ---------------------------------------------
    print("\n  [5] Kaba gostergeler:")
    for needle in ("₺", "TL", "price", "urun", "product"):
        print(f"    '{needle}' gecis sayisi: {html.count(needle)}")

    print("\n  SONUC:", "Gomulu veriden okunabilir." if found_any
          else "Gomulu veri bulunamadi -> ag trafigine bakmak gerekiyor.")


if __name__ == "__main__":
    urls = sys.argv[1:] or DEFAULT_URLS
    for u in urls:
        inspect(u)
    print("\n" + "-" * 78)
    print("Sonuc 'gomulu veri bulunamadi' ise tarayicidan su adimi yap:")
    print("  F12 > Network > Fetch/XHR sekmesi > sayfayi yenile")
    print("  Listede urun donduren istegi bul (Response'ta fiyat gorunur)")
    print("  Uzerine sag tikla > Copy > Copy as cURL  ve bana gonder")
