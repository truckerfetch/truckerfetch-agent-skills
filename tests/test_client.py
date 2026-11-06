"""Behavior checks with synthetic credentials and a local HTTP service."""
import importlib.util
import io
import json
from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATH = Path(__file__).parents[1] / "skills/truckerfetch/scripts/client.py"
spec = importlib.util.spec_from_file_location("client", PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Client, ApiError = module.Client, module.ApiError
UID = "11111111-1111-4111-8111-111111111111"


class Handler(BaseHTTPRequestHandler):
    calls = []
    mode = "success"

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply()

    def do_POST(self):
        self.reply()

    def reply(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        type(self).calls.append((self.command, self.path, dict(self.headers), body))
        if self.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "/leak")
            self.end_headers()
            return
        if self.mode == "confirmation":
            data = {"error": {"code": "confirmation_required", "message": "do not echo tf_synthetic"},
                    "confirmation_url": f"http://127.0.0.1:{self.server.server_port}/developer/confirm/{UID}"}
            self.send_response(409)
        elif self.mode == "untrusted_confirmation":
            data = {"error": {"code": "confirmation_required"}, "confirmation_url": "https://attacker.example/developer/confirm/" + UID}
            self.send_response(409)
        elif self.mode == "throttle":
            data = {"error": {"code": "rate_limited"}, "requestId": "req_1"}
            self.send_response(429)
            self.send_header("Retry-After", "60")
        else:
            data = {"data": [{"usdot": "12345", "legal_name": "Synthetic carrier"}], "meta": {"request_id": "req_1"}, "next_cursor": "opaque"}
            self.send_response(200)
        payload = json.dumps(data).encode()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.calls = []
        Handler.mode = "success"
        self.client = Client("tf_synthetic", f"http://127.0.0.1:{self.server.server_port}", allow_insecure_local=True)

    def test_origin_and_credentials_boundaries(self):
        for origin in ("http://truckerfetch.com", "https://user:pass@truckerfetch.com", "https://truckerfetch.com/api", "https://truckerfetch.com?x=1", "https://truckerfetch.com/#x"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                Client("tf_synthetic", origin)
        with self.assertRaises(ValueError):
            Client("bad\nkey")

    def test_authenticated_json_search_and_cursor(self):
        payload = {"filters": {"states": ["TX"]}, "limit": 20, "cursor": "opaque-prior"}
        result = self.client.request("POST", "carriers/search", data=payload)
        method, path, headers, body = Handler.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/api/v1/carriers/search")
        self.assertEqual(headers["Authorization"], "Bearer tf_synthetic")
        self.assertEqual(json.loads(body), payload)
        self.assertEqual(result["next_cursor"], "opaque")

    def test_redirect_is_never_followed(self):
        Handler.mode = "redirect"
        with self.assertRaises(ApiError) as raised:
            self.client.request("GET", "account")
        self.assertEqual(raised.exception.status, 302)
        self.assertEqual(len(Handler.calls), 1)
        self.assertEqual(Handler.calls[0][1], "/api/v1/account")

    def test_mutation_needs_authorization_and_saved_key(self):
        for arguments in ({}, {"authorized": True}):
            with self.assertRaises(ValueError):
                self.client.request("POST", "exports", data={"scope": "catalog"}, **arguments)
        self.assertEqual(Handler.calls, [])
        for _ in range(2):
            self.client.request("POST", "exports", data={"scope": "catalog"}, authorized=True, idempotency_key=UID)
        self.assertEqual(Handler.calls[0][2]["Idempotency-Key"], UID)
        self.assertEqual(Handler.calls[0][3], Handler.calls[1][3])

    def test_approval_url_and_safe_error(self):
        Handler.mode = "confirmation"
        with self.assertRaises(ApiError) as raised:
            self.client.request("POST", "editions/" + UID + "/access", data={"confirm_edition_date_et": "2026-11-01"}, authorized=True, idempotency_key=UID)
        error = raised.exception
        self.assertEqual(error.code, "confirmation_required")
        self.assertTrue(error.confirmation_url.endswith("/developer/confirm/" + UID))
        self.assertNotIn("tf_synthetic", str(error))
        self.assertEqual(len(Handler.calls), 1)

    def test_cross_origin_confirmation_is_not_exposed(self):
        Handler.mode = "untrusted_confirmation"
        with self.assertRaises(ApiError) as raised:
            self.client.request("GET", "account")
        self.assertIsNone(raised.exception.confirmation_url)

    def test_429_reports_retry_after_without_retry(self):
        Handler.mode = "throttle"
        with self.assertRaises(ApiError) as raised:
            self.client.request("GET", "usage")
        self.assertEqual(raised.exception.retry_after, "60")
        self.assertEqual(raised.exception.request_id, "req_1")
        self.assertEqual(len(Handler.calls), 1)

    def test_response_body_limit(self):
        with self.assertRaises(ValueError):
            self.client._read(io.BytesIO(b"x" * (module.MAX_RESPONSE + 1)))

    def test_unknown_paths_and_body_injection_rejected(self):
        for path in ("https://attacker.example", "../account", "account?x=y", "/account", "exports/" + UID + "/parts/1?token=x"):
            with self.assertRaises(ValueError):
                self.client.request("GET", path)
        with self.assertRaises(ValueError):
            self.client.request("GET", "account", data={"x": 1})
        self.assertEqual(Handler.calls, [])


if __name__ == "__main__":
    unittest.main()
