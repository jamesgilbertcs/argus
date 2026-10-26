"""Mocked direct REST tests; normal unittest discovery never contacts Zetaris."""

from __future__ import annotations

import io
import json
import logging
import tempfile
import traceback
import unittest
import uuid
from decimal import Decimal
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler

from scripts.check_zetaris import check_live
from scripts.zetaris_client import (
    COUNT_QUERIES, LARGE_TRANSFERS_SQL, PING_SQL, PREVIEW_QUERIES, SQL_PATH,
    AuthenticationError, ConfigurationError, ConnectionError, ProtocolError,
    QueryResult, RequestError, RequestTimeout, ResultLimitError, SQLPolicyError,
    ZetarisClient, ZetarisConfig, _NoRedirects, approved_sql, exact_integer,
)


TOKEN = "synthetic-unit-test-key-NOT-A-REAL-CREDENTIAL"
BASE = "http://localhost:8888/api/v1.0"
SETTINGS = {"ZETARIS_REST_URL": BASE, "ZETARIS_ORG_ID": "1", "ZETARIS_API_KEY": TOKEN}


def config(**changes: object) -> ZetarisConfig:
    values = dict(rest_url=BASE, org_id="1", api_key=TOKEN)
    values.update(changes)
    return ZetarisConfig(**values)


def payload(headers: list[str] | None = None, rows: list[list[object]] | None = None,
            **metadata: object) -> bytes:
    body = {"headers": ["one"] if headers is None else headers,
            "data": [["1"]] if rows is None else rows, "total": 1, "timeUsed": 17}
    body.update(metadata)
    return json.dumps(body).encode("utf-8")


def response(body: bytes | None = None, *, status: int = 200,
             content_type: str = "application/json", url: str = BASE + SQL_PATH) -> Mock:
    result = Mock()
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.status = status
    result.headers = {"Content-Type": content_type}
    result.geturl.return_value = url
    result.read.side_effect = io.BytesIO(payload() if body is None else body).read
    return result


