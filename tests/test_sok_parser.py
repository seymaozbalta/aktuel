from decimal import Decimal
from pathlib import Path

from src.scrapers.sok import SokScraper, split_title_unit


def _parse():
    html = Path("tests/fixtures/sok_sample.html").read_text(encoding="utf-8")
    return SokScraper.__new__(SokScraper).parse(html, source_url="x",
                                                category="sut-urunleri")


def test_baslik_ve_gramaj_ayrilir():
    assert split_title_unit("Mis Pastörize Günlük Süt 1 L") == \
        ("Mis Pastörize Günlük Süt", "1 L")
    assert split_title_unit("İçim %1,5 Yağlı Süt 6x200 ml") == \
        ("İçim %1,5 Yağlı Süt", "6x200 ml")
    assert split_title_unit("Ahmad Tea Earl Grey Demlik Poşet 40'lı") == \
        ("Ahmad Tea Earl Grey Demlik Poşet 40'lı", None)


def test_gercek_kart():
    items = _parse()
    assert len(items) == 3                  # kategori linki atlandi
    mis = items[0]
    assert mis.external_id == "6044"
    assert mis.name == "Mis Pastörize Günlük Süt"
    assert mis.unit_raw == "1 L" and mis.unit_amount == Decimal("1")
    assert mis.price == Decimal("59.00")
    assert mis.old_price is None
    assert mis.image_url and "ceptesok" in mis.image_url
    assert mis.campaign_start is None       # raf fiyati, kampanya degil


def test_hash_degisse_de_calisir_ve_indirim_okunur():
    yogurt = _parse()[1]
    assert yogurt.name == "Sütaş Kaymaksız Yoğurt"
    assert yogurt.price == Decimal("25.90")
    assert yogurt.old_price == Decimal("32.50")


def test_coklu_paket_ve_query_string():
    icim = _parse()[2]
    assert icim.external_id == "9911"
    assert icim.unit_amount == Decimal("1200") and icim.unit_type == "ml"
    assert icim.product_url.endswith("-p-9911")   # ?ref= temizlendi


def test_fingerprint_urun_numarasina_bagli():
    """SOK urunun adini degistirse bile fiyat gecmisi kopmamali."""
    a = _parse()[0]
    html = Path("tests/fixtures/sok_sample.html").read_text(encoding="utf-8")
    html = html.replace("Mis Pastörize Günlük Süt 1 L", "Mis Günlük Süt Yeni Ambalaj 1 L")
    b = SokScraper.__new__(SokScraper).parse(html, source_url="x")[0]
    assert a.name != b.name
    assert a.fingerprint == b.fingerprint


def test_sayfalama_bos_sayfada_durur():
    html = Path("tests/fixtures/sok_sample.html").read_text(encoding="utf-8")
    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        # 1. sayfa 3 urun; 2. sayfa AYNI icerik (site sona gelince tekrar ediyor)
        return html

    s = SokScraper.__new__(SokScraper)
    s.fetch = fake_fetch
    items = s.scrape_category("/sut-ve-sut-urunleri-c-460", "sut-urunleri")
    assert len(items) == 3
    assert len(calls) == 2          # 2. sayfada yeni urun yok -> durdu
    assert calls[1].endswith("?page=2")


def test_sayfalama_kisa_sayfada_ekstra_istek_atmaz():
    full = Path("tests/fixtures/sok_sample.html").read_text(encoding="utf-8")
    # 2. sayfa: sadece 1 urun, farkli numarayla -> son sayfa
    short = full.split("<!-- INDIRIMLI")[0].replace("p-6044", "p-7777") + "</div></body></html>"
    pages = {1: full, 2: short}
    calls = []

    def fake_fetch(url, **kw):
        n = int(url.split("page=")[1]) if "page=" in url else 1
        calls.append(n)
        return pages.get(n, "")

    s = SokScraper.__new__(SokScraper)
    s.fetch = fake_fetch
    items = s.scrape_category("/x-c-1", None)
    assert len(items) == 4
    assert calls == [1, 2]          # 3. sayfayi istemedi
