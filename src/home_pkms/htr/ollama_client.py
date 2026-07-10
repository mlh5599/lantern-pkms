"""Ollama client for page-level HTR via structured JSON output.

Calls with options.num_gpu=0 by default — this pipeline is CPU-only by design: it's
not real-time, so CPU inference is fine, and it leaves any GPU on the Ollama host
free for other ad hoc model use. Override force_cpu=False if you'd rather dedicate a
GPU to this pipeline instead.
"""

from __future__ import annotations

import base64
import json

import httpx

from home_pkms.htr.schema import PAGE_LINES_SCHEMA
from home_pkms.structuring.symbol_mapping import VLMLine


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
        payload = {
            "model": self._model,
            "prompt": prompt,
            "images": [base64.b64encode(image_png_bytes).decode("ascii")],
            "format": PAGE_LINES_SCHEMA,
            "stream": False,
        }
        if self._force_cpu:
            payload["options"] = {"num_gpu": 0}

        resp = self._http.post("/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()

        raw_response = data.get("response")
        if not raw_response:
            raise OllamaError(f"Ollama response had no 'response' field: {data!r}")

        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise OllamaError(f"Ollama response was not valid JSON: {raw_response!r}") from exc

        lines_data = parsed.get("lines", [])
        return [VLMLine.model_validate(line) for line in lines_data]
