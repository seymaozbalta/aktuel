from datetime import date
from decimal import Decimal

from src.scrapers.bim import (
    make_fingerprint,
    parse_catalog_label,
    parse_price,
    parse_unit,
)


def test_parse_price_bim_format():
    assert parse_price("79, 00 \u20ba") == Decimal("79.00")
    assert parse_price("9, 50 \u20ba") == Decimal("9.50")
    assert parse_price("2.890 TL") == Decimal("2890")
    assert parse_price("1.234,56 \u20ba") == Decimal("1234.56")
    assert parse_price("bos") is None


def test_parse_unit():
    assert parse_unit("6x180 ml") == (Decimal("1080"), "ml")
    assert parse_unit("2,5 kg") == (Decimal("2.5"), "kg")
    assert parse_unit("1 L") == (Decimal("1"), "l")
    assert parse_unit("700 g 20-24 cm") == (Decimal("700"), "g")
    assert parse_unit("") == (None, None)


def test_parse_catalog_label():
    today = date(2026, 9, 19)
    assert parse_catalog_label("08 Eyl\u00fcl Sal\u0131", today)[0] == date(2026, 9, 8)
    start, end = parse_catalog_label("01-28 Eyl\u00fcl", today)
    assert (start, end) == (date(2026, 9, 1), date(2026, 9, 28))


def test_fingerprint_is_case_and_accent_stable():
    a = make_fingerprint("bim", "\u0130\u00c7\u0130M", "\u00c7\u0130LEKL\u0130 S\u00dcT", "6x180 ml")
    b = make_fingerprint("bim", "i\u00e7im", "\u00c7ilekli S\u00fct", "6x180 ml")
    assert a == b


def test_gramaj_sayisi_fiyata_karismaz():
    """Kart metninin tamaminda '6x180 ml 79, 00' -> 18079 olmamali."""
    from src.scrapers.bim import find_prices
    assert find_prices("\u0130\u00c7\u0130M \u2022 6x180 ml 79, 00 \u20ba") == [Decimal("79.00")]
    assert find_prices("\u2022 2500 g 185, 00 \u20ba") == [Decimal("185.00")]
    assert find_prices("2.890, 00 \u20ba") == [Decimal("2890.00")]


def test_gercek_dom_yapisi():
    from pathlib import Path
    from src.scrapers.bim import BimScraper

    html = Path("tests/fixtures/bim_sample.html").read_text(encoding="utf-8")
    items = BimScraper.__new__(BimScraper).parse(html, source_url="x")
    assert len(items) == 3
    first = items[0]
    assert first.brand == "\u0130\u00c7\u0130M\u0130NO"
    assert first.name == "\u00c7\u0130LEKL\u0130 S\u00dcT"
    assert first.price == Decimal("79.00")
    assert first.unit_amount == Decimal("1080") and first.unit_type == "ml"
    assert items[1].variants == ["Kalem", "Spagetti"]
    assert items[2].brand is None


def test_kategori_kelime_siniri_gozetir():
    """'su' -> 'SUshida', 'kola' -> 'ciKOLAta' eslesmemeli."""
    from src.scrapers.bim import guess_category

    assert guess_category("Sushida Dardanel", "California Maki") != "icecek"
    assert guess_category("\u00dclker", "S\u00fctl\u00fc \u00c7ikolata Kapl\u0131 Bisk\u00fcvi") == "atistirmalik"
    assert guess_category("Lipton", "Siyah \u00c7ay") == "icecek"
    assert guess_category("KOM\u0130L\u0130", "R\u0130V\u0130ERA ZEYT\u0130NYA\u011eI") == "yag-sos-salca"
