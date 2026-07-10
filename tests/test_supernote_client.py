import hashlib
import json

import httpx
import pytest
import respx

from home_pkms.supernote.client import (
    PATH_DOWNLOAD_V3,
    PATH_LIST_FOLDER,
    PATH_LOGIN_EQUIPMENT,
    PATH_RANDOM_CODE,
    PATH_SYNC_END,
    PATH_SYNC_START,
    SupernoteAuthError,
    SupernoteClient,
    SupernoteError,
    _hash_password,
)

BASE_URL = "https://spc.example.com"


def test_hash_password_matches_documented_scheme() -> None:
    # SHA256(MD5(plain) + randomCode)
    expected_md5 = hashlib.md5(b"hunter2").hexdigest()
    expected = hashlib.sha256((expected_md5 + "salt123").encode()).hexdigest()
    assert _hash_password("hunter2", "salt123") == expected


@respx.mock
def test_login_success_stores_token() -> None:
    respx.post(f"{BASE_URL}{PATH_RANDOM_CODE}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "randomCode": "abc123", "timestamp": 1783643324747}
        )
    )
    respx.post(f"{BASE_URL}{PATH_LOGIN_EQUIPMENT}").mock(
        return_value=httpx.Response(200, json={"success": True, "token": "jwt-token-xyz"})
    )

    client = SupernoteClient(BASE_URL)
    client.login("mike@example.com", "hunter2")

    assert client._token == "jwt-token-xyz"


@respx.mock
def test_login_echoes_randomcode_response_timestamp_not_a_fresh_one() -> None:
    # Regression test for the real bug found in Phase 0: the server has no session/
    # cookie state and uses account+timestamp as a stateless lookup key for the
    # issued code. Sending a freshly-generated client timestamp instead of echoing
    # the one from the randomCode response makes the server unable to find the
    # code at all, surfacing as an indistinguishable-from-wrong-password error.
    respx.post(f"{BASE_URL}{PATH_RANDOM_CODE}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "randomCode": "abc123", "timestamp": 1783643324747}
        )
    )
    login_route = respx.post(f"{BASE_URL}{PATH_LOGIN_EQUIPMENT}").mock(
        return_value=httpx.Response(200, json={"success": True, "token": "jwt-token-xyz"})
    )

    client = SupernoteClient(BASE_URL)
    client.login("mike@example.com", "hunter2")

    sent_body = json.loads(login_route.calls.last.request.content)
    assert sent_body["timestamp"] == 1783643324747


@respx.mock
def test_random_code_response_missing_timestamp_raises() -> None:
    respx.post(f"{BASE_URL}{PATH_RANDOM_CODE}").mock(
        return_value=httpx.Response(200, json={"success": True, "randomCode": "abc123"})
    )
    client = SupernoteClient(BASE_URL)
    with pytest.raises(SupernoteAuthError):
        client.login("mike@example.com", "hunter2")


@respx.mock
def test_login_failure_raises_auth_error() -> None:
    respx.post(f"{BASE_URL}{PATH_RANDOM_CODE}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "randomCode": "abc123", "timestamp": 1783643324747}
        )
    )
    respx.post(f"{BASE_URL}{PATH_LOGIN_EQUIPMENT}").mock(
        return_value=httpx.Response(
            200, json={"success": False, "errorCode": "1001", "errorMsg": "bad credentials"}
        )
    )

    client = SupernoteClient(BASE_URL)
    with pytest.raises(SupernoteError):
        client.login("mike@example.com", "wrongpass")


@respx.mock
def test_list_folder_before_login_raises() -> None:
    client = SupernoteClient(BASE_URL)
    with pytest.raises(SupernoteAuthError):
        client.list_folder("/")


@respx.mock
def test_list_folder_parses_entries() -> None:
    respx.post(f"{BASE_URL}{PATH_LIST_FOLDER}").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "entries": [
                    {
                        "tag": "folder",
                        "id": "1",
                        "name": "Daily",
                        "path_display": "/Note/Daily",
                        "parent_path": "/Note",
                    },
                    {
                        "tag": "file",
                        "id": "1234",
                        "name": "2026-07-09.note",
                        "path_display": "/Note/Daily/2026/2026-07-09.note",
                        "content_hash": "deadbeef",
                        "size": 4096,
                        "lastUpdateTime": 1767571999000,
                        "parent_path": "/Note/Daily/2026",
                    },
                ],
            },
        )
    )

    client = SupernoteClient(BASE_URL)
    client._token = "already-logged-in"  # bypass login for this unit test
    entries = client.list_folder("/", recursive=True)

    assert len(entries) == 2
    folder, file_entry = entries
    assert folder.is_folder
    assert not file_entry.is_folder
    assert file_entry.content_hash == "deadbeef"
    assert file_entry.size == 4096


@respx.mock
def test_download_fetches_url_then_content() -> None:
    respx.post(f"{BASE_URL}{PATH_DOWNLOAD_V3}").mock(
        return_value=httpx.Response(
            200, json={"success": True, "url": "https://cdn.example.com/blob/1234"}
        )
    )
    respx.get("https://cdn.example.com/blob/1234").mock(
        return_value=httpx.Response(200, content=b"\x00note-bytes")
    )

    client = SupernoteClient(BASE_URL)
    client._token = "already-logged-in"
    content = client.download("1234")

    assert content == b"\x00note-bytes"


@respx.mock
def test_sync_start_and_end() -> None:
    start_route = respx.post(f"{BASE_URL}{PATH_SYNC_START}").mock(
        return_value=httpx.Response(200, json={"success": True, "synType": True})
    )
    end_route = respx.post(f"{BASE_URL}{PATH_SYNC_END}").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    client = SupernoteClient(BASE_URL)
    client._token = "already-logged-in"
    client.sync_start()
    client.sync_end(success=True)

    assert start_route.called
    assert end_route.called
    sent_body = end_route.calls.last.request.content
    assert b'"flag": "Y"' in sent_body or b'"flag":"Y"' in sent_body
