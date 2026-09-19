# Aktüel Ürünler ve Fiyat Karşılaştırma Sistemi

MVP: BİM aktüel ürünlerini toplayıp Supabase'e zaman serisi olarak yazar.

## Kurulum

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env        # SUPABASE_URL ve SUPABASE_SERVICE_KEY doldur
```

## Veritabanı

Supabase SQL Editor'de şema dosyasını çalıştır (stores / products / prices).
`unit_raw` sütunu gerekli:

```sql
alter table public.products add column if not exists unit_raw text;
```

## Çalıştırma

```bash
python tools/inspect_bim.py          # 1) selektörleri doğrula
python -m src.main --dry-run         # 2) DB'ye yazmadan dene
python -m src.main                   # 3) aktif katalogu kaydet
python -m src.main --all-catalogs    # tüm sekmeleri gez
python -m src.main --only-temel-gida # sadece temel gıda
python -m pytest                     # testler
```

## Notlar

- Sayfa server-side render ediliyor, Selenium gerekmiyor.
- `REQUEST_DELAY` 1.5 sn. Aşağı çekme.
- `data/raw/` altındaki HTML'ler debug içindir, git'e girmez.
- BİM slug'ları katalog dönemini taşır (`...-7-9`), bu yüzden ürün eşleştirme
  marka+ad+gramajdan üretilen `fingerprint` ile yapılır.
