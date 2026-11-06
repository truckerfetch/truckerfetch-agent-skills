"""Bounded standard-library REST helper. No automatic retries or redirects."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlparse

UUID = r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}"
DOT = r"[0-9]{1,16}"
MAX_RESPONSE = 4 * 1024 * 1024
KEY = re.compile(r"[-._:a-zA-Z0-9]{8,128}")
READ = re.compile(
    r"(?:account|usage|editions(?:/(?:latest|" + UUID + r"))?"
    r"|carriers/" + DOT + r"(?:/(?:authorities|insurance-filings))?"
    r"|evidence/" + UUID + r"|changes|watchlists(?:/" + UUID + r"(?:/(?:targets|events))?)?"
    r"|exports(?:/" + UUID + r"(?:/(?:download|parts/[0-9]{1,6}))?)?"
    r"|webhook-endpoints|webhook-deliveries)"
)
SEARCH = re.compile(r"(?:carriers/search|renewals/search|editions/" + UUID + r"/search)")
CREATE = re.compile(r"(?:watchlists|exports|editions/" + UUID + r"/access|watchlists/" + UUID + r"/(?:items|targets))")
EDIT = re.compile(r"watchlists/" + UUID + r"(?:/rules)?")
REMOVE = re.compile(r"watchlists/" + UUID + r"(?:/(?:targets/" + UUID + r"|items/" + DOT + r"))?")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ApiError(Exception):
    """Sanitized service failure; deliberately excludes raw error bodies."""

    def __init__(self, status, code, *, request_id=None, confirmation_url=None, retry_after=None):
        self.status = status
        self.code = code
        self.request_id = request_id
        self.confirmation_url = confirmation_url
        self.retry_after = retry_after
        super().__init__(f"TruckerFetch HTTP {status}: {code}")


class Client:
    def __init__(self, api_key=None, base_url=None, *, allow_insecure_local=False, timeout=30):
        self.api_key = api_key or os.environ.get("TRUCKERFETCH_API_KEY")
        self.base_url = (base_url or os.environ.get("TRUCKERFETCH_BASE_URL") or "https://truckerfetch.com").rstrip("/")
        url = urlparse(self.base_url)
        local = url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1", "::1")
        if (not url.hostname or url.username is not None or url.password is not None
                or url.query or url.fragment or url.path):
            raise ValueError("Use a trusted origin without credentials, path, query or fragment")
        if url.scheme != "https" and not (local and allow_insecure_local):
            raise ValueError("HTTPS required; local HTTP must be explicitly enabled for testing")
        if not self.api_key or not re.fullmatch(r"[A-Za-z0-9._~-]+", self.api_key):
            raise ValueError("Set TRUCKERFETCH_API_KEY to a valid REST key")
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError("Use a timeout from 0 to 60 seconds, exclusive of zero")
        self.timeout = timeout
        # Ignore machine proxy settings: credentials go only to the chosen origin.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, *, data=None, query=None, idempotency_key=None,
                authorized=False, raw=False):
        """Call one known endpoint; mutations require existing user authorization.

        Persist payload/key before calling. A timeout never triggers a retry here.
        """
        method = method.upper()
        search = method == "POST" and SEARCH.fullmatch(path)
        mutation = ((method == "POST" and CREATE.fullmatch(path))
                    or (method == "PATCH" and EDIT.fullmatch(path))
                    or (method == "DELETE" and REMOVE.fullmatch(path)))
        if not ((method == "GET" and READ.fullmatch(path)) or search or mutation):
            raise ValueError("Unknown method or API endpoint")
        if mutation and authorized is not True:
            raise ValueError("Establish user authorization for this change before calling")
        requires_key = method == "POST" and mutation and not path.endswith("/items")
        if requires_key and (not isinstance(idempotency_key, str) or not KEY.fullmatch(idempotency_key)):
            raise ValueError("Provide a saved 8-128 character idempotency key")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not KEY.fullmatch(idempotency_key)):
            raise ValueError("Invalid idempotency key")
        if method in ("GET", "DELETE") and data is not None:
            raise ValueError("This method does not accept a request body")
        if method in ("POST", "PATCH") and not isinstance(data, dict):
            raise ValueError("Provide a JSON object body")
        if query:
            if method != "GET" or set(query) - {"cursor", "limit", "edition_id", "usdot", "watchlist_id", "since", "event_types"}:
                raise ValueError("Unsupported query parameter")
            if any(type(v) not in (str, int) for v in query.values()):
                raise ValueError("Query values must be strings or integers")
        if raw and not (method == "GET" and re.fullmatch(r"exports/" + UUID + r"/parts/[0-9]{1,6}", path)):
            raise ValueError("Raw responses are only supported for export parts")
        url = self.base_url + "/api/v1/" + path
        if query:
            url += "?" + urlencode(query)
        headers = {"Authorization": "Bearer " + self.api_key, "Accept": "application/octet-stream" if raw else "application/json"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        payload = None
        if data is not None:
            payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode("utf-8")
            if len(payload) > 1024 * 1024:
                raise ValueError("Request exceeds helper's 1 MiB body limit")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = self._read(response)
        except urllib.error.HTTPError as error:
            with error:
                try:
                    content = self._read(error)
                    body = json.loads(content)
                except (ValueError, UnicodeError):
                    body = {}
                if not isinstance(body, dict):
                    body = {}
                detail = body.get("error")
                code = detail.get("code") if isinstance(detail, dict) else None
                code = code if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code) else "http_error"
                request_id = body.get("requestId") or error.headers.get("X-Request-Id")
                if not isinstance(request_id, str) or not re.fullmatch(r"[-a-zA-Z0-9_]{1,100}", request_id):
                    request_id = None
                confirmation = self._confirmation_url(body.get("confirmation_url"))
                retry = error.headers.get("Retry-After")
                if not retry or not re.fullmatch(r"[0-9]{1,6}", retry):
                    retry = None
            raise ApiError(error.code, code, request_id=request_id,
                           confirmation_url=confirmation, retry_after=retry) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ApiError(0, "transport_error") from None
        if raw:
            return content
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError):
            raise ApiError(0, "invalid_json") from None
        if not isinstance(value, dict):
            raise ApiError(0, "invalid_envelope")
        return value

    @staticmethod
    def _read(response):
        content = response.read(MAX_RESPONSE + 1)
        if len(content) > MAX_RESPONSE:
            raise ValueError("Response exceeds helper's 4 MiB limit")
        return content

    def _confirmation_url(self, value):
        if not isinstance(value, str):
            return None
        url = urlparse(value)
        origin = urlparse(self.base_url)
        if (url.scheme == origin.scheme and url.netloc == origin.netloc
                and re.fullmatch(r"/developer/confirm/" + UUID, url.path)
                and not url.query and not url.fragment):
            return value
        return None
