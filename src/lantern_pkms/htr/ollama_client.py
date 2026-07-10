"""Ollama client for page-level HTR via structured JSON output.

Calls with options.num_gpu=0 by default — this pipeline is CPU-only by design: it's
not real-time, so CPU inference is fine, and it leaves any GPU on the Ollama host
free for other ad hoc model use. Override force_cpu=False if you'd rather dedicate a
GPU to this pipeline instead.
"""

from __future__ import annotations

import base64
import json
import logging

import httpx

from lantern_pkms.htr.schema import PAGE_LINES_SCHEMA
from lantern_pkms.structuring.symbol_mapping import VLMLine

logger = logging.getLogger("lantern_pkms")

# A dense bullet-journal page's structured JSON can run long — with no explicit
# budget, Ollama/the model's default generation cap was silently truncating
# output mid-field on data-dense pages (issue #4). num_predict is a ceiling, not
# a forced length: generation still stops at its own JSON-closing token, so this
# shouldn't affect latency on typical pages, only remove the cap that dense ones
# were hitting. num_ctx gives headroom for image tokens + prompt + that output.
_NUM_PREDICT = 4096
_NUM_CTX = 8192

# Generation is non-deterministic (no temperature/seed pinned here), so a retry
# has a real chance of producing a complete response even on a page that just
# failed — cheap insurance on top of the num_predict/num_ctx fix above.
_MAX_ATTEMPTS = 2


class OllamaError(Exception):
    pass


class OllamaHTRClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        force_cpu: bool = True,
        http_client: httpx.Client | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._model = model
        self._force_cpu = force_cpu
        self._http = http_client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "OllamaHTRClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def transcribe_page(self, image_png_bytes: bytes, prompt: str) -> list[VLMLine]:
        last_error: OllamaError | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return self._transcribe_once(image_png_bytes, prompt)
            except OllamaError as exc:
                last_error = exc
                logger.warning("HTR attempt %d/%d failed: %s", attempt, _MAX_ATTEMPTS, exc)
        assert last_error is not None
        raise last_error

    def _transcribe_once(self, image_png_bytes: bytes, prompt: str) -> list[VLMLine]:
        options: dict = {"num_predict": _NUM_PREDICT, "num_ctx": _NUM_CTX}
        if self._force_cpu:
            options["num_gpu"] = 0

        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [base64.b64encode(image_png_bytes).decode("ascii")],
            "format": PAGE_LINES_SCHEMA,
            "stream": False,
            # qwen3-vl is a hybrid reasoning model — left to its default, it puts the
            # actual structured output in the 'thinking' field and leaves 'response'
            # empty, which looks like a failed call. Disabling thinking makes it put
            # the answer directly in 'response', and is faster besides (skips
            # generating the reasoning trace).
            "think": False,
            "options": options,
        }

        resp = self._http.post("/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()

        # Fall back to 'thinking' if 'response' is still empty — belt-and-suspenders
        # in case a model/version ignores think=False and only ever fills 'thinking'.
        raw_response = data.get("response") or data.get("thinking")
        if not raw_response:
            raise OllamaError(f"Ollama response had no 'response' or 'thinking' field: {data!r}")

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama response was not valid JSON: {raw_response!r}") from exc

        lines_data = parsed.get("lines", [])
        return [VLMLine.model_validate(line) for line in lines_data]
