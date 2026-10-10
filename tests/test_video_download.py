import asyncio
from pathlib import Path

import pytest

from shadow import video_download as videos


@pytest.mark.parametrize("url", [
    "https://www.instagram.com/reel/AbC_123/",
    "https://www.instagram.com/p/AbC-123/?igsh=abc",
    "https://www.tiktok.com/@user/video/1234567890",
    "https://vm.tiktok.com/ZAbc123/",
    "https://vt.tiktok.com/ZAbc123/",
    "https://www.tiktok.com/t/ZAbc123/",
    "https://www.instagram.com/some.user_1/reel/AbC_123/",
    "https://www.instagram.com/some.user_1/p/AbC-123/?igsh=abc",
])
def test_supported_video_links(url):
    assert videos.find_video_url("Video: " + url) == url


@pytest.mark.parametrize("url", [
    "https://instagram.com.attacker.example/reel/123/",
    "https://instagram.com@127.0.0.1/reel/123/",
    "https://user:secret@instagram.com/reel/123/",
    "https://www.instagram.com:8443/reel/123/",
    "http://www.instagram.com/reel/123/",
    "https://www.instagram.com/some_profile/",
    "https://www.tiktok.com/@user",
    "https://127.0.0.1/video/123/",
])
def test_rejects_untrusted_hosts_and_non_video_links(url):
    assert videos.find_video_url(url) is None


def test_download_error_cleans_temporary_files(monkeypatch):
    directories = []

    class FailedProcess:
        returncode = 1
        async def wait(self):
            return 1

    async def spawn(*args, **kwargs):
        directories.append(Path(args[-1]))
        (directories[-1] / "video.mp4.part").write_bytes(b"partial")
        return FailedProcess()

    monkeypatch.setattr(videos.asyncio, "create_subprocess_exec", spawn)

    async def run():
        with pytest.raises(videos.VideoDownloadError):
            async with videos.downloaded_video("https://www.instagram.com/reel/ABC/"):
                pytest.fail("Failed downloads must not be uploaded")
    asyncio.run(run())
    assert directories
    assert not directories[0].exists()


def test_success_file_exists_only_inside_context(monkeypatch):
    class SuccessProcess:
        returncode = 0
        async def wait(self):
            return 0

    async def spawn(*args, **kwargs):
        (Path(args[-1]) / "video.mp4").write_bytes(b"test-video")
        return SuccessProcess()
    monkeypatch.setattr(videos.asyncio, "create_subprocess_exec", spawn)

    async def run():
        async with videos.downloaded_video("https://www.instagram.com/reel/ABC/") as path:
            assert path.is_file()
        assert not path.exists()
    asyncio.run(run())


def _run_failing_download(monkeypatch, code):
    class Process:
        returncode = code
        async def wait(self):
            return code

    async def spawn(*args, **kwargs):
        return Process()
    monkeypatch.setattr(videos.asyncio, "create_subprocess_exec", spawn)

    async def run():
        async with videos.downloaded_video("https://www.instagram.com/reel/ABC/"):
            pytest.fail("Failed downloads must not be uploaded")
    with pytest.raises(videos.VideoDownloadError) as caught:
        asyncio.run(run())
    return str(caught.value)


def test_private_video_gets_specific_message(monkeypatch):
    assert "yopiq" in _run_failing_download(monkeypatch, videos.EXIT_LOGIN_REQUIRED)


def test_too_large_video_gets_specific_message(monkeypatch):
    assert "50 MB" in _run_failing_download(monkeypatch, videos.EXIT_TOO_LARGE)


def test_other_failures_are_logged_with_the_exit_code(monkeypatch, caplog):
    with caplog.at_level("WARNING", logger="shadow.video"):
        assert "yuklab bo" in _run_failing_download(monkeypatch, 1)
    assert "exit code 1" in caplog.text


def test_tiktok_needs_browser_impersonation_so_curl_cffi_ships_with_yt_dlp():
    from pathlib import Path
    requirements = (Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "yt-dlp[curl-cffi]==" in requirements
