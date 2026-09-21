"""ŞOK Market scraper'i.

BIM'den uc temel farki var:

1) Aktuel katalog degil, RAF FIYATI. BIM haftalik kampanya yayinliyor;
   SOK'un online magazasi her gun gecerli normal fiyati gosteriyor.
   Bu yuzden campaign_start/campaign_end bos kaliyor.

2) SAYFALAMA var. Kategori basina 20 urun gosteriliyor, gerisi ?page=2,
   ?page=3... Toplam sayi yazilmiyor; bos sayfa gelene kadar ilerliyoruz.

3) KARARLI URUN KIMLIGI var. Adresler "...-p-6044" ile bitiyor. BIM'de
   slug her katalogda degistigi icin urunu ad+gramajdan taniyorduk; SOK
   urun adini degistirse bile 6044 ayni kaliyor. Fiyat gecmisi kopmasin
   diye fingerprint'i bu numaradan uretiyoruz.

Kart yapisi (Eylul 2026, tools/inspect_sok.py ile dogrulandi):

  <a href="/mis-pastorize-gunluk-sut-1-l-p-6044">
    <h2 class="CProductCard-module_title__u8bMW">Mis Pastörize Günlük Süt 1 L</h2>
    <span class="CPriceBox-module_price__bYk-c" data-testid="discountedPrice">59,00 ₺</span>
  </a>

DIKKAT: "__u8bMW" gibi ekler CSS Modules hash'i. Site her derlendiginde
degisir. Bu yuzden sinif adinin sadece ON EKINI eslestiriyoruz.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from ..config import SOK_BASE_URL, SOK_MAX_PAGES
from ..models import ScrapedProduct
from .base import BaseScraper
from .common import (
    UNIT_RE,
    find_prices,
    guess_category,
    make_fingerprint,
    parse_unit,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Hedef kategoriler: yol -> bizim kategori adimiz
#
# Kaynak kategoriyi bildigimiz icin anahtar kelime tahminine gerek yok;
# SOK "sut ve sut urunleri" diyorsa oyledir. None olanlar karisik
# kategoriler (yemeklik malzemede hem yag hem bakliyat var), onlarda
# tahmin yurutuyoruz.
# --------------------------------------------------------------------------
SOK_CATEGORIES: dict[str, str | None] = {
    "/sut-ve-sut-urunleri-c-460": "sut-urunleri",
    "/kahvaltilik-c-890": "kahvaltilik",
    "/et-ve-tavuk-ve-sarkuteri-c-160": "et-tavuk-balik",
    "/meyve-ve-sebze-c-20": "meyve-sebze",
    "/yemeklik-malzemeler-c-1770": None,
    "/ekmek-ve-pastane-c-1250": "kahvaltilik",
    "/icecek-c-20505": "icecek",
    "/atistirmaliklar-c-20376": "atistirmalik",
}

# --------------------------------------------------------------------------
# Seciciler (hash'e dayanikli)
# --------------------------------------------------------------------------
PRODUCT_HREF_RE = re.compile(r"-p-(\d+)/?$")
TITLE_CLS_RE = re.compile(r"^CProductCard-module_title")
PRICEBOX_CLS_RE = re.compile(r"^CPriceBox-module_cPriceBox")
PRICE_TESTID_RE = re.compile(r"price", re.I)
CURRENT_PRICE_TESTID = "discountedPrice"


def split_title_unit(title: str) -> tuple[str, str | None]:
    """'Mis Pastörize Günlük Süt 1 L' -> ('Mis Pastörize Günlük Süt', '1 L')

    Gramaj basligin SONUNDA ise ayirir. Ortadaysa ("Süt 1 L Kutu Mega")
    basligi bozmamak icin oldugu gibi birakir ama gramaji yine dondurur.
    """
    title = " ".join(title.split())
    matches = list(UNIT_RE.finditer(title))
    if not matches:
        return title, None
    m = matches[-1]
    tail = title[m.end():].strip()
    if len(tail) <= 3:  # sonda en fazla "'lü" gibi kisa bir ek kalmis
        name = title[: m.start()].strip(" -,/")
        return (name or title), title[m.start():].strip()
    return title, m.group(0).strip()


def _href_path(href: str) -> str:
    return href.split("?")[0].split("#")[0]


class SokScraper(BaseScraper):
    store_slug = "sok"
    store_name = "ŞOK"
    store_website = SOK_BASE_URL

    # -- kart ayiklama -----------------------------------------------------
    @staticmethod
    def _find_card(anchor: Tag) -> Tag:
        """Kartin kendisi genelde <a>'nin icinde; baslik yoksa yukari cik."""
        if anchor.find(class_=TITLE_CLS_RE) or anchor.find(["h2", "h3"]):
            return anchor
        node: Tag | None = anchor
        for _ in range(6):
            node = node.parent if node else None
            if node is None or not isinstance(node, Tag):
                break
            hrefs = {_href_path(a["href"]) for a in node.find_all("a", href=True)
                     if PRODUCT_HREF_RE.search(_href_path(a["href"]))}
            if len(hrefs) > 1:
                break
            if node.find(class_=TITLE_CLS_RE) or node.find(["h2", "h3"]):
                return node
        return anchor

    @staticmethod
    def _extract_title(card: Tag) -> str | None:
        el = card.find(class_=TITLE_CLS_RE) or card.find(["h2", "h3"])
        if el is None:
            return None
        text = el.get_text(" ", strip=True)
        return text or None

    @staticmethod
    def _extract_prices(card: Tag):
        """(guncel_fiyat, eski_fiyat)

        Oncelik sirasi:
          1) data-testid="discountedPrice"  -> test nitelikleri en kararli tutamak
          2) data-testid'inde 'price' gecen diger elemanlar -> eski fiyat adayi
          3) fiyat kutusunun metni
          4) kartin tum metni (son care)
        """
        current = None
        el = card.find(attrs={"data-testid": CURRENT_PRICE_TESTID})
        if el is not None:
            values = find_prices(el.get_text(" ", strip=True))
            current = values[0] if values else None

        others = []
        for tagged in card.find_all(attrs={"data-testid": PRICE_TESTID_RE}):
            if tagged.get("data-testid") == CURRENT_PRICE_TESTID:
                continue
            others.extend(find_prices(tagged.get_text(" ", strip=True)))

        if current is None:
            box = card.find(class_=PRICEBOX_CLS_RE)
            source = box.get_text(" ", strip=True) if box else card.get_text(" ", strip=True)
            values = find_prices(source)
            if not values:
                return None, None
            current = min(values)
            others.extend(v for v in values if v != current)

        old = max(others) if others else None
        if old is not None and old <= current:
            old = None
        return current, old

    @staticmethod
    def _extract_image(card: Tag) -> str | None:
        for img in card.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src and "ceptesok" in src:
                return src
        return None

    # -- tek sayfa ---------------------------------------------------------
    def parse(self, html: str, *, source_url: str,
              category: str | None = None) -> list[ScrapedProduct]:
        soup = BeautifulSoup(html, "lxml")
        items: list[ScrapedProduct] = []
        seen: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            path = _href_path(anchor["href"])
            m = PRODUCT_HREF_RE.search(path)
            if not m or path in seen:
                continue
            seen.add(path)
            product_id = m.group(1)

            card = self._find_card(anchor)
            title = self._extract_title(card)
            if not title:
                log.debug("baslik yok, atlandi: %s", path)
                continue

            price, old_price = self._extract_prices(card)
            if price is None:
                log.debug("fiyat yok, atlandi: %s", path)
                continue

            name, unit_raw = split_title_unit(title)
            unit_amount, unit_type = parse_unit(unit_raw or "")

            try:
                items.append(ScrapedProduct(
                    # Kararli numara var -> ad degisse bile ayni urun kalsin
                    fingerprint=make_fingerprint(self.store_slug, None,
                                                 f"id {product_id}", None),
                    external_id=product_id,
                    name=name,
                    brand=None,  # SOK markayi ayri alanda vermiyor; ad icinde
                    category=category or guess_category(None, name, unit_raw),
                    unit_raw=unit_raw,
                    unit_amount=unit_amount,
                    unit_type=unit_type,
                    image_url=self._extract_image(card),
                    product_url=urljoin(SOK_BASE_URL, path),
                    price=price,
                    old_price=old_price,
                    source_url=source_url,
                ))
            except Exception as exc:
                log.warning("dogrulama hatasi (%s): %s", path, exc)

        return items

    # -- bir kategoriyi bastan sona gez -----------------------------------
    def scrape_category(self, path: str, category: str | None,
                        max_pages: int = SOK_MAX_PAGES) -> list[ScrapedProduct]:
        collected: dict[str, ScrapedProduct] = {}
        page_size: int | None = None
        slug = path.strip("/").split("-c-")[0][:30]

        for page in range(1, max_pages + 1):
            url = urljoin(SOK_BASE_URL, path)
            if page > 1:
                url = f"{url}?page={page}"
            # Sadece ilk sayfayi diske yaz; hepsini yazmak yuzlerce MB eder
            html = self.fetch(url, tag=f"{slug}_p{page}", save=(page == 1))
            items = self.parse(html, source_url=url, category=category)

            new = [i for i in items if i.fingerprint not in collected]
            for i in new:
                collected[i.fingerprint] = i

            log.info("  %s s.%d: %d urun (%d yeni)", slug, page, len(items), len(new))

            # Durma kosullari:
            #  - hic yeni urun yok: son sayfayi gectik (site ya bos sayfa ya
            #    da ayni sayfayi tekrar veriyor; ikisini de yakalar)
            #  - sayfa ilkinden kisa: bu son sayfa, bir istek daha atmaya gerek yok
            if not new:
                break
            if page_size is None:
                page_size = len(items)
            elif len(items) < page_size:
                break
        else:
            log.warning("%s: %d sayfa sinirina ulasildi, devami olabilir",
                        slug, max_pages)

        return list(collected.values())

    def collect(self, *, all_catalogs: bool = False,
                categories: dict[str, str | None] | None = None
                ) -> list[ScrapedProduct]:
        categories = categories or SOK_CATEGORIES
        results: dict[str, ScrapedProduct] = {}

        for path, category in categories.items():
            try:
                items = self.scrape_category(path, category)
            except Exception as exc:
                # Bir kategori patlarsa digerleri yine toplansin
                log.error("kategori basarisiz %s: %s", path, exc)
                continue
            before = len(results)
            for item in items:
                # Ayni urun birden fazla kategoride olabilir: ilk goren kazanir
                results.setdefault(item.fingerprint, item)
            log.info("%s -> %d urun (%d yeni toplam)",
                     path, len(items), len(results) - before)

        return list(results.values())