class ConfigurationTests(unittest.TestCase):
    def test_valid_configuration_hides_key_and_normalises_trailing_slash(self) -> None:
        value = config(rest_url=BASE + "/", query_limit=2000, timeout=12.5)
        self.assertEqual(value.rest_url, BASE)
        self.assertEqual(value.org_id, "1")
        self.assertEqual(value.query_limit, 2000)
        self.assertNotIn(TOKEN, repr(value))

    def test_loads_existing_file_without_modifying_it_or_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            text = (f"# local settings\nexport ZETARIS_REST_URL='{BASE}'\nZETARIS_ORG_ID=1\n"
                    f'ZETARIS_API_KEY="{TOKEN}"\nZETARIS_PASSWORD=not-used-by-client\n')
            path.write_text(text, encoding="utf-8")
            original = path.read_bytes()
            value = ZetarisConfig.from_environment(path, environ={})
            self.assertEqual(value.api_key, TOKEN)
            self.assertEqual(path.read_bytes(), original)
            value = ZetarisConfig.from_environment(path, environ={"ZETARIS_ORG_ID": "2"})
            self.assertEqual(value.org_id, "2")
            self.assertFalse(hasattr(value, "password"))

    def test_environment_only_configuration_and_numeric_options(self) -> None:
        value = ZetarisConfig.from_environment(Path("nonexistent-test-settings"), environ={
            **SETTINGS, "ZETARIS_TIMEOUT_SECONDS": "3.5", "ZETARIS_QUERY_LIMIT": "2000",
            "ZETARIS_MAX_RESPONSE_BYTES": "4096"})
        self.assertEqual(value.timeout, 3.5)
        self.assertEqual(value.query_limit, 2000)
        self.assertEqual(value.max_response_bytes, 4096)

    def test_missing_required_configuration(self) -> None:
        for key in SETTINGS:
            with self.subTest(key=key):
                settings = dict(SETTINGS)
                del settings[key]
                with self.assertRaisesRegex(ConfigurationError, key):
                    ZetarisConfig.from_environment(Path("nonexistent-test-settings"), environ=settings)

    def test_bad_urls_do_not_echo_credentials(self) -> None:
        for value in ["", "localhost:8888", "http://example.invalid/api/v1.0", "ftp://localhost/api/v1.0",
                      f"https://{TOKEN}@example.invalid/api/v1.0", f"https://example.invalid/api/v1.0?key={TOKEN}",
                      f"https://example.invalid/api/v1.0#{TOKEN}", "https://example.invalid/wrong",
                      "http://localhost:wrong/api/v1.0", "http://localhost:0/api/v1.0", "http://localhost/\napi/v1.0"]:
            with self.subTest(value=value), self.assertRaises(ConfigurationError) as caught:
                config(rest_url=value)
            self.assertNotIn(TOKEN, str(caught.exception))

    def test_https_and_explicit_loopback_addresses_are_valid(self) -> None:
        for value in ["https://example.invalid/api/v1.0", "http://127.0.0.1:8888/api/v1.0",
                      "http://[::1]:8888/api/v1.0"]:
            self.assertEqual(config(rest_url=value).rest_url, value)

    def test_bad_org_token_and_numeric_limits(self) -> None:
        cases = [("org_id", value) for value in ["", "0", "-1", "name", "1\r\nX: bad", str(2**63)]]
        cases += [("api_key", value) for value in ["", "has space", "key\nheader", "café"]]
        cases += [("timeout", value) for value in [0, -1, 601, float("nan"), float("inf"), True, "30", 10**400]]
        cases += [("query_limit", value) for value in [0, -1, 10001, 1.5, True]]
        cases += [("max_response_bytes", value) for value in [0, 100, 64 * 1024 * 1024 + 1, True]]
        for key, value in cases:
            with self.subTest(key=key, value=value), self.assertRaises(ConfigurationError):
                config(**{key: value})

    def test_bad_local_settings_are_not_echoed_or_executed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env.local"
            for text in [TOKEN, f"ZETARIS_API_KEY='{TOKEN}", f"A={TOKEN}\nA=duplicate", "A=" + "x" * 65537]:
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(ConfigurationError) as caught:
                    ZetarisConfig.from_environment(path, environ=SETTINGS)
                self.assertNotIn(TOKEN, str(caught.exception))
            path.write_text("A=$(never_execute_this)\n", encoding="utf-8")
            self.assertEqual(ZetarisConfig.from_environment(path, environ=SETTINGS).api_key, TOKEN)
            with self.assertRaises(ConfigurationError):
                ZetarisConfig.from_environment(path, environ={**SETTINGS, "ZETARIS_QUERY_LIMIT": TOKEN})


class SQLPolicyTests(unittest.TestCase):
    def test_exact_catalog_accepts_case_whitespace_and_one_terminator(self) -> None:
        for sql in [PING_SQL, LARGE_TRANSFERS_SQL, *COUNT_QUERIES.values(), *PREVIEW_QUERIES.values()]:
            self.assertEqual(approved_sql(sql), sql)
            self.assertEqual(approved_sql("  " + sql.lower().replace(" ", "\n  ") + " ;\n"), sql)

    def test_rejects_non_catalog_selects_and_all_write_or_escape_attempts(self) -> None:
        for sql in ["SELECT 2", "SELECT 1", "SELECT 1 AS one; DROP TABLE x", "SELECT 1 AS one;;",
                    "SELECT 1 AS one -- harmless", "SELECT/**/1 AS one", "WITH x AS (SELECT 1) SELECT * FROM x",
                    "SELECT * FROM evaluation.scenario_ground_truth", "SELECT * FROM ARGUS_SECURITY.users",
                    "SELECT secret()", "SHOW TABLES", "UPDATE x SET y=1", "", None, "X" * 10001]:
            with self.subTest(sql=sql), self.assertRaises(SQLPolicyError):
                approved_sql(sql)


class ClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = patch("scripts.zetaris_client.build_opener")
        self.addCleanup(self.builder.stop)
        self.mock_builder = self.builder.start()
        self.opener = self.mock_builder.return_value
        self.client = ZetarisClient(config())
        self.opener.open.return_value = response()

    def run_query(self, body: bytes) -> QueryResult:
        self.opener.open.return_value = response(body)
        return self.client.execute_read_only(PING_SQL)

    def test_verified_request_path_body_authentication_and_timeout(self) -> None:
        result = self.client.execute_read_only(" select 1 as one; ")
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, BASE + SQL_PATH)
        self.assertEqual(request.method, "POST")
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["authorization"], "Bearer " + TOKEN)
        self.assertEqual(headers["x-org-id"], "1")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 30.0})
        body = json.loads(request.data)
        self.assertEqual(set(body), {"queryId", "sql", "source", "limit"})
        self.assertEqual(body["sql"], PING_SQL)
        self.assertEqual(body["source"], "SqlEditor")
        self.assertEqual(body["limit"], 1000)
        self.assertEqual(result.request_id, headers["x-request-id"])
        self.assertEqual(result.query_id, body["queryId"])
        self.assertEqual(result.records(), [{"one": "1"}])

    def test_fresh_request_and_query_uuid_for_each_request(self) -> None:
        self.opener.open.side_effect = lambda *args, **kwargs: response()
        results = [self.client.execute_read_only(PING_SQL) for _ in range(3)]
        identifiers = [identifier for result in results for identifier in (result.request_id, result.query_id)]
        self.assertEqual(len(set(identifiers)), 6)
        for identifier in identifiers:
            self.assertEqual(uuid.UUID(identifier).version, 4)

    def test_no_request_for_disallowed_sql(self) -> None:
        with self.assertRaises(SQLPolicyError):
            self.client.execute_read_only("SELECT " + TOKEN)
        self.opener.open.assert_not_called()

    def test_empty_results(self) -> None:
        result = self.run_query(payload(headers=["event_id"], rows=[], total=0))
        self.assertEqual(result.records(), [])
        self.assertEqual(result.rows, ())
        self.assertFalse(result.possibly_truncated)

    def test_absent_fields_and_explicit_null_remain_distinguishable_in_raw_response(self) -> None:
        absent = self.run_query(b'{"total":0,"timeUsed":0}')
        null = self.run_query(b'{"headers":null,"data":null,"total":0,"timeUsed":0}')
        self.assertIsNone(absent.headers)
        self.assertIsNone(absent.rows)
        self.assertNotIn("data", absent.raw_response)
        self.assertIn("data", null.raw_response)
        with self.assertRaises(ProtocolError):
            absent.records()

    def test_missing_cells_empty_strings_encoding_and_timestamps_unchanged(self) -> None:
        values = [None, "", "London%2C UK", "|Finance|IT / Security|", "2026-09-10T10:05:00Z", "U001"]
        result = self.run_query(payload(headers=["null", "empty", "location", "departments", "timestamp", "user_id"], rows=[values]))
        self.assertEqual(result.rows, (tuple(values),))
        self.assertIsNone(result.records()[0]["null"])

    def test_integer_string_and_exponent_values_are_preserved_exactly(self) -> None:
        result = self.run_query(payload(headers=["bytes_transferred"], rows=[["2400000000"], ["9.5E8"]], total=2))
        self.assertEqual(result.rows, (("2400000000",), ("9.5E8",)))
        self.assertEqual(exact_integer(result.rows[0][0]), 2_400_000_000)
        self.assertEqual(exact_integer(result.rows[1][0]), 950_000_000)

    def test_json_integer_and_decimal_values_use_no_float_conversion(self) -> None:
        raw = b'{"headers":["big","fraction"],"data":[[9007199254740993,0.100000000000000000001]],"total":1,"timeUsed":2}'
        result = self.run_query(raw)
        self.assertIs(type(result.rows[0][0]), int)
        self.assertEqual(result.rows[0][0], 9_007_199_254_740_993)
        self.assertIs(type(result.rows[0][1]), Decimal)
        self.assertEqual(result.rows[0][1], Decimal("0.100000000000000000001"))
        self.assertEqual(result.response_bytes, raw)

    def test_preserves_unknown_response_metadata_without_logging_it(self) -> None:
        body = payload(warnings=[{"text": TOKEN}], futureField={"nested": [1, 2, 3]})
        with self.assertNoLogs(level=logging.DEBUG):
            result = self.run_query(body)
        self.assertEqual(result.raw_response["warnings"], [{"text": TOKEN}])
        self.assertEqual(result.raw_response["futureField"], {"nested": [1, 2, 3]})
        self.assertEqual(result.response_bytes, body)
        self.assertNotIn(TOKEN, repr(result))

    def test_duplicate_header_values_preserved_but_named_records_rejected(self) -> None:
        result = self.run_query(payload(headers=["x", "x"], rows=[["a", "b"]]))
        self.assertEqual(result.rows, (("a", "b"),))
        with self.assertRaises(ProtocolError):
            result.records()

    def test_rejects_invalid_response_shapes_without_echoing_server_data(self) -> None:
        cases = [[], {"msg": TOKEN}, {"total": "1", "timeUsed": 1}, {"total": True, "timeUsed": 1},
                 {"total": -1, "timeUsed": 1}, {"total": 0},
                 {"headers": TOKEN, "data": [], "total": 0, "timeUsed": 1},
                 {"headers": [TOKEN], "data": "bad", "total": 1, "timeUsed": 1},
                 {"headers": ["x"], "data": [[1, 2]], "total": 1, "timeUsed": 1},
                 {"data": [[TOKEN]], "total": 1, "timeUsed": 1},
                 {"headers": ["x"], "data": [[{"secret": TOKEN}]], "total": 1, "timeUsed": 1}]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ProtocolError) as caught:
                self.run_query(json.dumps(body).encode())
            self.assertNotIn(TOKEN, str(caught.exception))
            self.assertIsNotNone(caught.exception.request_id)

    def test_malformed_json_duplicate_keys_invalid_utf8_and_nan(self) -> None:
        for body in [b"not-json", b"\xff", TOKEN.encode(), b'{"total":1,"total":2,"timeUsed":1}',
                     b'{"headers":["x"],"data":[[NaN]],"total":1,"timeUsed":1}']:
            with self.subTest(body=body), self.assertRaises(ProtocolError):
                self.run_query(body)

    def test_http_authentication_and_server_errors_are_safe(self) -> None:
        for code, error_type in [(401, AuthenticationError), (403, AuthenticationError),
                                 (400, RequestError), (500, RequestError), (302, RequestError), (307, RequestError)]:
            self.opener.open.side_effect = HTTPError(BASE + SQL_PATH, code, TOKEN, {}, io.BytesIO(TOKEN.encode()))
            with self.subTest(code=code), self.assertNoLogs(level=logging.DEBUG), self.assertRaises(error_type) as caught:
                self.client.execute_read_only(PING_SQL)
            self.assertEqual(caught.exception.status_code, code)
            rendered = "".join(traceback.format_exception(caught.exception))
            self.assertNotIn(TOKEN, rendered)
            self.assertEqual(uuid.UUID(caught.exception.request_id).version, 4)

    def test_connection_failures_and_timeouts_do_not_leak_underlying_error(self) -> None:
        for failure, error_type in [(URLError(TOKEN), ConnectionError), (OSError(TOKEN), ConnectionError),
                                    (TimeoutError(TOKEN), RequestTimeout), (URLError(TimeoutError(TOKEN)), RequestTimeout),
                                    (URLError(PermissionError(TOKEN)), ConnectionError), (IncompleteRead(TOKEN.encode()), ConnectionError)]:
            self.opener.open.side_effect = failure
            with self.subTest(error_type=error_type), self.assertRaises(error_type) as caught:
                self.client.execute_read_only(PING_SQL)
            self.assertNotIn(TOKEN, "".join(traceback.format_exception(caught.exception)))

    def test_timeout_while_reading_response_is_handled(self) -> None:
        self.opener.open.return_value.read.side_effect = TimeoutError(TOKEN)
        with self.assertRaises(RequestTimeout):
            self.client.execute_read_only(PING_SQL)

    def test_unexpected_content_type_status_and_endpoint_are_rejected(self) -> None:
        for value in [response(content_type="text/html"), response(status=204),
                      response(url="https://other.invalid/api/v1.0" + SQL_PATH)]:
            self.opener.open.return_value = value
            with self.assertRaises(ProtocolError):
                self.client.execute_read_only(PING_SQL)

    def test_response_byte_limit_does_not_silently_truncate(self) -> None:
        client = ZetarisClient(config(max_response_bytes=1024))
        self.opener.open.return_value = response(payload(extra="x" * 2000))
        with self.assertRaises(ResultLimitError):
            client.execute_read_only(PING_SQL)

    def test_row_limit_does_not_silently_drop_rows(self) -> None:
        client = ZetarisClient(config(query_limit=1))
        self.opener.open.return_value = response(payload(rows=[["1"], ["2"]], total=2))
        with self.assertRaises(ResultLimitError):
            client.execute_read_only(PING_SQL)
        self.opener.open.return_value = response(payload(rows=[["1"]], total=20))
        result = client.execute_read_only(PING_SQL)
        self.assertTrue(result.possibly_truncated)
        self.assertEqual(result.total, 20)


