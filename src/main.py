"""Giris noktasi.

Kullanim:
    python -m src.main --dry-run              # DB'ye yazmadan dene
    python -m src.main                        # aktif katalogu kaydet
    python -m src.main --all-catalogs         # tum sekmeleri gez
    python -m src.main --only-temel-gida      # sadece temel gida
"""
from __future__ import annotations

import argparse
import logging
import sys
from .config import today_tr
from .models import ScrapedProduct
from .scrapers.bim import BimScraper, filter_temel_gida

SCRAPERS = {"bim": BimScraper}


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def print_preview(items: list[ScrapedProduct], limit: int = 25) -> None:
    print(f"\n{'MARKA':<20} {'URUN':<42} {'GRAMAJ':<14} {'FIYAT':>10}  KATEGORI")
    print("-" * 110)
    for item in items[:limit]:
        print(
            f"{(item.brand or '-')[:19]:<20} "
            f"{item.name[:41]:<42} "
            f"{(item.unit_raw or '-')[:13]:<14} "
            f"{item.price:>9,.2f}  "
            f"{item.category or '-'}"
        )
    if len(items) > limit:
        print(f"... ve {len(items) - limit} urun daha")
    print("-" * 110)

    by_cat: dict[str, int] = {}
    for item in items:
        by_cat[item.category or "siniflandirilmamis"] = (
            by_cat.get(item.category or "siniflandirilmamis", 0) + 1
        )
    print("Kategori dagilimi: " + ", ".join(
        f"{k}={v}" for k, v in sorted(by_cat.items(), key=lambda x: -x[1])
    ))
    no_unit = sum(1 for i in items if i.unit_amount is None)
    print(f"Toplam {len(items)} urun | gramaji ayiklanamayan: {no_unit}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aktuel urun toplayici")
    parser.add_argument("--store", default="bim", choices=sorted(SCRAPERS))
    parser.add_argument("--all-catalogs", action="store_true",
                        help="tum katalog sekmelerini gez (daha cok istek)")
    parser.add_argument("--only-temel-gida", action="store_true",
                        help="sadece temel gida kategorilerini kaydet")
    parser.add_argument("--dry-run", action="store_true",
                        help="veritabanina yazma, sadece ekrana bas")
    parser.add_argument("--limit", type=int, default=None,
                        help="ilk N urunu al (test icin)")
    parser.add_argument("--min-products", type=int, default=0,
                        help="bu sayidan az urun cikarsa hata ver (otomasyon icin)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(args.verbose)
    log = logging.getLogger("main")

    scraper = SCRAPERS[args.store]()
    log.info("%s icin toplama basliyor", scraper.store_name)

    items = scraper.collect(all_catalogs=args.all_catalogs)
    log.info("ham sonuc: %d urun", len(items))

    # Saglik kontrolu: parser kismen bozulursa sessizce az veri toplar
    # (ornegin 75 yerine 4 urun). Otomasyonda bunun gurultulu bir sekilde
    # hata vermesi gerekiyor, yoksa haftalar sonra fark edersin.
    # Filtrelerden once calisiyor, cunku --only-temel-gida sayiyi mesru
    # olarak dusurur.
    if args.min_products and len(items) < args.min_products:
        log.error(
            "SAGLIK KONTROLU BASARISIZ: %d urun bulundu, en az %d bekleniyordu. "
            "Site yapisi degismis olabilir; tools/inspect_bim.py ile kontrol et.",
            len(items), args.min_products,
        )
        return 1

    if args.only_temel_gida:
        items = filter_temel_gida(items)
        log.info("temel gida filtresi sonrasi: %d urun", len(items))

    if args.limit:
        items = items[: args.limit]

    if not items:
        log.error("Hic urun cikarilamadi. data/raw/ altindaki HTML'i incele "
                  "veya tools/inspect_bim.py calistir.")
        return 1


    print_preview(items)

    if args.dry_run:
        log.info("dry-run: veritabanina yazilmadi")
        return 0

    from .db import save_all  # geç import: dry-run'da Supabase sarti aranmasin

    written = save_all(
        items,
        store_slug=scraper.store_slug,
        store_name=scraper.store_name,
        store_website=scraper.store_website,
        scrape_date=today_tr(),
    )
    log.info("tamamlandi: %d fiyat kaydi", written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
