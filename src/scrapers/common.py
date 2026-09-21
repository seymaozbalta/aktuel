"""Marketler arasi ortak yardimcilar.

BIM ve SOK parser'larinin ikisi de fiyat, gramaj, Turkce normalizasyon ve
kategori tahmini icin ayni fonksiyonlari kullaniyor. Ucuncu market
eklendiginde de burasi degismeden kalacak.

Buradaki her fonksiyon saf: ag, veritabani ya da HTML bilmez. Bu yuzden
test etmesi kolay ve bir marketteki degisiklik digerini bozmaz.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, InvalidOperation

from ..models import ScrapedProduct

# --------------------------------------------------------------------------
# Fiyat
# --------------------------------------------------------------------------
# Fiyat BIM'de uc parcaya bolunuyor: "79," + "00" + "₺"
# get_text(" ") sonrasi "79, 00 ₺" olur. Tamsayi ve kurus ayri yakalaniyor ki
# gramajdaki sayi ("6x180 ml") fiyata karismasin.
PRICE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:[,.]\s*(\d{1,2}))?\s*(?:₺|TL\b|TRY\b)",
    re.I,
)

# --------------------------------------------------------------------------
# Gramaj
# --------------------------------------------------------------------------
# "6x180 ml", "2,5 kg", "1 L", "700 g 20-24 cm"
UNIT_RE = re.compile(
    r"(?:(\d+)\s*[x×*]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kg|gr|g|ml|lt|l|cl|cc|adet|lı|li|lu|lü)\b",
    re.I,
)
UNIT_ALIASES = {"gr": "g", "lt": "l", "cc": "ml", "lı": "adet", "li": "adet",
                "lu": "adet", "lü": "adet"}

# --------------------------------------------------------------------------
# Kategori
# --------------------------------------------------------------------------
# Temel gida filtresi / kategori tahmini
#
# DIKKAT: eslesme kelime siniri (\b) ile yapiliyor. Duz "in" araması yapilsaydi
# "su" -> "SUshida", "kola" -> "ciKOLAta" gibi yanlis eslesmeler olurdu.
# Turkce ekli biçimleri ("pekmez" / "pekmezi") ayri ayri yazmak gerekiyor.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "sut-urunleri": (
        "sut", "sutu", "peynir", "peyniri", "yogurt", "yogurdu", "ayran",
        "kefir", "tereyag", "tereyagi", "quark", "kaymak", "sutlac",
    ),
    "et-tavuk-balik": (
        "tavuk", "tavugu", "pilic", "dana", "kofte", "sucuk", "sucugu",
        "sosis", "doner", "salam", "somon", "balik", "kavurma", "burger",
        "kebap", "manti", "jambon", "pastirma", "ton", "sushi", "maki",
    ),
    "bakliyat-tahil": (
        "pirinc", "pirinci", "mercimek", "mercimegi", "nohut", "fasulye",
        "bulgur", "makarna", "un", "unu", "irmik", "sehriye", "lazanya",
        "kuskus", "bakliyat",
    ),
    "yag-sos-salca": (
        "yag", "yagi", "zeytinyag", "zeytinyagi", "aycicek", "ayciceg",
        "sirke", "sirkesi", "salca", "salcasi", "sos", "sosu", "soslari",
        "tahin", "tahini", "pekmez", "pekmezi", "mayonez", "ketcap",
    ),
    "kahvaltilik": (
        "zeytin", "zeytini", "recel", "receli", "bal", "bali", "ekmek",
        "ekmegi", "yumurta", "helva", "granola", "gevrek", "gevregi",
        "tost", "grissini", "kahvaltilik", "tahil", "musli",
    ),
    "atistirmalik": (
        "cikolata", "cikolatali", "biskuvi", "biskuvisi", "gofret", "kek",
        "cips", "cipsi", "kraker", "seker", "sekeri", "kurabiye", "draje",
        "bar", "sufle", "dondurma", "cerez", "findik", "badem", "fistik",
        "leblebi", "puding", "krema", "kremasi",
    ),
    "meyve-sebze": (
        "domates", "salatalik", "patates", "sogan", "biber", "elma", "muz",
        "portakal", "mandalina", "limon", "uzum", "armut", "karpuz", "kavun",
        "havuc", "marul", "maydanoz", "ispanak", "kabak", "patlican", "cilek",
        "meyve", "sebze",
    ),
    "icecek": (
        "cay", "cayi", "kahve", "kahvesi", "su", "suyu", "gazoz", "kola",
        "soda", "nektar", "icecek", "serbet", "limonata",
    ),
}

# Anahtar kelimeler kelime sinirlariyla derleniyor
_CATEGORY_PATTERNS: dict[str, re.Pattern[str]] = {
    cat: re.compile(r"\b(?:" + "|".join(sorted(kws, key=len, reverse=True)) + r")\b")
    for cat, kws in CATEGORY_KEYWORDS.items()
}

TEMEL_GIDA_CATEGORIES = {
    "sut-urunleri", "et-tavuk-balik", "bakliyat-tahil",
    "yag-sos-salca", "kahvaltilik", "icecek", "meyve-sebze",
}


# --------------------------------------------------------------------------
# Yardimci fonksiyonlar
# --------------------------------------------------------------------------
def fold_tr(text: str) -> str:
    """Turkce karakterleri ASCII'ye indirger, noktalama isaretlerini korur.

    Python'un str.lower()'i Turkce'de hatali ('I'.lower() -> 'i'), bu yuzden
    donusum elle yapiliyor.
    """
    if not text:
        return ""
    text = text.replace("İ", "i").replace("I", "i").replace("ı", "i")
    text = text.lower()
    text = (text.replace("ş", "s").replace("ğ", "g").replace("ü", "u")
                .replace("ö", "o").replace("ç", "c"))
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def normalize_tr(text: str) -> str:
    """fingerprint icin: sadece harf, rakam ve tek bosluk birakir."""
    text = re.sub(r"[^a-z0-9]+", " ", fold_tr(text))
    return " ".join(text.split())


def find_prices(text: str) -> list[Decimal]:
    """Metindeki tum para tutarlarini bulur.

    '• 6x180 ml 79, 00 ₺' -> [79.00]   (gramaj yutulmaz)
    '2.890 TL'            -> [2890.00]
    """
    values: list[Decimal] = []
    for m in PRICE_RE.finditer(text or ""):
        whole = m.group(1).replace(".", "")
        frac = (m.group(2) or "00").ljust(2, "0")
        try:
            value = Decimal(f"{whole}.{frac}")
        except InvalidOperation:
            continue
        if value > 0:
            values.append(value)
    return values


def parse_price(raw: str) -> Decimal | None:
    """Tek bir tutar metnini Decimal'e cevirir. Bulamazsa None."""
    values = find_prices(raw)
    return values[0] if values else None


