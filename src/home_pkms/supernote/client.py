"""Hand-rolled client for the Supernote Private Cloud device-sync protocol.

Deliberately NOT built on the allenporter/supernote package's client — that library's
async client talks to their own competing self-hosted server reimplementation, not
verified against Ratta's official supernote-service (the self-hosted Supernote
Private Cloud image this client targets). See AGENTS.md's "Critical gotcha" section
for the full reasoning and the hard-won protocol details.

Endpoints and request/response shapes below are grounded in two sources:
1. A real captured device<->server trace (confirms: synchronous/start, list_folder,
   synchronous/end, and the request body shape for list_folder).
2. allenporter/supernote's reverse-engineered OpenAPI spec (api-spec/ in their repo) —
   a third party's documentation of the protocol, not Ratta's own, so treat anything
   not covered by (1) as a best-effort draft.

This is explicitly UNVERIFIED against Mike's real deployment until Phase 0 (see
scripts/htr_bench.py and the plan's Verification section) runs a live login +
list_folder smoke test. That's a hard go/no-go gate, not a formality — if the
protocol doesn't match, expect to need adjustments here based on a real trace
captured from Mike's own tablet.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx

# Confirmed via a real captured device sync trace.
PATH_SYNC_START = "/api/file/2/files/synchronous/start"
PATH_SYNC_END = "/api/file/2/files/synchronous/end"
PATH_LIST_FOLDER = "/api/file/2/files/list_folder"

# Spec-derived only (allenporter/supernote api-spec) — confirm in Phase 0.
PATH_RANDOM_CODE = "/api/official/user/query/random/code"
# Device/terminal login (equipment=3), NOT the web account login endpoint
# (login/new, equipment=1). This matters for 2FA/TOTP: Mike's account uses TOTP,
# and a real Supernote tablet has no way to prompt for a 2FA code during
# unattended background sync — so whatever login path real devices use for
# ongoing sync must not require interactive TOTP entry every time. The device
# endpoint is the natural candidate. UNVERIFIED until tested against a real
# account in Phase 0 — see the module docstring and the plan's open item on this.
PATH_LOGIN_EQUIPMENT = "/api/official/user/account/login/equipment"
PATH_DOWNLOAD_V3 = "/api/file/3/files/download_v3"

DEVICE_TYPE_TERMINAL = 3  # per spec: 1=Web, 2=App, 3=Terminal/Device, 4=Platform
LOGIN_METHOD_EMAIL = "2"


class SupernoteError(Exception):
    """Base error for anything the Supernote API itself rejects (non-2xx or success=false)."""


class SupernoteAuthError(SupernoteError):
    pass


@dataclass(frozen=True)
class SupernoteEntry:
    """One file or folder from a list_folder response (EntriesVO)."""

    id: str
    name: str
    path_display: str
    is_folder: bool
    content_hash: str | None
    size: int | None
    last_update_time_ms: int | None
    parent_path: str | None


def _hash_password(password: str, random_code: str) -> str:
    """SHA256(MD5(plain) + randomCode) per the documented LoginDTO password scheme."""
    md5_pw = hashlib.md5(password.encode("utf-8")).hexdigest()  # noqa: S324 - protocol-mandated, not our choice
    return hashlib.sha256((md5_pw + random_code).encode("utf-8")).hexdigest()


class SupernoteClient:
    """Client for one Supernote Private Cloud account, mimicking a device's sync session."""

    def __init__(
        self,
        base_url: str,
        equipment_no: str = "home-pkms-ingestion",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._equipment_no = equipment_no
        self._http = http_client or httpx.Client(base_url=self._base_url, timeout=30.0)
        self._token: str | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SupernoteClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- auth --------------------------------------------------------------------

    def login(self, account: str, password: str, totp_code: str | None = None) -> None:
        """Log in as a device/terminal (not a web session — see PATH_LOGIN_EQUIPMENT).

        Verified live (2026-07-09) against a real Supernote Private Cloud instance
        using a disposable test account with a known password, both this
        device/terminal path and the web account login path. The critical detail,
        undocumented anywhere and found only by diffing a real captured browser
        exchange: **the `timestamp` field in the login request must be the exact
        timestamp echoed back from the randomCode response, not a freshly generated
        client timestamp.** The server has no session/cookie state (confirmed no
        Set-Cookie on the randomCode response) — it evidently uses account+timestamp
        as a stateless lookup key for the code it issued, so a mismatched timestamp
        makes the server unable to find/validate the code at all, surfacing as a
        generic "password error" indistinguishable from an actually-wrong password.
        This cost a lot of blind guessing before being caught — don't regenerate
        `timestamp` independently in any future changes here.

        Confirmed live (2026-07-09) with MFA re-enabled on Mike's real account:
        this device/terminal login path succeeds with no MFA challenge at all,
        exactly like the physical tablet. Not a hypothesis anymore.

        totp_code is accepted but NOT wired into the request body — no confirmed
        field name exists for it, since it was never actually needed in testing.
        """
        random_code, code_timestamp = self._get_random_code(account)
        hashed = _hash_password(password, random_code)
        body = {
            "account": account,
            "password": hashed,
            "equipment": DEVICE_TYPE_TERMINAL,
            "loginMethod": LOGIN_METHOD_EMAIL,
            "equipmentNo": self._equipment_no,
            "timestamp": code_timestamp,
        }
        if totp_code:
            # Best-effort placeholder field name — unconfirmed, never exercised.
            body["totpCode"] = totp_code
        data = self._post(PATH_LOGIN_EQUIPMENT, body, authenticated=False)
        token = data.get("token")
        if not token:
            raise SupernoteAuthError("login succeeded but response had no token")
        self._token = token

    def _get_random_code(self, account: str) -> tuple[str, int]:
        """Returns (randomCode, timestamp) — both must be used together for login."""
        data = self._post(PATH_RANDOM_CODE, {"account": account}, authenticated=False)
        random_code = data.get("randomCode")
        timestamp = data.get("timestamp")
        if not random_code or timestamp is None:
            raise SupernoteAuthError("randomCode response missing randomCode or timestamp")
        return random_code, timestamp

    # -- sync session --------------------------------------------------------------

    def sync_start(self) -> None:
        self._post(PATH_SYNC_START, {"equipmentNo": self._equipment_no})

    def sync_end(self, success: bool = True) -> None:
        self._post(PATH_SYNC_END, {"equipmentNo": self._equipment_no, "flag": "Y" if success else "N"})

    # -- files -----------------------------------------------------------------

    def list_folder(self, path: str = "/", recursive: bool = True) -> list[SupernoteEntry]:
        data = self._post(
            PATH_LIST_FOLDER,
            {"path": path, "recursive": recursive, "equipmentNo": self._equipment_no},
        )
        return [_parse_entry(raw) for raw in data.get("entries", [])]

    def get_download_url(self, file_id: str) -> str:
        data = self._post(PATH_DOWNLOAD_V3, {"id": file_id, "equipmentNo": self._equipment_no})
        url = data.get("url")
        if not url:
            raise SupernoteError(f"download_v3 response had no url for file {file_id}")
        return url

    def download(self, file_id: str) -> bytes:
        url = self.get_download_url(file_id)
        resp = self._http.get(url, timeout=120.0)
        resp.raise_for_status()
        return resp.content

    # -- transport ---------------------------------------------------------------

    def _post(self, path: str, body: dict, authenticated: bool = True) -> dict:
        headers = {}
        if authenticated:
            if not self._token:
                raise SupernoteAuthError("not logged in — call login() first")
            headers["x-access-token"] = self._token

        resp = self._http.post(path, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        if data.get("success") is False:
            raise SupernoteError(f"{path} failed: {data.get('errorCode')} {data.get('errorMsg')}")
        return data


def _parse_entry(raw: dict) -> SupernoteEntry:
    return SupernoteEntry(
        id=str(raw["id"]),
        name=raw.get("name", ""),
        path_display=raw.get("path_display", ""),
        is_folder=raw.get("tag") == "folder",
        content_hash=raw.get("content_hash"),
        size=raw.get("size"),
        last_update_time_ms=raw.get("lastUpdateTime"),
        parent_path=raw.get("parent_path"),
    )