class TransportAndLiveCheckerTests(unittest.TestCase):
    def test_no_redirects_or_environment_proxies(self) -> None:
        with patch.dict("os.environ", {"HTTP_PROXY": "http://other.invalid:8080", "HTTPS_PROXY": "http://other.invalid:8080"}):
            client = ZetarisClient(config())
        proxies = [handler for handler in client._opener.handlers if isinstance(handler, ProxyHandler)]
        self.assertTrue(all(handler.proxies == {} for handler in proxies))
        redirects = [handler for handler in client._opener.handlers if isinstance(handler, _NoRedirects)]
        self.assertEqual(len(redirects), 1)
        self.assertIsNone(redirects[0].redirect_request(None, None, 307, TOKEN, {}, "https://other.invalid"))

    def test_exact_integer_adapter_never_accepts_floats_or_fractional_values(self) -> None:
        for value in [1, "1", "1.0", "1E0", Decimal("1")]:
            self.assertIs(type(exact_integer(value)), int)
            self.assertEqual(exact_integer(value), 1)
        for value in [1.0, True, "NaN", "Infinity", "1.1", None, " 1 ", "1e999", Decimal("NaN")]:
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                exact_integer(value)

    def test_live_check_uses_exactly_the_three_approved_queries(self) -> None:
        with patch("scripts.zetaris_client.build_opener") as builder:
            builder.return_value.open.side_effect = [response(), response(payload(["total"], [["1000"]])),
                response(payload(["event_id", "bytes_transferred"], [["ACCESS002000", "9.5E8"], ["ACCESS001994", "2400000000"]], total=2))]
            client = ZetarisClient(config())
            messages = []
            check_live(client, messages.append)
            statements = [json.loads(call.args[0].data)["sql"] for call in builder.return_value.open.call_args_list]
        self.assertEqual(statements, [PING_SQL, COUNT_QUERIES["authentication_logs"], LARGE_TRANSFERS_SQL])
        self.assertTrue(any("raw_type=str, checked_type=int" in line for line in messages))
        self.assertFalse(any(TOKEN in line for line in messages))

    def test_live_check_rejects_wrong_values_without_printing_server_data(self) -> None:
        client = Mock()
        client.execute_read_only.return_value = QueryResult(("one",), ((TOKEN,),), 1, 1, str(uuid.uuid4()), str(uuid.uuid4()), 1000, {}, b"")
        with self.assertRaises(ProtocolError) as caught:
            check_live(client)
        self.assertNotIn(TOKEN, str(caught.exception))


if __name__ == "__main__":
    unittest.main()
