"""Semantic adapter boundary for controlled memory retrieval (Phase 2).

Provider-neutral embedding adapter for Phase 2 semantic memory retrieval.
The module exposes:

* ``build_semantic_adapter`` — single factory/resolver that returns
  ``None`` when no provider has been configured. Callers MUST treat
  ``None`` as "do not call the adapter" and fall back to the
  deterministic lexical retrieval path.
* ``resolve_semantic_config`` — single source of config defaults.
  Always returns the same shape; empty strings mean "not configured".
* ``OpenAICompatibleEmbeddingAdapter`` — provider-neutral HTTP
  adapter for an OpenAI-style ``/v1/embeddings`` endpoint. The
  transport is injectable so tests can fake the network.

Default configuration is provider=none with empty endpoint/model/key
env name. The adapter is never constructed at module import time and
no HTTP client is created until ``score`` is called and
``is_configured`` is True.

Score semantics (single contract):

* ``score`` returns cosine similarity normalized to ``[0, 1]`` using
  ``(cosine + 1) / 2``. This is what ``min_score`` is compared
  against. Real OpenAI-compatible endpoints typically return unit
  vectors so raw cosine is essentially the dot product and the
  normalized value is in ``[0.5, 1]`` for any non-zero vector.
* Threshold (``min_score``) is configurable, default ``0.75``
  (normalized cosine). Setting it to a raw-cosine value is the
  caller's responsibility; the adapter does not silently swap
  semantics.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = [
    "OpenAICompatibleEmbeddingAdapter",
    "SemanticAdapterConfig",
    "build_semantic_adapter",
    "resolve_semantic_config",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TIMEOUT_MS",
]

DEFAULT_MIN_SCORE = 0.75
DEFAULT_TIMEOUT_MS = 120


_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "enabled"})


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE_VALUES


def _bound_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(parsed) or not math.isfinite(parsed):
        return default
    return max(low, min(high, parsed))


@dataclass(frozen=True)
class SemanticAdapterConfig:
    """Resolved, validated, provider-neutral configuration.

    All string fields are empty by default and the ``provider`` is
    ``"none"``. ``build_semantic_adapter`` only ever returns an
    adapter when every field is populated and the api-key env var
    resolves to a non-empty value at adapter build time.
    """

    provider: str = "none"
    endpoint: str = ""
    model: str = ""
    api_key_env: str = ""
    api_key: str = ""
    min_score: float = DEFAULT_MIN_SCORE
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def is_configured(self) -> bool:
        return bool(
            self.provider
            and self.provider != "none"
            and self.endpoint
            and self.model
            and self.api_key_env
            and self.api_key
        )

    # Keep the old snapshot-like access pattern available without exposing
    # the resolved secret value. Existing status/tests use mapping access.
    def __getitem__(self, key: str) -> Any:
        if key == "api_key":
            return ""
        if key == "min_score":
            return str(self.min_score)
        if key == "timeout_ms":
            return str(self.timeout_ms)
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except (KeyError, AttributeError):
            return default


def _coerce_config(
    raw: SemanticAdapterConfig | Mapping[str, Any] | None,
    *,
    env: Mapping[str, str] | None = None,
) -> SemanticAdapterConfig:
    """Convert a public snapshot or dataclass to one validated config."""

    source = os.environ if env is None else env
    if raw is None:
        raw = resolve_semantic_config(env=source)
    if isinstance(raw, SemanticAdapterConfig):
        api_key_env = raw.api_key_env
        api_key = raw.api_key or str(source.get(api_key_env, "")).strip()
        return SemanticAdapterConfig(
            provider=raw.provider,
            endpoint=raw.endpoint,
            model=raw.model,
            api_key_env=api_key_env,
            api_key=api_key,
            min_score=raw.min_score,
            timeout_ms=raw.timeout_ms,
        )
    if isinstance(raw, Mapping):
        api_key_env = str(raw.get("api_key_env", "")).strip()
        return SemanticAdapterConfig(
            provider=str(raw.get("provider", "none")).strip().lower() or "none",
            endpoint=str(raw.get("endpoint", "")).strip(),
            model=str(raw.get("model", "")).strip(),
            api_key_env=api_key_env,
            api_key=str(raw.get("api_key", "") or source.get(api_key_env, "")).strip(),
            min_score=_bound_float(raw.get("min_score"), DEFAULT_MIN_SCORE, 0.0, 1.0),
            timeout_ms=int(_bound_float(raw.get("timeout_ms"), DEFAULT_TIMEOUT_MS, 10.0, 5000.0)),
        )
    raise TypeError("semantic_config_must_be_mapping_or_dataclass")


def resolve_semantic_config(
    *,
    env: Mapping[str, str] | None = None,
) -> SemanticAdapterConfig:
    """Read semantic adapter config from the environment.

    Always returns a fully populated ``SemanticAdapterConfig`` with
    safe defaults. Does NOT resolve the api-key value — that is done
    inside ``build_semantic_adapter`` so the resulting adapter stays
    minimal and tests can override.
    """

    source = os.environ if env is None else env
    provider = str(source.get("NANA_MEMORY_SEMANTIC_PROVIDER", "none")).strip().lower() or "none"
    endpoint = str(source.get("NANA_MEMORY_SEMANTIC_ENDPOINT", "")).strip()
    model = str(source.get("NANA_MEMORY_SEMANTIC_MODEL", "")).strip()
    api_key_env = str(source.get("NANA_MEMORY_SEMANTIC_API_KEY_ENV", "")).strip()
    min_score = _bound_float(
        source.get("NANA_MEMORY_SEMANTIC_MIN_SCORE"),
        DEFAULT_MIN_SCORE,
        0.0,
        1.0,
    )
    timeout_ms = int(
        _bound_float(
            source.get("NANA_MEMORY_SEMANTIC_TIMEOUT_MS"),
            DEFAULT_TIMEOUT_MS,
            10.0,
            5000.0,
        )
    )
    return SemanticAdapterConfig(
        provider=provider,
        endpoint=endpoint,
        model=model,
        api_key_env=api_key_env,
        min_score=min_score,
        timeout_ms=timeout_ms,
    )


def build_semantic_adapter(
    *,
    config: SemanticAdapterConfig | None = None,
    transport: Callable[..., Any] | None = None,
    env: Mapping[str, str] | None = None,
) -> "OpenAICompatibleEmbeddingAdapter | None":
    """Return a configured adapter or ``None``.

    Returns ``None`` when:

    * the resolved ``provider`` is ``"none"``;
    * any of ``endpoint``, ``model`` or ``api_key_env`` is empty;
    * the referenced env var (the value of ``api_key_env``) is empty
      in the environment;
    * the resolved config is otherwise incomplete.

    The factory is the only entry point callers should use to obtain
    an adapter. Constructing ``OpenAICompatibleEmbeddingAdapter``
    directly is supported for tests but production code must rely on
    this resolver.
    """

    raw_config = config or resolve_semantic_config(env=env)
    cfg = _coerce_config(raw_config, env=env)
    if not cfg.api_key_env:
        return None
    if not cfg.is_configured() or cfg.provider == "none":
        return None
    return OpenAICompatibleEmbeddingAdapter(config=cfg, transport=transport)


def _is_finite_sequence(value: Any) -> bool:
    if not isinstance(value, (list, tuple)):
        return False
    for item in value:
        try:
            number = float(item)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(number):
            return False
    return True


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vector_dimension_mismatch")
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        fx = float(x)
        fy = float(y)
        dot += fx * fy
        norm_a += fx * fx
        norm_b += fy * fy
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _validate_embedding_payload(
    response: Any,
    *,
    expected_count: int,
) -> list[list[float]]:
    if not isinstance(response, Mapping) or "data" not in response:
        raise ValueError("embedding_response_malformed")
    data = response["data"]
    if not isinstance(data, list) or not data:
        raise ValueError("embedding_data_empty")
    if len(data) != expected_count:
        raise ValueError("embedding_count_mismatch")
    by_index: dict[int, list[float]] = {}
    for item in data:
        if not isinstance(item, Mapping):
            raise ValueError("embedding_item_not_object")
        if "embedding" not in item or "index" not in item:
            raise ValueError("embedding_item_missing_fields")
        try:
            index = int(item["index"])
        except (TypeError, ValueError):
            raise ValueError("embedding_index_not_int")
        if index in by_index:
            raise ValueError("embedding_index_duplicate")
        vec = item["embedding"]
        if not isinstance(vec, (list, tuple)) or not vec:
            raise ValueError("embedding_vector_empty")
        if not _is_finite_sequence(vec):
            raise ValueError("embedding_non_finite")
        by_index[index] = [float(v) for v in vec]
    if sorted(by_index.keys()) != list(range(expected_count)):
        raise ValueError("embedding_index_incomplete")
    vectors = [by_index[i] for i in range(expected_count)]
    dimension = len(vectors[0])
    if any(len(vec) != dimension for vec in vectors):
        raise ValueError("embedding_dimension_inconsistent")
    return vectors


class OpenAICompatibleEmbeddingAdapter:
    """Provider-neutral OpenAI-compatible embedding adapter.

    The adapter is fail-safe: timeout, malformed response, dimension
    mismatch, NaN/Inf, and missing credentials raise typed errors that
    the controlled retrieval boundary converts into a lexical
    fallback. The adapter never logs secrets, auth headers, or raw
    request bodies.

    Score semantics are documented at module level. The factory
    ``build_semantic_adapter`` is the canonical entry point.
    """

    def __init__(
        self,
        *,
        config: SemanticAdapterConfig | Mapping[str, Any] | None = None,
        transport: Callable[..., Any] | None = None,
    ) -> None:
        self._config = _coerce_config(config)
        self._transport = transport

    @property
    def config(self) -> SemanticAdapterConfig:
        return self._config

    @property
    def is_configured(self) -> bool:
        """Single authoritative flag for caller-side checks.

        Returns ``False`` when:

        * ``provider`` is empty or ``"none"``;
        * any of ``endpoint`` / ``model`` / ``api_key_env`` is empty;
        * the resolved ``api_key`` is empty.

        Callers MUST treat ``False`` as "do not call the adapter".
        """

        return self._config.is_configured()

    def score(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """Score a query against already-filtered candidates.

        Returns ``{"scores": {record_id: float}, "elapsed_ms": float}``.
        ``scores`` is empty when there are no eligible candidate texts.
        Raises:

        * ``ValueError`` on malformed responses, NaN/Inf, dimension or
          index mismatch;
        * ``TimeoutError`` when wall-time exceeds the requested budget.
        """

        if not self.is_configured:
            return {"scores": {}, "elapsed_ms": 0.0, "skipped": "unconfigured"}

        effective_timeout = self._config.timeout_ms if timeout_ms is None else int(timeout_ms)
        if effective_timeout <= 0:
            effective_timeout = DEFAULT_TIMEOUT_MS

        candidate_records: list[Mapping[str, Any]] = []
        for record in candidates or ():
            if isinstance(record, Mapping):
                candidate_records.append(record)

        texts: list[str] = [str(query or "").strip()[:2000]]
        id_map: list[str] = []
        for record in candidate_records:
            text = str(record.get("text") or "").strip()[:2000]
            if not text:
                continue
            texts.append(text)
            id_map.append(str(record.get("id", "")))

        if len(texts) <= 1:
            return {"scores": {}, "elapsed_ms": 0.0, "skipped": "no_candidates"}

        started = time.monotonic()
        vectors = self._embed_batch(texts, effective_timeout)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        if elapsed_ms > effective_timeout:
            raise TimeoutError("semantic adapter exceeded timeout")

        scores: dict[str, float] = {}
        query_vec = vectors[0]
        for idx, candidate_vec in enumerate(vectors[1:], start=0):
            if idx >= len(id_map):
                break
            raw_cosine = _cosine_similarity(query_vec, candidate_vec)
            normalized = max(0.0, min(1.0, (raw_cosine + 1.0) / 2.0))
            scores[id_map[idx]] = round(normalized, 6)
        return {"scores": scores, "elapsed_ms": round(elapsed_ms, 3), "skipped": ""}

    # ------------------------------------------------------------------
    # transport

    def _embed_batch(self, texts: list[str], timeout_ms: int) -> list[list[float]]:
        payload = {"model": self._config.model, "input": texts}
        endpoint = self._config.endpoint.rstrip("/") + "/embeddings"
        timeout_seconds = max(0.05, float(timeout_ms) / 1000.0)
        headers = {
            "Authorization": "Bearer " + self._config.api_key,
            "Content-Type": "application/json",
        }

        if self._transport is not None:
            response = self._transport(
                url=endpoint,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
        else:
            try:
                import requests  # local import — never at module import time
            except Exception as exc:  # pragma: no cover - covered by transport injection
                raise RuntimeError("requests_unavailable: " + type(exc).__name__) from exc
            http = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
            http.raise_for_status()
            response = http.json()

        return _validate_embedding_payload(response, expected_count=len(texts))


def normalize_min_score(
    value: Any,
    *,
    default: float = DEFAULT_MIN_SCORE,
) -> float:
    """Public helper: clamp any value into ``[0, 1]``."""

    return _bound_float(value, default, 0.0, 1.0)


def _iter_candidate_texts(candidates: Iterable[Mapping[str, Any]]) -> list[str]:
    """Extract sanitized texts from candidate mappings."""

    out: list[str] = []
    for record in candidates:
        if isinstance(record, Mapping):
            text = str(record.get("text") or "").strip()[:2000]
            if text:
                out.append(text)
    return out
