from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Optional
from urllib.parse import unquote, urlparse

import requests
import urllib.error
import urllib.request


class DownloadError(RuntimeError):
    """Raised when a PDF download fails or returns invalid content."""


REQUEST_TIMEOUT = 5
CHUNK_SIZE = 8192
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
ACCEPT_HEADER = "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8"


def find_existing_download(ticker: str, download_dir: Path) -> Optional[Path]:
    if not ticker:
        return None
    candidates = sorted(
        download_dir.glob(f"{ticker}_*.pdf"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def safe_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    name = unquote(Path(parsed.path).name or "report.pdf")
    if not name.lower().endswith(".pdf"):
        if ".pdf" in name.lower():
            idx = name.lower().rfind(".pdf")
            name = name[: idx + 4]
        else:
            name = f"{name}.pdf"
    return name


def hash_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]


def _print_progress(downloaded: int, total: int, *, prefix: str) -> None:
    if total <= 0:
        sys.stdout.write(f"\r{prefix}{downloaded / 1_000_000:.1f} MB")
        sys.stdout.flush()
        return
    percent = int(downloaded * 100 / total)
    sys.stdout.write(f"\r{prefix}{percent:3d}% ({downloaded / 1_000_000:.1f}/{total / 1_000_000:.1f} MB)")
    sys.stdout.flush()


def _save_stream_to_file(
    chunk_iterator: Iterable[bytes],
    *,
    out_path: Path,
    content_type: str,
    total_length: int,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    file_handle = None
    first_chunk = None
    downloaded = 0
    show_progress = sys.stdout.isatty() and total_length >= 0
    try:
        for chunk in chunk_iterator:
            if not chunk:
                continue
            if first_chunk is None:
                first_chunk = chunk
                is_pdf_magic = first_chunk[:4] == b"%PDF"
                if ("pdf" not in content_type) and (not is_pdf_magic):
                    raise DownloadError(
                        f"not a PDF (Content-Type='{content_type or 'unknown'}', magic={'ok' if is_pdf_magic else 'missing'})"
                    )
                file_handle = tmp_path.open("wb")
                file_handle.write(first_chunk)
            else:
                if file_handle is None:
                    file_handle = tmp_path.open("wb")
                file_handle.write(chunk)

            downloaded += len(chunk)
            if show_progress:
                _print_progress(downloaded, total_length, prefix="    downloading: ")

        if file_handle:
            file_handle.close()
        if first_chunk is None:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)
            raise DownloadError("empty response body")
        tmp_path.rename(out_path)
        if show_progress:
            sys.stdout.write("\n")
    except Exception:
        if file_handle:
            file_handle.close()
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def _download_with_requests(url: str, out_path: Path) -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT_HEADER,
        "Referer": url,
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DownloadError(str(exc)) from exc

    content_type = (response.headers.get("Content-Type") or "").lower()
    total_length = int(response.headers.get("Content-Length") or 0)
    try:
        _save_stream_to_file(
            response.iter_content(chunk_size=CHUNK_SIZE),
            out_path=out_path,
            content_type=content_type,
            total_length=total_length,
        )
    except DownloadError:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        raise DownloadError(str(exc)) from exc


def _download_with_urllib(url: str, out_path: Path) -> None:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": ACCEPT_HEADER,
        "Referer": url,
        "Accept-Language": "en-US,en;q=0.9",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            total_length = response.length or int(response.headers.get("Content-Length") or 0)

            def _chunk_generator():
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

            _save_stream_to_file(
                _chunk_generator(),
                out_path=out_path,
                content_type=content_type,
                total_length=total_length,
            )
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
        raise DownloadError(str(exc)) from exc


def _download_with_curl(url: str, out_path: Path) -> None:
    curl_path = shutil.which("curl")
    if curl_path is None:
        raise DownloadError("curl binary not found")

    tmp_path = out_path.with_suffix(out_path.suffix + ".part")
    if tmp_path.exists():
        tmp_path.unlink(missing_ok=True)

    cmd = [
        curl_path,
        "--silent",
        "--show-error",
        "--location",
        "--fail",
        "--max-time",
        str(REQUEST_TIMEOUT),
        "--user-agent",
        USER_AGENT,
        "-H",
        f"Accept: {ACCEPT_HEADER}",
        "-H",
        f"Referer: {url}",
        "--output",
        str(tmp_path),
        url,
    ]
    result = subprocess.run(  # noqa: S603, S607
        cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        message = result.stderr.strip() or f"curl exit code {result.returncode}"
        raise DownloadError(message)

    try:
        with tmp_path.open("rb") as file_handle:
            magic = file_handle.read(4)
            if not magic.startswith(b"%PDF"):
                raise DownloadError("curl downloaded file is not a PDF")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.rename(out_path)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def download_pdf(url: str, out_path: Path) -> None:
    errors = []
    try:
        _download_with_requests(url, out_path)
        return
    except DownloadError as exc:
        errors.append(f"requests: {exc}")

    try:
        _download_with_urllib(url, out_path)
        return
    except DownloadError as exc:
        errors.append(f"urllib: {exc}")

    try:
        _download_with_curl(url, out_path)
        return
    except DownloadError as exc:
        errors.append(f"curl: {exc}")

    raise DownloadError("; ".join(errors))
