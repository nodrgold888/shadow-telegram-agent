from __future__ import annotations

import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

MAX_BYTES = 50 * 1024 * 1024
TIMEOUT_SECONDS = 120
_DOWNLOAD_SLOTS = asyncio.Semaphore(2)
EXIT_LOGIN_REQUIRED = 2
EXIT_TOO_LARGE = 3
log = logging.getLogger("shadow.video")


class VideoDownloadError(Exception):
    pass


def find_video_url(text: str) -> str | None:
    for candidate in re.findall(r"https?://[^\s<>]+", text):
        candidate = candidate.rstrip(".,!?;:)]}»”'\"")
        try:
            parsed = urlsplit(candidate)
            if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
                continue
            host = (parsed.hostname or "").lower()
            path = parsed.path
        except ValueError:
            continue
        if host in {"instagram.com", "www.instagram.com", "m.instagram.com"} and re.match(
            # /reel/ID, /p/ID, /tv/ID and the profile-prefixed /<user>/reel/ID form.
            r"^(?:/[A-Za-z0-9._]+)?/(?:reel|reels|p|tv)/[A-Za-z0-9_-]+/?$", path
        ):
            return candidate
        if host in {"tiktok.com", "www.tiktok.com", "m.tiktok.com"} and (
            re.match(r"^/@[^/]+/video/\d+/?$", path)
            or re.match(r"^/t/[A-Za-z0-9]+/?$", path)
        ):
            return candidate
        if host in {"vm.tiktok.com", "vt.tiktok.com"} and re.match(r"^/[A-Za-z0-9]+/?$", path):
            return candidate
    return None


def _worker(url: str, directory: str) -> int:
    from yt_dlp import YoutubeDL
    from yt_dlp.extractor.instagram import InstagramIE
    from yt_dlp.extractor.tiktok import TikTokIE, TikTokVMIE
    from yt_dlp.utils import DownloadError

    if find_video_url(url) != url:
        return 1
    target = Path(directory)

    def enforce_size(_: dict) -> None:
        total = sum(p.stat().st_size for p in target.iterdir() if p.is_file())
        if total > MAX_BYTES:
            raise DownloadError("video_size_limit")

    params = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "playlist_items": "1",
        "format": "best[ext=mp4]",
        "outtmpl": str(target / "video.%(ext)s"),
        "max_filesize": MAX_BYTES,
        "socket_timeout": 15,
        "retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 1,
        "progress_hooks": [enforce_size],
        "cachedir": False,
        "overwrites": False,
        "postprocessors": [],
    }
    try:
        # Do not load generic extractors, account cookies or local configuration.
        with YoutubeDL(params, auto_init=False) as downloader:
            for extractor in (InstagramIE(), TikTokIE(), TikTokVMIE()):
                downloader.add_info_extractor(extractor)
            downloader.download([url])
        video = target / "video.mp4"
        return 0 if video.is_file() and 0 < video.stat().st_size <= MAX_BYTES else 1
    except DownloadError as exc:
        message = str(exc).lower()
        if any(word in message for word in ("login", "log in", "private", "cookies", "authentication")):
            return EXIT_LOGIN_REQUIRED
        if "video_size_limit" in message or "max-filesize" in message:
            return EXIT_TOO_LARGE
        return 1
    except Exception:
        return 1


@asynccontextmanager
async def downloaded_video(url: str):
    if find_video_url(url) != url:
        raise VideoDownloadError("Bu video havolasi qo‘llab-quvvatlanmaydi.")
    async with _DOWNLOAD_SLOTS:
        with TemporaryDirectory(prefix="shadow-video-") as directory:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "shadow.video_download", "--worker", url, directory,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                try:
                    await asyncio.wait_for(process.wait(), timeout=TIMEOUT_SECONDS)
                except asyncio.TimeoutError as exc:
                    raise VideoDownloadError("Video yuklash vaqti tugadi. Keyinroq qayta urinib ko‘ring.") from exc
                if process.returncode == EXIT_LOGIN_REQUIRED:
                    raise VideoDownloadError("Bu video yopiq yoki kirish talab qiladi. Faqat ochiq (hamma ko‘ra oladigan) videolarni yuklay olaman.")
                if process.returncode == EXIT_TOO_LARGE:
                    raise VideoDownloadError("Video 50 MB dan katta, Telegram orqali yubora olmayman.")
                if process.returncode != 0:
                    log.warning("Video worker failed with exit code %s", process.returncode)
                    raise VideoDownloadError("Videoni yuklab bo‘lmadi. Havola ochiq video bo‘lishi kerak. Sayt kirishni cheklagan yoki video 50 MB dan katta bo‘lishi mumkin.")
                video = Path(directory) / "video.mp4"
                if not video.is_file() or not 0 < video.stat().st_size <= MAX_BYTES:
                    raise VideoDownloadError("Video topilmadi yoki hajmi 50 MB dan katta.")
                yield video
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--worker":
        raise SystemExit(1)
    raise SystemExit(_worker(sys.argv[2], sys.argv[3]))
