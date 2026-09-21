"""Marketler arasi urun eslestirme.

Bir urun cifti, sirayla bes kapidan gecerse "ayni urun" sayilir:

  1) Cesitleri   "Barilla Makarna Cesitleri" bire bir eslesmeye girmez
  2) Gramaj      birimler ortaklastirilir (1 L = 1000 ml), miktar ayni olmali
  3) Marka       markalar uyusmuyorsa gerisine bakilmaz
  4) Sayi/cesit  "%1,5" != "%3", "laktozsuz" != sade
  5) Benzerlik   markasi cikarilmis isimler karsilastirilir

Sira bilincli: ucuz ve kesin kontroller once, bulanik olan en sonda.

Ilke: YANLIS ESLESME, KACIRILAN ESLESMEDEN DAHA KOTUDUR. Kacirilan eslesme
kullaniciya gosterilmez; yanlis eslesme ise yaniltici bir fiyat karsilastirmasi
uretir. Bu yuzden kurallar temkinli.

Kullanim:
    python -m src.matching --dry-run     # eslesmeleri goster, yazma
    python -m src.matching               # product_matches tablosuna yaz
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from itertools import combinations

from .scrapers.common import normalize_tr

log = logging.getLogger(__name__)

METHOD_VERSION = "v2"

# --------------------------------------------------------------------------
# Ayarlar
# --------------------------------------------------------------------------
UNIT_TO_BASE: dict[str, tuple[str, int]] = {
    "g": ("g", 1), "kg": ("g", 1000),
    "ml": ("ml", 1), "l": ("ml", 1000), "cl": ("ml", 10),
    "adet": ("adet", 1),
}

MULTI_MARKERS = frozenset({"cesitleri", "cesidi", "cesit"})

# Bir tarafta olup digerinde olmayan bu kelimeler FARKLI URUN demektir.
# Liste gercek veriden cikti: "Sek Protein Sut" / "Sek Protein Laktozsuz Sut"
# ve "Filiz Sebzeli" / "Filiz Tam Bugday" makarna yanlis eslesiyordu.
VARIANT_WORDS = frozenset({
    # tat
    "cikolatali", "kakaolu", "cilekli", "muzlu", "balli", "vanilyali",
    "findikli", "fistikli", "bademli", "karamelli", "portakalli", "limonlu",
    "visneli", "seftalili", "kayisili", "ananasli", "elmali", "karpuzlu",
    "naneli", "tarcinli", "meyveli", "kremali", "sutlu", "bitter",
    "acili", "aci", "baharatli", "kasarli", "peynirli", "sucuklu",
    "kiymali", "tavuklu", "etli",
    # icerik / diyet
    "laktozsuz", "sekersiz", "tuzsuz", "tuzlu", "glutensiz", "yagsiz",
    "light", "protein", "organik", "kepekli", "cavdarli", "sebzeli", "sade",
    # yag orani ("tam yagli", "yarim yagli", "az yagli", "tam bugday")
    "tam", "yarim", "az",
    # urun turu: "Suzme Peynir" != "Beyaz Peynir", "UHT Sut" != "Pastorize Sut"
    "suzme", "beyaz", "kasar", "tost", "dilimli", "uht", "pastorize",
})

MIN_BRAND_TOKEN = 3    # "s" (McVitie's'in s'si) gibi kirintilar marka sayilmaz
PREFIX_MIN = 5         # "mcvitie" ~ "mcvities" gibi on ek eslesmesi icin alt sinir
JACCARD_MIN = 0.55     # trigram benzerligi esigi
CONTAIN_MIN = 0.95     # kisa isim uzun ismin icinde neredeyse tamamen geciyorsa


# --------------------------------------------------------------------------
# Veri
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Product:
    id: int
    store: str
    name: str
    brand: str | None = None
    unit_amount: Decimal | float | None = None
    unit_type: str | None = None

    @property
    def base_unit(self) -> tuple[str, Decimal] | None:
        return to_base(self.unit_amount, self.unit_type)

    @property
    def label(self) -> str:
        return f"{self.brand} {self.name}" if self.brand else self.name


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str
    score: float = 0.0


@dataclass(frozen=True)
class Match:
    a: Product
    b: Product
    score: float
    method: str

    def row(self) -> dict:
        return {
            "product_id": self.a.id,
            "matched_product_id": self.b.id,
            "score": round(self.score, 3),
            "method": self.method,
            "status": "auto",
        }


# --------------------------------------------------------------------------
# Saf yardimcilar (test edilebilir)
# --------------------------------------------------------------------------
def to_base(amount, unit: str | None) -> tuple[str, Decimal] | None:
    """(1, 'l') -> ('ml', 1000) | (2.5, 'kg') -> ('g', 2500)"""
    if amount is None or not unit:
        return None
    conv = UNIT_TO_BASE.get(unit.lower())
    if conv is None:
        return None
    base, factor = conv
    try:
        value = Decimal(str(amount)).quantize(Decimal("0.01")) * factor
    except InvalidOperation:
        return None
    return base, value.normalize()


def tokens(text: str | None) -> list[str]:
    return normalize_tr(text or "").split()


def _same_word(a: str, b: str) -> bool:
    """Tam esitlik ya da uzun kelimelerde on ek ("mcvitie" ~ "mcvities")."""
    if a == b:
        return True
    return min(len(a), len(b)) >= PREFIX_MIN and (a.startswith(b) or b.startswith(a))


def brand_tokens(p: Product) -> list[str]:
    """Marka ayri verilmediyse (SOK, bazi BIM urunleri) ismin ilk kelimesi."""
    toks = tokens(p.brand) if p.brand else tokens(p.name)[:1]
    return [t for t in toks if len(t) >= MIN_BRAND_TOKEN]


def brands_match(a: Product, b: Product) -> bool:
    """ANA markalar uyusmali.

    BIM marka alanina bazen urun hattini da yaziyor: "Dankek Magma".
    Burada ana marka Dankek, Magma bir urun hatti. Her marka kelimesine esit
    davransaydik "Ulker Magma" da "magma" uzerinden gecerdi.

    Kural: bir tarafin ILK marka kelimesi, karsi tarafin marka kelimelerinde
    ya da isminin ilk 3 kelimesinde bulunmali. McVitie's ornegi: SOK'un ilk
    kelimesi "mcvities", BIM markasi "Ulker McVitie's" icinde var -> gecer.
    """
    ba, bb = brand_tokens(a), brand_tokens(b)
    if not ba or not bb:
        return False
    head_a = set(ba) | set(tokens(a.name)[:3])
    head_b = set(bb) | set(tokens(b.name)[:3])
    return (any(_same_word(ba[0], y) for y in head_b)
            or any(_same_word(bb[0], y) for y in head_a))


def remainder(p: Product, brand_toks: list[str]) -> list[str]:
    """Isimden marka kelimelerini cikarir: 'Ulker Hobby Findikli Bar' -> hobby findikli bar"""
    return [t for t in tokens(p.name)
            if not any(_same_word(t, b) for b in brand_toks)]


def trigrams(words: list[str]) -> set[str]:
    """pg_trgm ile ayni mantik: her kelime '  kelime ' seklinde doldurulur."""
    grams: set[str] = set()
    for w in words:
        padded = f"  {w} "
        grams.update(padded[i:i + 3] for i in range(len(padded) - 2))
    return grams


def is_multi(p: Product) -> bool:
    return bool(MULTI_MARKERS & set(tokens(p.name)))


# --------------------------------------------------------------------------
# Karar
# --------------------------------------------------------------------------
def compare(a: Product, b: Product) -> Verdict:
    """Iki urunu bes kapidan gecirir; ilk takildigi kapiyi soyler."""
    # 1) Cesitleri
    if is_multi(a) or is_multi(b):
        return Verdict(False, "cesitleri")

    # 2) Gramaj
    ua, ub = a.base_unit, b.base_unit
    if ua is None or ub is None:
        return Verdict(False, "gramaj yok")
    if ua != ub:
        return Verdict(False, "gramaj farkli")

    # 3) Marka
    if not brands_match(a, b):
        return Verdict(False, "marka farkli")

    all_brand = brand_tokens(a) + brand_tokens(b)
    ra, rb = remainder(a, all_brand), remainder(b, all_brand)
    if not ra or not rb:
        return Verdict(False, "isim bos")

    # 4a) Sayilar: "%1,5" -> ['1','5'] ile "%3" -> ['3'] ayni degil
    na = sorted(t for t in ra if t.isdigit())
    nb = sorted(t for t in rb if t.isdigit())
    if na != nb:
        return Verdict(False, "sayi farkli")

    # 4b) Cesit kelimeleri
    va, vb = VARIANT_WORDS & set(ra), VARIANT_WORDS & set(rb)
    if va != vb:
        diff = ", ".join(sorted(va ^ vb))
        return Verdict(False, f"cesit farkli ({diff})")

    # 5) Benzerlik
    ta, tb = trigrams(ra), trigrams(rb)
    common = len(ta & tb)
    jaccard = common / len(ta | tb)
    if jaccard >= JACCARD_MIN:
        return Verdict(True, "benzerlik", jaccard)

    # Kisa isim uzun ismin icinde tamamen geciyorsa: SOK bazen ismi kisaltiyor
    # ("Dankek Magma Cikolatali" / "Magma Cikolatali Sos Dolgulu Kek").
    #
    # Koruma: urunun TAM adi en az 3 kelime olmali. "Mis Sut" (2 kelime)
    # her seyin icinde gecer, ona kapsama uygulanmaz.
    # Neden tam ad, marka cikarilmis kalan degil: marka alani urun hattini
    # da icerebiliyor ("Dankek Magma"); o zaman kalan tek kelimeye iner
    # ve gercek eslesme kacar. Gercek veride tam olarak bu oldu.
    containment = common / min(len(ta), len(tb))
    label_len = min(len(tokens(a.label)), len(tokens(b.label)))
    if containment >= CONTAIN_MIN and label_len >= 3:
        return Verdict(True, "kapsama", jaccard)

    return Verdict(False, "benzerlik dusuk", jaccard)


def find_matches(products: list[Product]) -> tuple[list[Match], Counter]:
    """Her market cifti icin eslesmeleri bulur.

    'Bloklama': her urunu 1400 urunle karsilastirmak yerine sadece ayni
    gramajdakilerle karsilastiriyoruz. Sonuc ayni, is yuku cok daha az.
    """
    by_store: dict[str, list[Product]] = defaultdict(list)
    for p in products:
        by_store[p.store].append(p)

    matches: list[Match] = []
    reasons: Counter = Counter()

    for store_a, store_b in combinations(sorted(by_store), 2):
        index: dict[tuple, list[Product]] = defaultdict(list)
        for p in by_store[store_b]:
            if p.base_unit is not None:
                index[p.base_unit].append(p)

        for a in by_store[store_a]:
            if a.base_unit is None:
                continue
            best: tuple[Verdict, Product] | None = None
            for b in index.get(a.base_unit, []):
                v = compare(a, b)
                reasons[v.reason.split(" (")[0]] += 1
                if v.ok and (best is None or v.score > best[0].score):
                    best = (v, b)
            if best:
                v, b = best
                matches.append(Match(a, b, v.score, f"{METHOD_VERSION}-{v.reason}"))

    return matches, reasons


# --------------------------------------------------------------------------
# Veritabani
# --------------------------------------------------------------------------
PAGE = 1000


def load_products(sb) -> list[Product]:
    """Tum urunleri sayfa sayfa okur.

    DIKKAT: Supabase tek istekte en fazla 1000 satir dondurur. SOK'ta 1400+
    urun var; sayfalamasaydik 400'u sessizce disarida kalirdi.
    """
    stores = {r["id"]: r["slug"]
              for r in sb.table("stores").select("id,slug").execute().data}
    out: list[Product] = []
    start = 0
    while True:
        rows = (sb.table("products")
                .select("id,store_id,brand,name,unit_amount,unit_type")
                .order("id")
                .range(start, start + PAGE - 1)
                .execute().data) or []
        for r in rows:
            out.append(Product(
                id=r["id"], store=stores.get(r["store_id"], "?"),
                name=r["name"], brand=r.get("brand"),
                unit_amount=r.get("unit_amount"), unit_type=r.get("unit_type"),
            ))
        if len(rows) < PAGE:
            break
        start += PAGE
    return out


def save_matches(sb, matches: list[Match]) -> int:
    """Otomatik eslesmeleri yeniler; elle onaylanan/reddedilenlere dokunmaz."""
    manual = {
        (r["product_id"], r["matched_product_id"])
        for r in (sb.table("product_matches")
                  .select("product_id,matched_product_id")
                  .neq("status", "auto").execute().data or [])
    }
    # Eski otomatik eslesmeleri sil: kurallar iyilestiginde hatalilar temizlensin
    sb.table("product_matches").delete().eq("status", "auto").execute()

    rows = [m.row() for m in matches
            if (m.a.id, m.b.id) not in manual]
    for i in range(0, len(rows), 200):
        sb.table("product_matches").insert(rows[i:i + 200]).execute()
    log.info("%d otomatik eslesme yazildi (%d elle incelenmis korundu)",
             len(rows), len(manual))
    return len(rows)


# --------------------------------------------------------------------------
# Komut satiri
# --------------------------------------------------------------------------
def print_report(matches: list[Match], reasons: Counter) -> None:
    print(f"\n{'SKOR':>5}  {'YONTEM':<16} {'URUN A':<42} {'URUN B':<42} GRAMAJ")
    print("-" * 125)
    for m in sorted(matches, key=lambda x: -x.score):
        unit = f"{m.a.unit_amount} {m.a.unit_type}"
        print(f"{m.score:>5.2f}  {m.method:<16} {m.a.label[:41]:<42} "
              f"{m.b.label[:41]:<42} {unit}")
    print("-" * 125)
    print(f"{len(matches)} eslesme\n")
    print("Ayni gramajdaki adaylar nerede elendi:")
    for reason, n in reasons.most_common():
        print(f"  {reason:<18} {n:>6}")


def explain(products: list[Product], needle: str) -> None:
    """Adinda METIN gecen urunleri ve aralarindaki her karsilastirmayi gosterir.

    "Bu urun neden eslesmedi?" sorusunun cevabi: hangi kapida takildigi,
    markasinin ne sanildigi, isimden geriye ne kaldigi.
    """
    key = normalize_tr(needle)
    found = [p for p in products if key in normalize_tr(p.label)]
    print(f"\n'{needle}' gecen {len(found)} urun:\n")
    for p in found:
        brand = brand_tokens(p)
        print(f"  [{p.store}] #{p.id:<6} {p.label[:50]:<50} "
              f"gramaj={p.unit_amount} {p.unit_type} -> {p.base_unit}")
        print(f"  {'':>12} marka kelimeleri={brand}  "
              f"isim kelimeleri={tokens(p.name)}")

    print("\nMarketler arasi karsilastirmalar:\n")
    shown = 0
    for a in found:
        for b in found:
            if a.store >= b.store:
                continue
            v = compare(a, b)
            mark = "ESLESTI" if v.ok else "elendi "
            print(f"  {mark} [{v.reason:<22}] skor={v.score:.2f}  "
                  f"{a.label[:38]:<38} <-> {b.label[:38]}")
            shown += 1
    if not shown:
        print("  (karsilastirilacak cift yok: urunler tek markette olabilir)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Marketler arasi urun eslestirme")
    parser.add_argument("--dry-run", action="store_true",
                        help="eslesmeleri goster, veritabanina yazma")
    parser.add_argument("--explain", metavar="METIN",
                        help="adinda METIN gecen urunlerin karsilastirmalarini goster")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    from .db import get_client
    sb = get_client()

    products = load_products(sb)
    per_store = Counter(p.store for p in products)
    log.info("%d urun okundu: %s", len(products),
             ", ".join(f"{k}={v}" for k, v in sorted(per_store.items())))

    if args.explain:
        explain(products, args.explain)
        return 0

    matches, reasons = find_matches(products)
    print_report(matches, reasons)

    if args.dry_run:
        log.info("dry-run: veritabanina yazilmadi")
        return 0

    save_matches(sb, matches)
    return 0


if __name__ == "__main__":
    sys.exit(main())
