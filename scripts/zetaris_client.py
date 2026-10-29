"""Restricted direct REST client; protocol verified against the local OpenAPI spec.

No arbitrary SQL, login flow, environment mutation, automatic retry, or decoding
of compatibility-exported evidence. See docs/zetaris_rest.md for the contract.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from http.client import HTTPException
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
SQL_PATH = "/sql-editor/sqls/run"
PING_SQL = "SELECT 1 AS one"
LARGE_TRANSFERS_SQL = (
    "SELECT event_id, bytes_transferred "
    "FROM ARGUS_SECURITY.resource_access_logs_compat_test "
    "WHERE bytes_transferred IN (2400000000, 950000000)"
)
TABLES = ("users", "resources", "approved_networks", "authentication_logs",
          "resource_access_logs", "security_alerts")
COUNT_QUERIES = MappingProxyType({
    table: f"SELECT COUNT(*) AS total FROM ARGUS_SECURITY.{table}_compat_test" for table in TABLES
})
PREVIEW_QUERIES = MappingProxyType({
    table: f"SELECT * FROM ARGUS_SECURITY.{table}_compat_test" for table in TABLES
})
_APPROVED_SQL = MappingProxyType({" ".join(sql.split()).upper(): sql for sql in
                                   (PING_SQL, LARGE_TRANSFERS_SQL, *COUNT_QUERIES.values(), *PREVIEW_QUERIES.values())})


class ZetarisError(RuntimeError):
    """Safe diagnostics contain only static text, HTTP codes and generated UUIDs."""

    def __init__(self, message: str, *, request_id: str | None = None,
                 status_code: int | None = None) -> None:
        self.request_id = request_id
        self.status_code = status_code
        suffix = f" [request_id={request_id}]" if request_id else ""
        super().__init__(message + suffix)


class ConfigurationError(ZetarisError):
    pass


class SQLPolicyError(ZetarisError):
    pass


class AuthenticationError(ZetarisError):
    pass


class RequestError(ZetarisError):
    pass


class ConnectionError(ZetarisError):
    pass


class RequestTimeout(ZetarisError):
    pass


class ProtocolError(ZetarisError):
    pass


class ResultLimitError(ZetarisError):
    pass


def _read_settings(path: Path) -> dict[str, str]:
    """Small literal dotenv subset; never evaluate shell syntax or interpolate."""
    try:
        with path.open(encoding="utf-8-sig") as handle:
            text = handle.read(65_537)
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError):
        raise ConfigurationError("Cannot read local environment configuration") from None
    if len(text) > 65_536:
        raise ConfigurationError("Local environment configuration exceeds 64 KiB")
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", key.strip()):
            raise ConfigurationError(f"Invalid local configuration syntax at line {number}")
        key, value = key.strip(), value.strip()
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ConfigurationError(f"Unclosed local configuration quote at line {number}")
            value = value[1:-1]
        if key in result:
            raise ConfigurationError(f"Duplicate local configuration setting at line {number}")
        result[key] = value
    return result


@dataclass(frozen=True)
class ZetarisConfig:
    rest_url: str
    org_id: str
    api_key: str = field(repr=False)
    timeout: float = 30.0
    query_limit: int = 1000
    max_response_bytes: int = 8 * 1024 * 1024

    def __post_init__(self) -> None:
        try:
            if not isinstance(self.rest_url, str) or any(ord(char) < 33 for char in self.rest_url):
                raise ValueError
            url = urlsplit(self.rest_url)
            port = url.port  # Validate the port even though no rewrite is needed.
            if (url.scheme not in {"http", "https"} or not url.hostname or url.username is not None
                    or url.password is not None or url.query or url.fragment
                    or url.path.rstrip("/") != "/api/v1.0" or port == 0):
                raise ValueError
            try:
                local = ipaddress.ip_address(url.hostname).is_loopback
            except ValueError:
                local = url.hostname.lower() == "localhost"
            if url.scheme == "http" and not local:
                raise ValueError
        except (TypeError, ValueError):
            raise ConfigurationError("ZETARIS_REST_URL must be an HTTPS API base ending /api/v1.0; HTTP is allowed only on loopback") from None
        if not isinstance(self.org_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", self.org_id) or int(self.org_id) > 2**63 - 1:
            raise ConfigurationError("ZETARIS_ORG_ID must be a positive int64 organisation ID")
        if not isinstance(self.api_key, str) or not self.api_key or any(not 33 <= ord(char) <= 126 for char in self.api_key):
            raise ConfigurationError("ZETARIS_API_KEY must be a nonempty visible-ASCII token without whitespace")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not 0 < self.timeout <= 600 or not math.isfinite(self.timeout)):
            raise ConfigurationError("ZETARIS_TIMEOUT_SECONDS must be greater than zero and at most 600")
        if type(self.query_limit) is not int or not 1 <= self.query_limit <= 10_000:
            raise ConfigurationError("ZETARIS_QUERY_LIMIT must be an integer from 1 to 10000")
        if type(self.max_response_bytes) is not int or not 1024 <= self.max_response_bytes <= 64 * 1024 * 1024:
            raise ConfigurationError("ZETARIS_MAX_RESPONSE_BYTES must be between 1024 and 67108864")
        object.__setattr__(self, "rest_url", self.rest_url.rstrip("/"))

    @classmethod
    def from_environment(cls, env_file: Path = ROOT / ".env.local", *,
                         environ: Mapping[str, str] | None = None) -> ZetarisConfig:
        settings = _read_settings(env_file)
        settings.update(os.environ if environ is None else environ)
        required = ("ZETARIS_REST_URL", "ZETARIS_ORG_ID", "ZETARIS_API_KEY")
        missing = [name for name in required if not settings.get(name)]
        if missing:
            raise ConfigurationError("Missing configuration: " + ", ".join(missing))
        try:
            timeout = float(settings.get("ZETARIS_TIMEOUT_SECONDS", "30"))
            limit = int(settings.get("ZETARIS_QUERY_LIMIT", "1000"))
            maximum = int(settings.get("ZETARIS_MAX_RESPONSE_BYTES", str(8 * 1024 * 1024)))
        except (ValueError, TypeError):
            raise ConfigurationError("Invalid numeric timeout, query limit or response-size setting") from None
        return cls(settings[required[0]], settings[required[1]], settings[required[2]], timeout, limit, maximum)


def approved_sql(sql: str) -> str:
    if not isinstance(sql, str) or len(sql) > 10_000:
        raise SQLPolicyError("Only predefined ARGUS read queries are allowed")
    normalised = " ".join(sql.split())
    if normalised.endswith(";"):
        normalised = normalised[:-1].rstrip()
    result = _APPROVED_SQL.get(normalised.upper())
    if result is None:
        raise SQLPolicyError("Only predefined ARGUS read queries are allowed")
    return result  # Send our fixed statement, never the caller's text.


@dataclass(frozen=True, repr=False)
class QueryResult:
    headers: tuple[str, ...] | None
    rows: tuple[tuple[Any, ...], ...] | None
    total: int
    time_used: int
    request_id: str
    query_id: str
    limit: int
    raw_response: dict[str, Any]
    response_bytes: bytes

    def __repr__(self) -> str:
        return f"QueryResult(total={self.total}, request_id={self.request_id})"

    @property
    def possibly_truncated(self) -> bool:
        return self.rows is not None and (len(self.rows) >= self.limit or self.total > len(self.rows))

    def records(self) -> list[dict[str, Any]]:
        if self.headers is None or self.rows is None or len(set(self.headers)) != len(self.headers):
            raise ProtocolError("Named records require present, unique headers and present data", request_id=self.request_id)
        return [dict(zip(self.headers, row)) for row in self.rows]


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-standard JSON numeric constant")


def parse_result(payload: bytes, *, request_id: str, query_id: str, limit: int) -> QueryResult:
    try:
        raw = json.loads(payload.decode("utf-8"), parse_float=Decimal, object_pairs_hook=_unique_object,
                         parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ProtocolError("Zetaris returned malformed JSON", request_id=request_id) from None
    if not isinstance(raw, dict) or any(type(raw.get(key)) is not int or raw[key] < 0 for key in ("total", "timeUsed")):
        raise ProtocolError("Expected HeaderRows with integer total and timeUsed", request_id=request_id)
    headers, rows = raw.get("headers"), raw.get("data")
    # OpenAPI marks headers/data optional. Preserve absence/null as None, not [].
    if headers is not None and (not isinstance(headers, list) or not all(isinstance(value, str) for value in headers)):
        raise ProtocolError("Invalid result headers", request_id=request_id)
    if rows is not None:
        if not isinstance(rows, list) or (rows and headers is None):
            raise ProtocolError("Invalid result data", request_id=request_id)
        if len(rows) > limit:
            raise ResultLimitError("Server returned more rows than the requested limit", request_id=request_id)
        for row in rows:
            if (not isinstance(row, list) or len(row) != len(headers or [])
                    or not all(value is None or isinstance(value, (str, int, Decimal, bool)) for value in row)):
                raise ProtocolError("Invalid row width or cell type", request_id=request_id)
    return QueryResult(None if headers is None else tuple(headers),
                       None if rows is None else tuple(tuple(row) for row in rows),
                       raw["total"], raw["timeUsed"], request_id, query_id, limit, raw, payload)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


class ZetarisClient:
    def __init__(self, config: ZetarisConfig) -> None:
        self.config = config
        # No system/environment proxy may receive the bearer key. TLS verification
        # remains enabled by the stdlib. No cookie jar or login/session is used.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def execute_read_only(self, sql: str) -> QueryResult:
        statement = approved_sql(sql)
        return self._execute(statement, self.config.query_limit)

    def execute_evidence_query(self, query: Any, *, limit: int = 200) -> QueryResult:
        """Only closed EvidenceQuery templates may extend the fixed SQL catalogue."""
        if __package__:
            from .evidence_queries import EvidenceQuery
        else:
            from evidence_queries import EvidenceQuery
        if type(query) is not EvidenceQuery or type(limit) is not int or not 1 <= limit <= self.config.query_limit:
            raise SQLPolicyError("Invalid bounded evidence query")
        try:
            statement = EvidenceQuery.sql(query)
        except (ValueError, TypeError, AttributeError):
            raise SQLPolicyError("Invalid bounded evidence query") from None
        return self._execute(statement, limit)

    def _execute(self, statement: str, limit: int) -> QueryResult:
        request_id, query_id = str(uuid.uuid4()), str(uuid.uuid4())
        body = {"queryId": query_id, "sql": statement, "source": "SqlEditor", "limit": limit}
        endpoint = self.config.rest_url + SQL_PATH
        request = Request(endpoint, data=json.dumps(body).encode("utf-8"), method="POST", headers={
            "Authorization": f"Bearer {self.config.api_key}", "X-Org-ID": self.config.org_id,
            "X-Request-ID": request_id, "Content-Type": "application/json", "Accept": "application/json",
            "Accept-Encoding": "identity",
        })
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                if response.geturl() != endpoint:
                    raise ProtocolError("Unexpected response endpoint", request_id=request_id)
                if response.status != 200:
                    raise ProtocolError("Expected HTTP 200 for SQL execution", request_id=request_id)
                media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                if media_type != "application/json":
                    raise ProtocolError("Expected application/json response", request_id=request_id)
                payload = response.read(self.config.max_response_bytes + 1)
                if len(payload) > self.config.max_response_bytes:
                    raise ResultLimitError("Response exceeds configured byte limit", request_id=request_id)
        except HTTPError as error:
            code = error.code
            error.close()
            if code in (401, 403):
                raise AuthenticationError(f"Zetaris authentication/authorisation failed (HTTP {code})",
                                          request_id=request_id, status_code=code) from None
            if 300 <= code < 400:
                raise RequestError("Redirect refused; configure the final REST endpoint",
                                   request_id=request_id, status_code=code) from None
            raise RequestError(f"Zetaris request failed (HTTP {code}); server body suppressed",
                               request_id=request_id, status_code=code) from None
        except (TimeoutError, URLError, OSError, HTTPException) as error:
            reason = error.reason if isinstance(error, URLError) else error
            if isinstance(reason, TimeoutError):
                raise RequestTimeout("Zetaris request timed out", request_id=request_id) from None
            if isinstance(reason, PermissionError):
                raise ConnectionError("Network access denied; check execution-environment permissions", request_id=request_id) from None
            raise ConnectionError("Cannot reach the configured Zetaris endpoint", request_id=request_id) from None
        return parse_result(payload, request_id=request_id, query_id=query_id, limit=limit)


def exact_integer(value: Any) -> int:
    """Explicit validation adapter for live checks; raw query values are untouched."""
    if type(value) is int:
        return value
    if isinstance(value, str) and len(value) <= 128 and re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]{1,3})?", value):
        number = Decimal(value)
    elif isinstance(value, Decimal):
        number = value
    else:
        raise ProtocolError("Expected an exact integer or an exact numeric string")
    if not number.is_finite() or number != number.to_integral_value() or abs(number) >= Decimal("1e128"):
        raise ProtocolError("Numeric result is not a bounded exact integer")
    return int(number)
