"""Scraper ile veritabani arasindaki sozlesme.

Parser bu modeli dondurmek zorunda; boylece ikinci marketi eklediginde
db.py'de tek satir bile degistirmen gerekmiyor.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class ScrapedProduct(BaseModel):
    # Kimlik
    fingerprint: str
    external_id: str | None = None

    # Urun bilgisi
    name: str
    brand: str | None = None
    category: str | None = None
    unit_raw: str | None = None          # "6x180 ml" -> ham metin
    unit_amount: Decimal | None = None   # 1080
    unit_type: str | None = None         # "ml"
    image_url: str | None = None
    product_url: str | None = None

    # Fiyat bilgisi
    price: Decimal = Field(gt=0)
    old_price: Decimal | None = None
    campaign_start: date | None = None
    campaign_end: date | None = None
    source_url: str | None = None

    # Ayiklanamayan ekstra satirlar (varyantlar vb.) - debug icin
    variants: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("urun adi bos olamaz")
        return v

    def product_row(self, store_id: int) -> dict:
        """products tablosuna yazilacak sozluk."""
        return {
            "store_id": store_id,
            "external_id": self.external_id,
            "fingerprint": self.fingerprint,
            "name": self.name,
            "brand": self.brand,
            "category": self.category,
            "unit_raw": self.unit_raw,
            "unit_amount": float(self.unit_amount) if self.unit_amount is not None else None,
            "unit_type": self.unit_type,
            "image_url": self.image_url,
            "product_url": self.product_url,
        }

    def price_row(self, product_id: int, scrape_date: date) -> dict:
        """prices tablosuna yazilacak sozluk."""
        return {
            "product_id": product_id,
            "price": float(self.price),
            "old_price": float(self.old_price) if self.old_price is not None else None,
            "currency": "TRY",
            "campaign_start": self.campaign_start.isoformat() if self.campaign_start else None,
            "campaign_end": self.campaign_end.isoformat() if self.campaign_end else None,
            "source_url": self.source_url,
            "scrape_date": scrape_date.isoformat(),
        }
