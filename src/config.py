"""Ortam degiskenleri ve proje sabitleri."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- Supabase -------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# --- Scraper davranisi ----------------------------------------------------
# Kibar ol: istekler arasi bekleme (saniye). Asagi cekme.
REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "1.5"))
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
)

# --- BIM ------------------------------------------------------------------
BIM_BASE_URL = "https://www.bim.com.tr"
BIM_AKTUEL_PATH = "/categories/100/aktuel-urunler.aspx"

# --- SOK ------------------------------------------------------------------
SOK_BASE_URL = "https://www.sokmarket.com.tr"
# Bir kategoride en fazla kac sayfa gezilsin (sonsuz donguye karsi sigorta).
# Sayfa basi 20 urun -> 30 sayfa = 600 urun/kategori.
SOK_MAX_PAGES = int(os.getenv("SOK_MAX_PAGES", "30"))

# --- Dosya yollari --------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
SAVE_RAW_HTML = os.getenv("SAVE_RAW_HTML", "1") == "1"

# Yazma islemlerinin parca boyutu (Supabase tek istekte cok satir sevmiyor)
CHUNK_SIZE = 200


def today_tr() -> date:
    """Istanbul saatine gore bugun.

    GitHub Actions UTC calisir; date.today() gece yarisi civarinda
    yanlis gun uretir. prices.scrape_date ile tutarli olmasi icin
    her yerde bu fonksiyon kullanilmali.
    """
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc) + timedelta(hours=3)).date()
