from __future__ import annotations

from functools import lru_cache

try:
    import tiktoken
except ImportError:  # pragma: no cover - optional dependency
    tiktoken = None

TOKEN_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _resolve_encoder():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(TOKEN_ENCODING_NAME)
    except Exception:  # pragma: no cover - defensive fallback
        return None


def count_tokens(text: str) -> int:
    if not text:
        return 0
    encoder = _resolve_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text))
        except Exception:  # pragma: no cover - defensive fallback
            pass
    # Fallback heuristic: average 4 characters per token
    return max(0, (len(text) + 3) // 4)


