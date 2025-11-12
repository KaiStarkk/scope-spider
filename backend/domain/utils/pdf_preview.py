from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from typing import Iterable, List, Tuple

try:  # pragma: no cover - optional dependency
    from pdf2image import convert_from_path
except ImportError:  # pragma: no cover
    convert_from_path = None

_PREVIEW_BASE = Path("extracted/previews")


def _cache_token(pdf_path: Path) -> str:
    try:
        stat = pdf_path.stat()
        key = f"{pdf_path.resolve()}::{stat.st_mtime_ns}::{stat.st_size}"
    except FileNotFoundError:
        key = str(pdf_path.resolve())
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return digest[:16]


def _preview_path(pdf_path: Path, page_number: int) -> Path:
    token = _cache_token(pdf_path)
    return _PREVIEW_BASE / token / f"page-{page_number}.png"


def ensure_page_previews(
    pdf_path: Path,
    pages: Iterable[int],
    *,
    dpi: int = 160,
) -> List[Tuple[int, Path]]:
    if convert_from_path is None:
        return []
    unique_pages = sorted({page for page in pages if page and page > 0})
    if not unique_pages:
        return []

    results: List[Tuple[int, Path]] = []
    for page in unique_pages:
        out_path = _preview_path(pdf_path, page)
        if not out_path.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                images = convert_from_path(
                    str(pdf_path),
                    dpi=dpi,
                    first_page=page,
                    last_page=page,
                    fmt="png",
                )
            except Exception:  # pragma: no cover - pdf2image runtime errors
                continue
            if not images:
                continue
            images[0].save(out_path, format="PNG")
        results.append((page, out_path))
    return results


def previews_as_data_urls(
    pdf_path: Path, pages: Iterable[int]
) -> List[Tuple[int, str]]:
    previews = ensure_page_previews(pdf_path, pages)
    data_urls: List[Tuple[int, str]] = []
    for page, image_path in previews:
        try:
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        except OSError:
            continue
        data_urls.append((page, f"data:image/png;base64,{encoded}"))
    return data_urls


