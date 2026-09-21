"""Eslestirme testleri.

Buradaki ciftler UYDURMA DEGIL: Supabase'deki trigram benzerlik sorgusunun
gercek ciktisindan alindi. Her biri icin dogru cevabi elle belirledik.
"""
from decimal import Decimal

from src.matching import Product, compare, find_matches, to_base


def bim(name, amount, unit, brand=None, pid=1):
    return Product(id=pid, store="bim", name=name, brand=brand,
                   unit_amount=amount, unit_type=unit)


def sok(name, amount, unit, pid=1001):
    return Product(id=pid, store="sok", name=name,
                   unit_amount=amount, unit_type=unit)


# --- Birim ------------------------------------------------------------------
def test_birimler_ortaklasir():
    assert to_base(1, "l") == to_base(1000, "ml")
    assert to_base(2.5, "kg") == to_base(2500, "g")
    assert to_base(1, "L") == ("ml", Decimal("1000"))
    assert to_base(None, "g") is None


# --- Dogru eslesmeler ---------------------------------------------------------
def test_ayni_isim_ayni_urun():
    v = compare(bim("Tam Yağlı Süzme Peynir", 500, "g", brand="İçim"),
                sok("İçim Tam Yağlı Süzme Peynir", 500, "g"))
    assert v.ok, v


def test_bim_markasi_bos_ise_ismin_ilk_kelimesi():
    v = compare(bim("Ülker Hobby Fındıklı Bar", 250, "g"),
                sok("Ülker Hobby Fındıklı Bar", 250, "g"))
    assert v.ok, v


def test_yazim_farkli_marka_ve_kapli_kaplamali():
    # Kivrik kesme isareti (’), "McVitie's" / "Mcvities", "Kaplı" / "Kaplamalı"
    v = compare(bim("Sütlü Çikolata  Kaplı Bisküvi", 150, "g", brand="Ülker McVitie’s"),
                sok("Mcvities Sütlü Çikolata Kaplamalı Bisküvi", 150, "g"))
    assert v.ok, v


def test_sok_ismi_kisaltmissa_kapsama_sentetik():
    v = compare(bim("Magma Çikolatalı Sos Dolgulu Kek", 65, "g", brand="Dankek"),
                sok("Dankek Magma Çikolatalı", 65, "g"))
    assert v.ok and v.reason == "kapsama", v


def test_1_litre_ile_1000_ml_ayni():
    v = compare(bim("Orman Meyveli Kefir", 1, "l", brand="İçim"),
                sok("İçim Orman Meyveli Kefir", 1000, "ml"))
    assert v.ok, v


# --- Dogru elemeler -----------------------------------------------------------
def test_farkli_marka_benzerlik_yuksek_olsa_bile_elenir():
    # SQL'de 0.71 alip gercek eslesmelerden YUKSEK puan almisti
    v = compare(bim("Orman Meyveli Kefir", 1, "l", brand="İçim"),
                sok("Mis Kefir Orman Meyveli", 1, "l"))
    assert not v.ok and v.reason == "marka farkli", v


def test_farkli_marka_krem_peynir_ve_kraker():
    assert compare(bim("Krem Peynir", 300, "g", brand="Aknaz"),
                   sok("Mis Krem Peynir", 300, "g")).reason == "marka farkli"
    assert compare(bim("Çubuk Kraker", 40, "g", brand="Atıştır"),
                   sok("Ülker Çubuk Kraker", 40, "g")).reason == "marka farkli"


def test_laktozsuz_farkli_urun():
    v = compare(bim("Protein Süt", 1, "l", brand="Sek"),
                sok("Sek Protein Laktozsuz Süt", 1, "l"))
    assert not v.ok and "laktozsuz" in v.reason, v


def test_sebzeli_ve_tam_bugday_farkli():
    v = compare(bim("Sebzeli  Burgu Makarna", 350, "g", brand="Filiz"),
                sok("Filiz Tam Buğday Burgu Makarna", 350, "g"))
    assert not v.ok and v.reason.startswith("cesit farkli"), v


def test_yag_orani_farkli():
    v = compare(bim("%1,5 Yağlı Süt", 1, "l", brand="İçim"),
                sok("İçim %3 Yağlı Süt", 1, "l"))
    assert not v.ok and v.reason == "sayi farkli", v


def test_cesitleri_bire_bir_eslesmez():
    v = compare(bim("Makarna Çeşitleri", 500, "g", brand="Barilla"),
                sok("Barilla Fusilli (Burgu) Makarna", 500, "g"))
    assert not v.ok and v.reason == "cesitleri", v


def test_ayni_marka_farkli_urun():
    v = compare(bim("Kinder Suprise Yumurta", 20, "g"),
                sok("Kinder Joy Yumurta Çikolata", 20, "g"))
    assert not v.ok, v


# --- Butun akis ---------------------------------------------------------------
def test_find_matches_en_iyi_adayi_secer_ve_bloklar():
    products = [
        bim("Tam Yağlı Süzme Peynir", 500, "g", brand="İçim", pid=1),
        bim("Orman Meyveli Kefir", 1, "l", brand="İçim", pid=2),
        sok("İçim Tam Yağlı Süzme Peynir", 500, "g", pid=101),
        sok("Mis Tam Yağlı Tost Peynir", 500, "g", pid=102),   # ayni gramaj, farkli marka
        sok("Mis Kefir Orman Meyveli", 1, "l", pid=103),       # tuzak
        sok("İçim Tam Yağlı Süzme Peynir", 250, "g", pid=104), # farkli gramaj
    ]
    matches, reasons = find_matches(products)
    pairs = {(m.a.id, m.b.id) for m in matches}
    assert pairs == {(1, 101)}
    # 250 g'lik urun bloklama sayesinde hic karsilastirilmadi
    assert sum(reasons.values()) == 3


# --- --explain ile gercek veride bulunan vakalar ------------------------------
def test_gercek_veri_marka_alani_urun_hattini_iceriyor():
    """BIM markayi 'Dankek Magma' olarak veriyor (ilk testteki varsayimim
    'Dankek' idi; test gecti ama gercek eslesme kacti)."""
    v = compare(bim("Çikolatalı Sos Dolgulu Kek", 65, "g", brand="Dankek Magma"),
                sok("Dankek Magma Çikolatalı", 65, "g"))
    assert v.ok and v.reason == "kapsama", v


def test_ayni_urun_hatti_farkli_ana_marka():
    """'magma' kelimesi ortak diye Ulker, Dankek'le eslesmemeli."""
    v = compare(bim("Çikolatalı Sos Dolgulu Kek", 65, "g", brand="Dankek Magma"),
                sok("Ülker Magma Çikolatalı", 65, "g"))
    assert not v.ok and v.reason == "marka farkli", v


def test_suzme_ve_beyaz_peynir_farkli():
    v = compare(bim("Tam Yağlı Süzme Peynir", 500, "g", brand="İçim"),
                sok("İçim Tam Yağlı Beyaz Peynir", 500, "g"))
    assert not v.ok and "beyaz" in v.reason, v


def test_iki_kelimelik_isim_kapsamaya_girmez():
    v = compare(bim("Süt", 1, "l", brand="Mis"),
                sok("Mis Laktozsuz Süt", 1, "l"))
    assert not v.ok, v
    v = compare(bim("Süt Kutu Ekonomik Paket", 1, "l", brand="Mis"),
                sok("Mis Süt", 1, "l"))
    assert not v.ok, v
