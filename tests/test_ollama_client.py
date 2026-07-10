import json

import httpx
import pytest
import respx

from lantern_pkms.htr.ollama_client import OllamaError, OllamaHTRClient

BASE_URL = "http://ollama.example.com:11434"


@respx.mock
def test_transcribe_page_parses_lines_and_forces_cpu() -> None:
    route = respx.post(f"{BASE_URL}/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": json.dumps(
                    {
                        "lines": [
                            {
                                "raw_symbol": "bullet",
                                "symbol_crossed_out": False,
                                "text_struck_through": False,
                                "text": "Buy groceries",
                                "confidence": 0.92,
                            },
                            {
                                "raw_symbol": "circle",
                                "text": "Dentist at 2pm",
                                "confidence": 0.81,
                            },
                        ]
                    }
                ),
                "done": True,
            },
        )
    )

    client = OllamaHTRClient(BASE_URL, model="qwen3-vl:8b")
    lines = client.transcribe_page(b"\x89PNG-fake-bytes", prompt="transcribe this page")

    assert len(lines) == 2
    assert lines[0].raw_symbol == "bullet"
    assert lines[0].text == "Buy groceries"
    assert lines[1].raw_symbol == "circle"

    sent_body = json.loads(route.calls.last.request.content)
    assert sent_body["model"] == "qwen3-vl:8b"
    assert sent_body["options"] == {"num_gpu": 0}
    assert sent_body["think"] is False

    # images should be base64 of the raw bytes we passed in
    import base64

    assert sent_body["images"][0] == base64.b64encode(b"\x89PNG-fake-bytes").decode()


@respx.mock
def test_force_cpu_false_omits_options() -> None:
    respx.post(f"{BASE_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": json.dumps({"lines": []})})
    )
    client = OllamaHTRClient(BASE_URL, model="qwen3-vl:8b", force_cpu=False)
    client.transcribe_page(b"bytes", prompt="p")

    sent_body = json.loads(respx.calls.last.request.content)
    assert "options" not in sent_body


@respx.mock
def test_missing_response_field_raises() -> None:
    respx.post(f"{BASE_URL}/api/generate").mock(return_value=httpx.Response(200, json={}))
    client = OllamaHTRClient(BASE_URL, model="qwen3-vl:8b")
    with pytest.raises(OllamaError):
        client.transcribe_page(b"bytes", prompt="p")


@respx.mock
def test_falls_back_to_thinking_field_when_response_empty() -> None:
    # Regression test: qwen3-vl is a hybrid reasoning model. Even with think=False
    # requested, older/other Ollama versions may still only populate 'thinking' and
    # leave 'response' empty — confirmed live against a real qwen3-vl:8b call where
    # the full valid JSON transcription landed in 'thinking' while 'response' was "".
    respx.post(f"{BASE_URL}/api/generate").mock(
        return_value=httpx.Response(
            200,
            json={
                "response": "",
                "thinking": json.dumps(
                    {"lines": [{"raw_symbol": "dash", "text": "Note from thinking", "confidence": 0.7}]}
                ),
                "done": True,
            },
        )
    )
    client = OllamaHTRClient(BASE_URL, model="qwen3-vl:8b")
    lines = client.transcribe_page(b"bytes", prompt="p")

    assert len(lines) == 1
    assert lines[0].text == "Note from thinking"


@respx.mock
def test_invalid_json_response_raises() -> None:
    respx.post(f"{BASE_URL}/api/generate").mock(
        return_value=httpx.Response(200, json={"response": "not json{{"})
    )
    client = OllamaHTRClient(BASE_URL, model="qwen3-vl:8b")
    with pytest.raises(OllamaError):
        client.transcribe_page(b"bytes", prompt="p")
