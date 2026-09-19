"""Supabase katmani.

Tum yazmalar upsert. Script'i gunde iki kez calistirsan da
prices tablosundaki (product_id, scrape_date) tekil kisiti
mukerrer satir olusmasini engelliyor.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Iterable, Iterator, Sequence

from supabase import Client, create_client

from .config import CHUNK_SIZE, SUPABASE_SERVICE_KEY, SUPABASE_URL, today_tr
from .models import ScrapedProduct

log = logging.getLogger(__name__)


def get_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL ve SUPABASE_SERVICE_KEY tanimli degil. "
            ".env dosyani kontrol et (.env.example'a bak)."
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _chunks(items: Sequence[dict], size: int = CHUNK_SIZE) -> Iterator[list[dict]]:
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


def get_or_create_store(sb: Client, slug: str, name: str,
                        website: str | None = None) -> int:
    res = sb.table("stores").select("id").eq("slug", slug).limit(1).execute()
    if res.data:
        return res.data[0]["id"]
    log.info("market kaydi olusturuluyor: %s", slug)
    created = (
        sb.table("stores")
        .insert({"slug": slug, "name": name, "website": website})
        .execute()
    )
    return created.data[0]["id"]


def upsert_products(sb: Client, store_id: int,
                    items: Iterable[ScrapedProduct]) -> dict[str, int]:
    """products tablosuna yazar, {fingerprint: product_id} dondurur."""
    rows = [item.product_row(store_id) for item in items]
    mapping: dict[str, int] = {}

    for chunk in _chunks(rows):
        res = (
            sb.table("products")
            .upsert(chunk, on_conflict="store_id,fingerprint")
            .execute()
        )
        for row in res.data or []:
            mapping[row["fingerprint"]] = row["id"]

    missing = {r["fingerprint"] for r in rows} - set(mapping)
    if missing:
        # upsert bazi satirlari dondurmezse (ornegin degisiklik yoksa) tamamla
        log.debug("%d urun icin id yeniden sorgulaniyor", len(missing))
        missing_list = list(missing)
        for i in range(0, len(missing_list), CHUNK_SIZE):
            batch = missing_list[i : i + CHUNK_SIZE]
            res = (
                sb.table("products")
                .select("id,fingerprint")
                .eq("store_id", store_id)
                .in_("fingerprint", batch)
                .execute()
            )
            for row in res.data or []:
                mapping[row["fingerprint"]] = row["id"]

    log.info("%d urun eslendi", len(mapping))
    return mapping


def upsert_prices(sb: Client, mapping: dict[str, int],
                  items: Iterable[ScrapedProduct],
                  scrape_date: date | None = None) -> int:
    scrape_date = scrape_date or today_tr()
    rows: list[dict] = []
    for item in items:
        product_id = mapping.get(item.fingerprint)
        if product_id is None:
            log.warning("product_id bulunamadi, fiyat atlandi: %s", item.name)
            continue
        rows.append(item.price_row(product_id, scrape_date))

    written = 0
    for chunk in _chunks(rows):
        res = (
            sb.table("prices")
            .upsert(chunk, on_conflict="product_id,scrape_date")
            .execute()
        )
        written += len(res.data or chunk)

    log.info("%d fiyat kaydi yazildi", written)
    return written


def save_all(items: list[ScrapedProduct], *, store_slug: str, store_name: str,
             store_website: str | None = None,
             scrape_date: date | None = None) -> int:
    sb = get_client()
    store_id = get_or_create_store(sb, store_slug, store_name, store_website)
    mapping = upsert_products(sb, store_id, items)
    return upsert_prices(sb, mapping, items, scrape_date)
