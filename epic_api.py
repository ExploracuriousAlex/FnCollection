"""Epic Games API communication layer for Fortnite STW."""

import base64
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from dotenv import load_dotenv

load_dotenv()

_ACCOUNT_SERVICE_BASE = "https://account-public-service-prod.ol.epicgames.com"
_FN_MCP_BASE = "https://fngw-mcp-gc-livefn.ol.epicgames.com"
EPIC_CLIENT_ID = os.environ.get("EPIC_CLIENT_ID", "")
EPIC_CLIENT_SECRET = os.environ.get("EPIC_CLIENT_SECRET", "")
WORLD_INFO_URL = f"{_FN_MCP_BASE}/fortnite/api/game/v2/world/info"


def http_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: object | None = None,
    timeout: int = 60,
) -> dict:
    """Make an HTTP request and return parsed JSON."""
    request_headers = dict(headers or {})
    data: bytes | None = None

    if body is not None:
        data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")

    req = Request(url, data=data, method=method, headers=request_headers)
    with urlopen(req, timeout=timeout) as response:
        payload = json.load(response)

    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected API response for {url}: top-level is not an object")
    return payload


def get_access_token(refresh_token: str) -> str:
    """Exchange a refresh token for an access token."""
    token = base64.b64encode(
        f"{EPIC_CLIENT_ID}:{EPIC_CLIENT_SECRET}".encode("utf-8")
    ).decode("ascii")

    body = f"grant_type=refresh_token&refresh_token={quote(refresh_token, safe='')}"
    req = Request(
        f"{_ACCOUNT_SERVICE_BASE}/account/api/oauth/token",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {token}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=60) as response:
        payload = json.load(response)

    access_token = payload.get("access_token", "") if isinstance(payload, dict) else ""
    if not access_token:
        raise ValueError("OAuth refresh response did not contain an access_token")
    return access_token


def verify_token(access_token: str) -> dict:
    """Verify an access token and return account info."""
    return http_json(
        f"{_ACCOUNT_SERVICE_BASE}/account/api/oauth/verify",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )


def lookup_account(display_name: str, access_token: str) -> dict:
    """Resolve display name to account info."""
    return http_json(
        f"{_ACCOUNT_SERVICE_BASE}/account/api/public/account/displayName/{quote(display_name, safe='')}",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )


def query_profile(account_id: str, profile_id: str, access_token: str) -> dict:
    """Query an MCP profile."""
    url = (
        f"{_FN_MCP_BASE}/fortnite/api/game/v2/profile/{account_id}/client/QueryProfile"
        f"?profileId={quote(profile_id, safe='')}&rvn=-1"
    )
    return http_json(
        url,
        method="POST",
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        body={},
    )


def query_world_info(access_token: str) -> dict:
    """Fetch current world info (mission alerts, theaters)."""
    return http_json(
        WORLD_INFO_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
    )
