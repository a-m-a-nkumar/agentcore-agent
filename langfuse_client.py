"""
Optional Langfuse client for observability.
When LANGFUSE_SECRET_KEY is not set, all tracing is a no-op so the app works unchanged.
"""

import os
import logging
from contextlib import contextmanager
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_LANGFUSE = None


class _NoOpObservation:
    """No-op observation for when Langfuse is disabled. Supports .update() and context manager."""

    def update(self, **kwargs: Any) -> None:
        pass

    def __enter__(self) -> "_NoOpObservation":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@contextmanager
def _noop_observation(**kwargs: Any):
    yield _NoOpObservation()


def get_langfuse():
    """
    Return the Langfuse client if configured, else a no-op wrapper.
    Caller can always use: langfuse.start_as_current_observation(...) and it will either
    trace or do nothing.
    """
    global _LANGFUSE
    if _LANGFUSE is not None:
        return _LANGFUSE

    secret = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    public = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    if not secret or not public:
        logger.debug("Langfuse not configured (LANGFUSE_SECRET_KEY or LANGFUSE_PUBLIC_KEY missing). Tracing disabled.")
        _LANGFUSE = _NoOpLangfuse()
        return _LANGFUSE

    try:
        from langfuse import Langfuse

        base_url = (os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com").strip()
        if not base_url.startswith("http"):
            base_url = f"https://{base_url}"
        try:
            _LANGFUSE = Langfuse(public_key=public, secret_key=secret, base_url=base_url)
        except TypeError:
            _LANGFUSE = Langfuse(public_key=public, secret_key=secret, host=base_url)
        try:
            if hasattr(_LANGFUSE, "auth_check") and _LANGFUSE.auth_check():
                logger.info("Langfuse client initialized and authenticated.")
        except Exception:
            logger.info("Langfuse client initialized (auth check skipped).")
        return _LANGFUSE
    except ImportError as e:
        if "langfuse" in str(e).lower() or "No module named" in str(e):
            logger.warning(
                "Langfuse tracing disabled: package not installed. "
                "Install in your app venv: pip install langfuse  then restart the app."
            )
        else:
            logger.warning("Langfuse init failed, tracing disabled: %s", e)
        _LANGFUSE = _NoOpLangfuse()
        return _LANGFUSE
    except Exception as e:
        logger.warning("Langfuse init failed, tracing disabled: %s", e)
        _LANGFUSE = _NoOpLangfuse()
        return _LANGFUSE


class _NoOpLangfuse:
    """No-op client when Langfuse is disabled. Same interface as Langfuse for our usage."""

    def start_as_current_observation(self, as_type: str = "span", name: Optional[str] = None, **kwargs: Any):
        return _noop_observation()

    def flush(self) -> None:
        pass


def flush_langfuse() -> None:
    """Flush pending Langfuse events. Safe to call when disabled."""
    client = get_langfuse()
    if hasattr(client, "flush"):
        try:
            client.flush()
        except Exception as e:
            logger.debug("Langfuse flush failed: %s", e)
