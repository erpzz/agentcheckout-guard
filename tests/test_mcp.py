import json
import pathlib
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from agentcheckout import AppHandler, Assessor, Database, Guard, PayPalSandbox, ThreadingHTTPServer


class NoNetworkPayPal:
    def __init__(self):
        self.calls = []

    def ready(self):
        return False

    def create(self, *_args, **_kwargs):
        self.calls.append("create")
        raise AssertionError("MCP must not create a PayPal order")

    def capture(self, *_args, **_kwargs):
        self.calls.append("capture")
        raise AssertionError("MCP must not capture a PayPal order")


class McpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paypal = NoNetworkPayPal()
        self.guard = Guard(Database(pathlib.Path(self.tmp.name) / "mcp.sqlite3"), self.paypal, Assessor())
        AppHandler.guard = self.guard
        AppHandler.token = "csrf-test"
        AppHandler.reviewer_token = "reviewer-only-test-secret"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), AppHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/mcp"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.guard.db.db.close()
        self.tmp.cleanup()

    def call(self, payload, headers=None):
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Origin": f"http://127.0.0.1:{self.server.server_port}",
        }
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=request_headers)
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.load(response)

    def test_initialize_and_tools_list_have_only_propose_and_read(self):
        _, initialized = self.call({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                    "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                                               "clientInfo": {"name": "test", "version": "1"}}})
        self.assertEqual(initialized["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("cannot approve", initialized["result"]["instructions"])
        _, result = self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                              {"MCP-Protocol-Version": "2025-11-25"})
        names = {tool["name"] for tool in result["result"]["tools"]}
        self.assertEqual(names, {"propose_purchase", "list_purchase_requests"})
        self.assertTrue(names.isdisjoint({"approve_purchase", "create_checkout", "capture_payment"}))

    def test_proposal_stays_pending_and_never_calls_paypal(self):
        _, result = self.call({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "propose_purchase", "arguments": {
                                   "item": "Notebook", "justification": "For a demo", "amount": 4.5}}},
                              {"MCP-Protocol-Version": "2025-11-25"})
        proposed = result["result"]["structuredContent"]["data"]
        self.assertEqual(proposed["status"], "PENDING_REVIEW")
        self.assertEqual(proposed["cents"], 450)
        saved = self.guard.db.get(proposed["id"])
        self.assertEqual(saved["status"], "PENDING_REVIEW")
        self.assertEqual(saved["source"], "alexa_plus_mcp")
        self.assertEqual(self.paypal.calls, [])

    def test_list_returns_sanitized_request_state(self):
        request = self.guard.request("Paper", "Research", "2.00", "test")
        self.guard.db.transition(request["id"], "PENDING_REVIEW", "APPROVED")
        _, result = self.call({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "list_purchase_requests", "arguments": {}}},
                              {"MCP-Protocol-Version": "2025-11-25"})
        item = result["result"]["structuredContent"]["data"][0]
        self.assertEqual(item["status"], "APPROVED")
        self.assertNotIn("checkout_url", item)
        self.assertNotIn("paypal_order_id", item)
        self.assertNotIn("capture_id", item)

    def test_invalid_proposals_are_rejected_without_state_change(self):
        for amount in ("4.50", True, 0, 501):
            with self.subTest(amount=amount):
                _, result = self.call({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                       "params": {"name": "propose_purchase", "arguments": {
                                           "item": "Notebook", "justification": "For a demo", "amount": amount}}},
                                      {"MCP-Protocol-Version": "2025-11-25"})
                self.assertTrue(result["result"]["isError"])
        self.assertEqual(self.guard.db.all(), [])

    def test_local_origin_and_protocol_version_are_enforced(self):
        req = urllib.request.Request(self.url, data=b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                               "Origin": "http://attacker.invalid", "MCP-Protocol-Version": "2025-11-25"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.call({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(error.exception.code, 400)

    def test_mcp_requires_streamable_http_accept_and_get_is_not_supported(self):
        req = urllib.request.Request(self.url, data=b'{"jsonrpc":"2.0","id":1,"method":"initialize"}',
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req)
        self.assertEqual(error.exception.code, 406)
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(self.url)
        self.assertEqual(error.exception.code, 405)


if __name__ == "__main__":
    unittest.main()