def parse_unit(raw: str) -> tuple[Decimal | None, str | None]:
    """'6x180 ml' -> (1080, 'ml') | '2,5 kg' -> (2.5, 'kg')"""
    if not raw:
        return None, None
    m = UNIT_RE.search(raw)
    if not m:
        return None, None
    mult, amount, unit = m.group(1), m.group(2), m.group(3).lower()
    unit = UNIT_ALIASES.get(unit, unit)
    try:
        value = Decimal(amount.replace(",", "."))
    except InvalidOperation:
        return None, None
    if mult:
        value *= Decimal(mult)
    return value, unit



def guess_category(brand: str | None, name: str,
                   unit_raw: str | None = None) -> str | None:
    """En cok anahtar kelime eslesen kategoriyi secer.

    Tek eslesmeye bakmak yerine skorlamak, 'Sutlu Cikolata' gibi iki
    kategoriye de deyen urunlerde daha isabetli sonuc veriyor.
    """
    haystack = normalize_tr(f"{brand or ''} {name} {unit_raw or ''}")
    best, best_score = None, 0
    for category, pattern in _CATEGORY_PATTERNS.items():
        score = len(set(pattern.findall(haystack)))
        if score > best_score:
            best, best_score = category, score
    return best


def make_fingerprint(store_slug: str, brand: str | None, name: str,
                     unit_raw: str | None) -> str:
    base = "|".join([
        store_slug,
        normalize_tr(brand or ""),
        normalize_tr(name),
        normalize_tr(unit_raw or ""),
    ])
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:20]


# --------------------------------------------------------------------------
# Scraper
# --------------------------------------------------------------------------

def filter_temel_gida(items: list[ScrapedProduct]) -> list[ScrapedProduct]:
    return [i for i in items if i.category in TEMEL_GIDA_CATEGORIES]
