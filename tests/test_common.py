"""Ortak yardimcilarin testleri.

Buradaki urun adlari uydurma degil: Supabase'deki gercek SOK verisinden
alindi. Anahtar kelime listesi degisirse bu vakalar korunmali.
"""
from src.scrapers.common import guess_category
from src.scrapers.sok import split_title_unit


def test_toz_seker_temel_malzeme_yumusak_seker_atistirmalik():
    # Cok kelimeli ifade ("toz seker") tek kelimeyi ("seker") yener
    for ad in ("Altınküp Toz Şeker", "Altınküp Küp Şeker",
               "Altınküp Esmer Küp Şeker", "Piyale Pudra Şekeri"):
        assert guess_category(None, ad) == "temel-malzeme", ad
    for ad in ("Ülker Yupo Şampiyon Yumuşak Şeker",
               "Uzungil Karışık Meyve Aromalı Akide Şekeri"):
        assert guess_category(None, ad) == "atistirmalik", ad


def test_tuzlu_ve_unlu_tuz_ve_un_ile_eslesmez():
    # Kelime siniri sayesinde "tuzlu" != "tuz", "unlu" != "un"
    assert guess_category(None, "Amigo Tuzlu Fıstık") == "atistirmalik"
    assert guess_category(None, "Mcvitie's Kakaolu Tam Buğday Unlu Bisküvi") == "atistirmalik"
    assert guess_category(None, "Billur Sofra Tuzu") == "temel-malzeme"
    assert guess_category(None, "Söke Un") == "bakliyat-tahil"


def test_pul_biber_sebze_degil_baharat():
    assert guess_category(None, "Bağdat Pul Biber") == "temel-malzeme"


def test_kiloyla_satilan_urun():
    assert split_title_unit("Tuzlu Kabuklu Fıstık Kg") == ("Tuzlu Kabuklu Fıstık", "1 kg")
    assert split_title_unit("Domates Kg") == ("Domates", "1 kg")
    assert split_title_unit("Avokado Adet") == ("Avokado", "1 adet")
