import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

API_BASE = os.getenv("MERAKI_API_BASE", "https://api.meraki.com/api/v1")
REQUEST_TIMEOUT = int(os.getenv("MERAKI_REQUEST_TIMEOUT", "30"))
MAX_RETRIES = int(os.getenv("MERAKI_MAX_RETRIES", "5"))


class MerakiRequestError(RuntimeError):
    """Structured request failure so callers can react to status and headers."""

    def __init__(
        self,
        message: str,
        status: Optional[int] = None,
        body: str = "",
        headers: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.headers = headers or {}


def build_url(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    if path.startswith("http://") or path.startswith("https://"):
        return path
    url = f"{API_BASE}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"
    return url


def request(method: str, url: str, api_key: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        method=method,
        headers={
            "X-Cisco-Meraki-API-Key": api_key,
            "Accept": "application/json",
        },
    )


def get_json(url: str, api_key: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    req = request("GET", url, api_key)
    try:
        with urllib.request.urlopen(req, timeout=timeout or REQUEST_TIMEOUT) as resp:
            data = resp.read().decode("utf-8")
            return {
                "data": json.loads(data) if data else None,
                "headers": dict(resp.headers),
                "status": resp.status,
            }
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        raise MerakiRequestError(
            f"HTTP {e.code} for {url}: {body}",
            status=e.code,
            body=body,
            headers=dict(e.headers),
        )
    except urllib.error.URLError as e:
        raise MerakiRequestError(f"Network error for {url}: {e}")


def parse_link_header(link: Optional[str]) -> Dict[str, str]:
    if not link:
        return {}
    links = {}
    for part in [p.strip() for p in link.split(",")]:
        if ";" not in part:
            continue
        url_part, rel_part = part.split(";", 1)
        url = url_part.strip()[1:-1]
        rel = rel_part.strip().split("=")[1].strip('"')
        links[rel] = url
    return links


def paged_get(
    path: str,
    api_key: str,
    params: Optional[Dict[str, Any]] = None,
    per_page_default: int = 500,
    max_retries: Optional[int] = None,
    courtesy_delay: float = 0.2,
) -> List[Any]:
    params = params or {}
    params.setdefault("perPage", per_page_default)
    url: Optional[str] = build_url(path, params)
    all_items: List[Any] = []
    retry_count = 0
    retry_limit = MAX_RETRIES if max_retries is None else max_retries

    while url:
        try:
            resp = get_json(url, api_key)
            retry_count = 0
        except MerakiRequestError as e:
            if e.status == 429 and retry_count < retry_limit:
                retry_after_header = e.headers.get("Retry-After")
                try:
                    retry_after = int(str(retry_after_header))
                except (TypeError, ValueError):
                    retry_after = 2
                time.sleep(retry_after * (2 ** retry_count))
                retry_count += 1
                continue
            raise

        data = resp["data"]
        if isinstance(data, list):
            all_items.extend(data)
        elif data is not None:
            all_items.append(data)

        links = parse_link_header(resp["headers"].get("Link"))
        url = links.get("next")
        time.sleep(courtesy_delay)

    return all_items


def get_one(path: str, api_key: str, params: Optional[Dict[str, Any]] = None) -> Any:
    return get_json(build_url(path, params), api_key)["data"]
