"""BIM aktuel urunler scraper'i.

Sayfa sunucu tarafinda render ediliyor -> Selenium gerekmiyor.
Yapi (Eylul 2026 itibariyle):

  <kart>
    <a href="/aktuel-urunler/cilekli-sut-7-9/aktuel.aspx">
    <baslik>ICIMINO</baslik>        <- marka
    <baslik>CILEKLI SUT</baslik>    <- urun adi
    • 6x180 ml                      <- gramaj
    79, 00 TL                       <- fiyat
  </kart>

Parser bilerek CSS sinif adlarina bagli degil: urun linkinin href
deseninden yukari tirmanip karti buluyor. Boylece BIM tema degistirse
bile calismaya devam etme sansi yuksek.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..config import BIM_AKTUEL_PATH, BIM_BASE_URL, today_tr
from ..models import ScrapedProduct
from .base import BaseScraper

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Regex'ler
# --------------------------------------------------------------------------
PRODUCT_HREF_RE = re.compile(r"/aktuel-urunler/[^/]+/aktuel\.aspx", re.I)
CATALOG_KEY_RE = re.compile(r"Bim_AktuelTarihKey=(\d+)", re.I)
# Fiyat BIM'de uc parcaya bolunuyor: "79," + "00" + "₺"
# get_text(" ") sonrasi "79, 00 ₺" olur. Tamsayi ve kurus ayri yakalaniyor ki
# gramajdaki sayi ("6x180 ml") fiyata karismasin.
PRICE_RE = re.compile(
    r"(\d{1,3}(?:\.\d{3})+|\d+)\s*(?:[,.]\s*(\d{1,2}))?\s*(?:₺|TL\b|TRY\b)",
    re.I,
)

# Gercek DOM sinif adlari (Eylul 2026 - tools/inspect_bim.py ile dogrulandi)
CLS_BRAND = "subTitle"
CLS_NAME = "title"
CLS_UNIT = "gramajadet"
CLS_PRICE = "priceArea"
BULLET_RE = re.compile(r"^[•·∙\-–]\s*")
SLUG_PERIOD_SUFFIX_RE = re.compile(r"-\d{1,2}-\d{1,2}$")

# "6x180 ml", "2,5 kg", "1 L", "700 g 20-24 cm"
UNIT_RE = re.compile(
    r"(?:(\d+)\s*[x×*]\s*)?(\d+(?:[.,]\d+)?)\s*"
    r"(kg|gr|g|ml|lt|l|cl|cc|adet|lı|li|lu|lü)\b",
    re.I,
)
UNIT_ALIASES = {"gr": "g", "lt": "l", "cc": "ml", "lı": "adet", "li": "adet",
                "lu": "adet", "lü": "adet"}

TR_MONTHS = {
    "ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5, "haziran": 6,
    "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10, "kasim": 11, "aralik": 12,
}

DATE_RANGE_RE = re.compile(r"(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(\w+)", re.UNICODE)
DATE_SINGLE_RE = re.compile(r"(\d{1,2})\s+(\w+)", re.UNICODE)

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
    "yag-sos-salca", "kahvaltilik", "icecek",
}


# --------------------------------------------------------------------------
# Yardimci fonksiyonlar (saf - test edilebilir)
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


def _year_for(month: int, day: int, today: date | None = None) -> int:
    """Katalog etiketlerinde yil yok. Bugune en yakin yili sec."""
    today = today or today_tr()
    best, best_delta = today.year, None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        delta = abs((candidate - today).days)
        if best_delta is None or delta < best_delta:
            best, best_delta = year, delta
    return best


def parse_catalog_label(label: str, today: date | None = None
                        ) -> tuple[date | None, date | None]:
    """'08 Eylul Sali' -> (2026-09-08, None)
       '01-28 Eylul'   -> (2026-09-01, 2026-09-28)"""
    if not label:
        return None, None
    norm = fold_tr(label)

    m = DATE_RANGE_RE.search(norm)
    if m:
        d1, d2, month_name = int(m.group(1)), int(m.group(2)), m.group(3)
        month = TR_MONTHS.get(month_name)
        if month:
            year = _year_for(month, d1, today)
            try:
                return date(year, month, d1), date(year, month, d2)
            except ValueError:
                return None, None

    m = DATE_SINGLE_RE.search(norm)
    if m:
        day, month_name = int(m.group(1)), m.group(2)
        month = TR_MONTHS.get(month_name)
        if month:
            year = _year_for(month, day, today)
            try:
                start = date(year, month, day)
            except ValueError:
                return None, None
            # BIM aktuelleri tipik olarak ~1 hafta raflarda kaliyor
            return start, start + timedelta(days=6)

    return None, None


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
class BimScraper(BaseScraper):
    store_slug = "bim"
    store_name = "BİM"
    store_website = BIM_BASE_URL

    # -- katalog kesfi -----------------------------------------------------
    @staticmethod
    def discover_catalogs(soup: BeautifulSoup) -> dict[str, str]:
        """{'1654': '08 Eylül Salı', ...}"""
        catalogs: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            m = CATALOG_KEY_RE.search(a["href"])
            if not m:
                continue
            label = a.get_text(" ", strip=True)
            if label and m.group(1) not in catalogs:
                catalogs[m.group(1)] = label
        return catalogs

    # -- kart bulma --------------------------------------------------------
    @staticmethod
    def _find_card(anchor: Tag) -> Tag | None:
        """Urun linkinden yukari tirmanip fiyati da iceren en kucuk kabi bul."""
        node: Tag | None = anchor
        fallback = anchor
        for _ in range(8):
            node = node.parent if node else None
            if node is None or not isinstance(node, Tag):
                break
            hrefs = {a.get("href") for a in node.find_all("a", href=PRODUCT_HREF_RE)}
            if len(hrefs) > 1:
                break  # cok ileri gittik, birden fazla urunu kapsiyor
            fallback = node
            if PRICE_RE.search(node.get_text(" ", strip=True)):
                return node
        return fallback

    @staticmethod
    def _extract_image(card: Tag) -> str | None:
        for img in card.find_all("img"):
            for attr in ("src", "data-src", "data-original", "data-lazy"):
                src = img.get(attr)
                if src and "/uploads/" in src:
                    return urljoin(BIM_BASE_URL, src)
        return None

    @staticmethod
    def _text_of(card: Tag, class_name: str) -> str | None:
        el = card.find(class_=class_name)
        if el is None:
            return None
        text = el.get_text(" ", strip=True)
        return text or None

    @classmethod
    def _extract_unit(cls, card: Tag) -> tuple[str | None, list[str]]:
        """(ana_gramaj, varyant_satirlari)"""
        unit = cls._text_of(card, CLS_UNIT)
        if unit:
            unit = BULLET_RE.sub("", unit).strip() or None
        bullets = cls._extract_bullets(card)
        if unit is None and bullets:
            unit, bullets = bullets[0], bullets[1:]
        else:
            bullets = [b for b in bullets if b != unit]
        return unit, bullets

    @classmethod
    def _price_text(cls, card: Tag) -> str:
        """Fiyati sadece fiyat kutusundan oku; kart metninin tamami degil.

        Aksi halde gramajdaki sayilar ('6x180 ml') fiyata karisabiliyor.
        """
        el = card.find(class_=CLS_PRICE)
        if el is not None:
            text = el.get_text(" ", strip=True)
            if text:
                return text
        return card.get_text(" ", strip=True)

    @classmethod
    def _extract_titles(cls, card: Tag) -> tuple[str | None, str | None]:
        """(marka, urun_adi) - bazi urunlerde marka ayri verilmiyor."""
        brand = cls._text_of(card, CLS_BRAND)
        name = cls._text_of(card, CLS_NAME)
        if name:
            return brand, name

        # Tema degisirse diye yedek plan: basliklardan sirayla oku
        parts = [
            h.get_text(" ", strip=True)
            for h in card.find_all(["h1", "h2", "h3", "h4", "h5"])
            if h.get_text(strip=True)
        ]
        if not parts:
            # tema degisirse diye yedek plan
            parts = [
                el.get_text(" ", strip=True)
                for el in card.find_all(
                    class_=re.compile(r"(title|name|brand|urun)", re.I)
                )
                if el.get_text(strip=True)
            ]
        parts = [p for p in dict.fromkeys(parts) if p.lower() != "paylaş"]
        if not parts:
            return None, None
        if len(parts) == 1:
            return None, parts[0]
        return parts[0], " ".join(parts[1:])

    @staticmethod
    def _extract_bullets(card: Tag) -> list[str]:
        """Madde isaretli satirlari toplar.

        BIM bazi kartlarda '•' isaretini ayri bir elemana koyuyor; o zaman
        satir basinda bullet gorunmez. Bu yuzden <li> icerikleri de
        dogrudan okunuyor.
        """
        bullets: list[str] = []
        for line in card.get_text("\n", strip=True).splitlines():
            line = line.strip()
            if BULLET_RE.match(line):
                cleaned = BULLET_RE.sub("", line).strip()
                if cleaned:
                    bullets.append(cleaned)
        for li in card.find_all("li"):
            cleaned = BULLET_RE.sub("", li.get_text(" ", strip=True)).strip()
            if cleaned:
                bullets.append(cleaned)
        # sirayi koruyarak tekrarlari at
        return list(dict.fromkeys(bullets))

    # -- ana parser --------------------------------------------------------
    def parse(self, html: str, *, source_url: str,
              campaign_start: date | None = None,
              campaign_end: date | None = None) -> list[ScrapedProduct]:
        soup = BeautifulSoup(html, "lxml")
        products: list[ScrapedProduct] = []
        seen_hrefs: set[str] = set()
        seen_fingerprints: set[str] = set()

        anchors = soup.find_all("a", href=PRODUCT_HREF_RE)
        log.info("%d urun linki bulundu", len(anchors))

        for anchor in anchors:
            href = anchor["href"]
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            card = self._find_card(anchor)
            if card is None:
                continue

            prices = find_prices(self._price_text(card))
            if not prices:
                log.debug("fiyat bulunamadi, atlandi: %s", href)
                continue
            price = min(prices)                               # guncel fiyat
            old_price = max(prices) if len(prices) > 1 else None
            if old_price is not None and old_price <= price:
                old_price = None

            brand, name = self._extract_titles(card)
            if not name:
                log.debug("urun adi bulunamadi, atlandi: %s", href)
                continue

            unit_raw, bullets = self._extract_unit(card)
            unit_amount, unit_type = parse_unit(unit_raw or "")
            if unit_amount is None and bullets:
                # gramaj bos birakilmissa varyant satirlarinda ara
                for b in bullets:
                    unit_amount, unit_type = parse_unit(b)
                    if unit_amount is not None:
                        unit_raw = b
                        break

            slug = href.strip("/").split("/")[1] if "/" in href.strip("/") else href
            external_id = SLUG_PERIOD_SUFFIX_RE.sub("", slug)

            fingerprint = make_fingerprint(self.store_slug, brand, name, unit_raw)
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)

            try:
                products.append(
                    ScrapedProduct(
                        fingerprint=fingerprint,
                        external_id=external_id,
                        name=name,
                        brand=brand,
                        category=guess_category(brand, name, unit_raw),
                        unit_raw=unit_raw,
                        unit_amount=unit_amount,
                        unit_type=unit_type,
                        image_url=self._extract_image(card),
                        product_url=urljoin(BIM_BASE_URL, href),
                        price=price,
                        old_price=old_price,
                        campaign_start=campaign_start,
                        campaign_end=campaign_end,
                        source_url=source_url,
                        variants=bullets,
                    )
                )
            except Exception as exc:  # pydantic dogrulama hatasi
                log.warning("dogrulama hatasi (%s): %s", href, exc)

        # "Daha Fazla Urun Goster" butonu var mi? -> gizli urun olabilir
        if "changeLPage" in html and len(products) < len(anchors):
            log.warning(
                "Sayfada 'Daha Fazla Urun Goster' butonu var. Cikarilan urun "
                "sayisini (%d) siteyle karsilastir.", len(products)
            )

        return products

    # -- orkestrasyon ------------------------------------------------------
    def collect(self, *, all_catalogs: bool = False) -> list[ScrapedProduct]:
        entry_url = urljoin(BIM_BASE_URL, BIM_AKTUEL_PATH)
        html = self.fetch(entry_url, tag="index")
        soup = BeautifulSoup(html, "lxml")
        catalogs = self.discover_catalogs(soup)
        log.info("%d katalog sekmesi bulundu: %s",
                 len(catalogs), ", ".join(catalogs.values()))

        results: list[ScrapedProduct] = []
        today = today_tr()

        if not all_catalogs:
            # Varsayilan sayfa = o an aktif katalog.
            # Etiketi bugune en yakin olan katalogdan tarih tahmini yapiyoruz.
            best_label, best_delta = None, None
            for label in catalogs.values():
                start, _ = parse_catalog_label(label, today)
                if start is None:
                    continue
                delta = abs((start - today).days)
                if best_delta is None or delta < best_delta:
                    best_label, best_delta = label, delta
            start, end = parse_catalog_label(best_label or "", today)
            results = self.parse(html, source_url=entry_url,
                                 campaign_start=start, campaign_end=end)
            return results

        for key, label in catalogs.items():
            url = f"{entry_url}?Bim_AktuelTarihKey={key}"
            start, end = parse_catalog_label(label, today)
            try:
                page_html = self.fetch(url, tag=f"cat{key}")
            except Exception as exc:
                log.error("katalog %s indirilemedi: %s", label, exc)
                continue
            items = self.parse(page_html, source_url=url,
                               campaign_start=start, campaign_end=end)
            log.info("katalog '%s': %d urun", label, len(items))
            results.extend(items)

        # farkli kataloglarda ayni urun cikabilir -> en dusuk fiyati tut
        deduped: dict[str, ScrapedProduct] = {}
        for item in results:
            existing = deduped.get(item.fingerprint)
            if existing is None or item.price < existing.price:
                deduped[item.fingerprint] = item
        return list(deduped.values())


def filter_temel_gida(items: list[ScrapedProduct]) -> list[ScrapedProduct]:
    return [i for i in items if i.category in TEMEL_GIDA_CATEGORIES]
