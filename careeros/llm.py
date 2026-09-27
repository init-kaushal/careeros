from __future__ import annotations

import os
import litellm

DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def complete(*, model: str | None = None, **kwargs) -> litellm.ModelResponse:
    """Call litellm.completion with automatic fallback support.

    Primary model: explicit `model` arg → CAREEROS_MODEL → DEFAULT_MODEL.
    Fallbacks: CAREEROS_FALLBACK_MODELS (comma-separated), tried in order on
    rate-limit (429) or provider errors. Ignored when `model` is given
    explicitly, since an explicit caller already chose their model.
    """
    primary = model or os.environ.get("CAREEROS_MODEL", DEFAULT_MODEL)
    if model is None:
        fallback_env = os.environ.get("CAREEROS_FALLBACK_MODELS", "")
        fallbacks = [m.strip() for m in fallback_env.split(",") if m.strip()]
    else:
        fallbacks = []

    timeout = kwargs.pop("timeout", int(os.environ.get("CAREEROS_LLM_TIMEOUT", "120")))
    if fallbacks:
        return litellm.completion(model=primary, fallbacks=fallbacks, timeout=timeout, **kwargs)
    return litellm.completion(model=primary, timeout=timeout, **kwargs)
