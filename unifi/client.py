import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class UniFiRequestError(RuntimeError):
    def __init__(self, message: str, status: Optional[int] = None, body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class UniFiClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: int = 30,
        verify_ssl: bool = True,
        courtesy_delay: float = 0.1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.courtesy_delay = courtesy_delay

    def _url(self, path: str, params: Optional[Dict[str, Any]] = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean, doseq=True)}"
        return url

    def get_json(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = self._url(path, params)
        req = urllib.request.Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "X-API-Key": self.api_key,
            },
        )
        context = None if self.verify_ssl else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=context) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise UniFiRequestError(f"HTTP {e.code} for {url}: {body[:500]}", status=e.code, body=body)
        except urllib.error.URLError as e:
            raise UniFiRequestError(f"Network error for {url}: {e}")

    @staticmethod
    def unwrap(payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload.get("data")
        return payload

    def paged_get(
        self,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        style: str = "offset",
        limit: int = 200,
    ) -> List[Any]:
        """Fetch list endpoints supporting either offset/limit or nextToken pagination."""
        params = dict(params or {})
        items: List[Any] = []

        if style == "nextToken":
            params.setdefault("pageSize", limit)
            next_token: Optional[str] = params.get("nextToken")
            while True:
                if next_token:
                    params["nextToken"] = next_token
                payload = self.get_json(path, params)
                data = self.unwrap(payload)
                if isinstance(data, list):
                    items.extend(data)
                elif data is not None:
                    items.append(data)
                next_token = payload.get("nextToken") if isinstance(payload, dict) else None
                time.sleep(self.courtesy_delay)
                if not next_token:
                    return items

        offset = int(params.get("offset") or 0)
        params.setdefault("limit", limit)
        while True:
            params["offset"] = offset
            payload = self.get_json(path, params)
            data = self.unwrap(payload)
            batch = data if isinstance(data, list) else ([] if data is None else [data])
            items.extend(batch)

            total = payload.get("totalCount") if isinstance(payload, dict) else None
            count = payload.get("count") if isinstance(payload, dict) else len(batch)
            if isinstance(total, int) and offset + int(count or 0) < total:
                offset += int(params["limit"])
                time.sleep(self.courtesy_delay)
                continue
            if len(batch) >= int(params["limit"]) and total is None:
                offset += int(params["limit"])
                time.sleep(self.courtesy_delay)
                continue
            return items

