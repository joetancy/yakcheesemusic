"""Browser-driven provider for flac.music.hi.cn.

This uses the site's visible search and download controls. It intentionally
does not call its internal endpoints or reproduce its challenge/session flow.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from app.downloaders.base import DownloaderBase, SearchResult

log = logging.getLogger("yakcheesemusic")
SITE_URL = "https://flac.music.hi.cn/"
PLATFORMS = (("kuwo", "K"), ("wyy", "W"))


def _seconds(value) -> int | None:
    try:
        if isinstance(value, str) and ":" in value:
            mins, secs = value.split(":", 1)
            return int(mins) * 60 + int(secs)
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _browser():
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    except Exception:
        pw.stop()
        raise
    return pw, browser


def _open_site(browser):
    context = browser.new_context(accept_downloads=True)
    page = context.new_page()
    page.goto(SITE_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        page.get_by_role("button", name="我知道了", exact=True).click(timeout=1500)
    except Exception:
        pass
    page.locator('.search-form input.ant-input[type="text"]').wait_for(
        state="visible", timeout=30000
    )
    return context, page


def _choose_platform(page, platform: str) -> None:
    label = dict(PLATFORMS).get(platform)
    if not label:
        raise ValueError(f"Unsupported FLAC Music platform: {platform}")
    page.locator(".platform-select").click()
    page.locator(".ant-select-item-option").filter(has_text=label).click()


def _search_page(page, keyword: str, platform: str) -> list[dict]:
    _choose_platform(page, platform)
    with page.expect_response(
        lambda response: "ajax.php?act=search" in response.url
        and response.request.method == "POST",
        timeout=45000,
    ) as response_info:
        page.locator('.search-form input.ant-input[type="text"]').fill(keyword)
        page.locator('.search-form button[type="submit"]').click()
    payload = response_info.value.json()
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("msg") or "FLAC Music search failed")
    return ((payload.get("data") or {}).get("list") or [])


def _to_result(item: dict, platform: str) -> SearchResult | None:
    song_id = item.get("id")
    title = (item.get("name") or "").strip()
    if song_id is None or not title:
        return None
    duration = _seconds(item.get("duration") or item.get("time"))
    formats = []
    if item.get("hasSQ"):
        formats.append("FLAC available (SQ)")
    if item.get("hasHQ"):
        formats.append("320 kbps available (HQ)")
    return SearchResult(
        provider="flac_music",
        provider_track_id=f"{platform}:{song_id}",
        title=title,
        artist=(item.get("artist") or "").strip(),
        album=(item.get("album_name") or "").strip(),
        duration_ms=duration * 1000 if duration is not None else None,
        quality="; ".join(formats) or None,
        # The site chooses the file format after the user-visible download
        # options are opened. Preserve the real returned extension below.
        format=None,
        source_url=SITE_URL,
    )


class FlacMusicDownloader(DownloaderBase):
    name = "flac_music"

    def search(
        self, title: str, artist: str, album: str = "", query: str | None = None
    ) -> list[SearchResult]:
        keyword = query or " ".join(x for x in (title, artist) if x)
        if not keyword.strip():
            return []
        pw, browser = _browser()
        try:
            _, page = _open_site(browser)
            results = []
            for platform, _ in PLATFORMS:
                for item in _search_page(page, keyword, platform):
                    result = _to_result(item, platform)
                    if result:
                        results.append(result)
            return results[:20]
        finally:
            browser.close()
            pw.stop()

    def download(self, result: SearchResult, dest_dir: str) -> str:
        try:
            platform, song_id = result.provider_track_id.split(":", 1)
        except ValueError as exc:
            raise ValueError("Invalid FLAC Music candidate ID") from exc
        os.makedirs(dest_dir, exist_ok=True)
        pw, browser = _browser()
        try:
            context, page = _open_site(browser)
            entries = _search_page(
                page, " ".join(x for x in (result.title, result.artist) if x), platform
            )
            entry = next((x for x in entries if str(x.get("id")) == song_id), None)
            if not entry:
                raise RuntimeError("FLAC Music no longer lists this track; search again")

            row = page.locator("article.song-item").filter(has_text=result.title)
            if result.artist:
                row = row.filter(has_text=result.artist)
            row.first.locator('button[aria-label="下载"]').click()
            options = page.locator(".download-options")
            options.wait_for(state="visible", timeout=20000)

            buttons = options.locator("button")
            count = buttons.count()
            if not count:
                raise RuntimeError("FLAC Music returned no downloadable quality options")
            # The site lists its best available option first. Prefer lossless,
            # otherwise 320 kbps, then the first option it actually offers.
            flac = buttons.filter(has_text="FLAC")
            mp3_320 = buttons.filter(has_text="320K")
            choice = flac.first if flac.count() else (mp3_320.first if mp3_320.count() else buttons.first)
            choice.click()
            detail = page.locator(".download-detail")
            detail.wait_for(state="visible", timeout=45000)
            # The anchor's download icon has aria-label="download", which is
            # included in its accessible name. Match the visible link text
            # instead of an exact accessible name.
            link = detail.locator("a").filter(has_text="点击下载")
            link.wait_for(state="visible", timeout=15000)
            download_url = link.get_attribute("href")
            if not download_url:
                raise RuntimeError("FLAC Music did not provide a download link")

            # Fetch the link exposed by the site's visible download panel using
            # the same browser context so cookies and redirects are preserved.
            response = context.request.get(
                download_url,
                timeout=120000,
                headers={"Referer": SITE_URL},
            )
            if not response.ok:
                raise RuntimeError(f"FLAC Music download failed: HTTP {response.status}")
            body = response.body()
            if not body:
                raise RuntimeError("FLAC Music returned an empty audio file")
            detail_text = detail.inner_text()
            ext = ".flac" if "FLAC" in detail_text.upper() else ".mp3"
            safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", song_id)
            path = Path(dest_dir) / f"flacmusic-{safe_id}{ext}"
            path.write_bytes(body)
            result.format = ext.lstrip(".")
            result.quality = detail_text
            return str(path)
        finally:
            browser.close()
            pw.stop()
