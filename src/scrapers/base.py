"""Tum scraper'larin ortak atasi.

Buradaki tek is: kibar ve dayanikli HTTP. Ikinci marketi eklerken
sadece collect() ve parse() yazacaksin.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..config import (
    MAX_RETRIES,
    RAW_DIR,
    REQUEST_DELAY,
    REQUEST_TIMEOUT,
    SAVE_RAW_HTML,
    USER_AGENT,
)
from ..models import ScrapedProduct

log = logging.getLogger(__name__)


class BaseScraper(ABC):
    store_slug: str = ""
    store_name: str = ""
    store_website: str | None = None

    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "tr-TR,tr;q=0.9",
                "Accept": "text/html,application/xhtml+xml",
            }
        )
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_at = time.monotonic()

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=2, min=2, max=20),
        retry=retry_if_exception_type(requests.RequestException),
        reraise=True,
    )
    def fetch(self, url: str, *, tag: str = "page", save: bool = True) -> str:
        """Sayfayi indirir, istege bagli olarak ham HTML'i diske yazar.

        save=False: sayfalama yapan scraper'larda her sayfayi diske yazmak
        yuzlerce MB'a cikar. Genelde kategorinin ilk sayfasini saklamak
        hata ayiklamak icin yeterli.
        """
        self._throttle()
        log.info("GET %s", url)
        resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text

        if SAVE_RAW_HTML and save:
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            path = RAW_DIR / f"{self.store_slug}_{tag}_{stamp}.html"
            path.write_text(html, encoding="utf-8")
            log.debug("ham HTML kaydedildi: %s", path)

        return html

    @abstractmethod
    def collect(self, *, all_catalogs: bool = False) -> list[ScrapedProduct]:
        """Marketin sayfalarini gezip dogrulanmis urun listesi dondurur."""
        raise NotImplementedError
