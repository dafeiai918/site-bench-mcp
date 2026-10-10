# -*- coding: utf-8 -*-
"""site-bench-mcp 测试：协议 + 评测逻辑，本地假站点"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import site_bench_mcp as sb  # noqa: E402

ANSWERS = {"9.11": "9.9", "strawberry": "3", "星期三": "星期六", "苹果": "6"}


class _Api(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._json({"data": [{"id": "mock-model"}]})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = req["messages"][-1]["content"]
        if req.get("response_format"):
            content = '{"ok": true}'
        elif req.get("stop"):
            content = "AAA "
        else:
            content = next((a for k, a in ANSWERS.items() if k in prompt), "好")
        if req.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in (content[:1], content[1:] or " "):
                chunk = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json({"choices": [{"message": {"content": content}}]})


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.server = sb.MCPServer("t", "1.0", sb.TOOLS)
        self.server._send = lambda m: self.sent.append(m)

    def _call(self, method, **kw):
        msg = {"jsonrpc": "2.0", "id": 1, "method": method}
        if kw:
            msg["params"] = kw
        self.server.handle(msg)
        return self.sent[-1]

    def test_initialize(self):
        out = self._call("initialize")
        self.assertEqual(out["result"]["serverInfo"]["name"], "t")

    def test_tools_list(self):
        names = [t["name"] for t in self._call("tools/list")["result"]["tools"]]
        self.assertEqual(sorted(names),
                         ["bench_site", "check_degradation", "check_latency", "compare_sites"])

    def test_unknown_tool(self):
        self.assertEqual(self._call("tools/call", name="x", arguments={})["error"]["code"], -32602)

    def test_missing_args_returns_text_not_crash(self):
        out = self._call("tools/call", name="check_latency", arguments={})
        self.assertFalse(out["result"]["isError"])
        self.assertIn("需要", out["result"]["content"][0]["text"])


class BenchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api = ThreadingHTTPServer(("127.0.0.1", 0), _Api)
        cls.port = cls.api.server_address[1]
        threading.Thread(target=cls.api.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.api.shutdown()

    def setUp(self):
        self.base = f"http://127.0.0.1:{self.port}/v1"

    def test_normalize(self):
        self.assertEqual(sb.normalize("https://a.com/v1"), ("https://a.com", "https://a.com/v1"))
        self.assertEqual(sb.normalize("https://a.com"), ("https://a.com", "https://a.com/v1"))

    def test_list_models(self):
        self.assertEqual(sb.list_models(self.base, "sk"), ["mock-model"])

    def test_pick_model_uses_first_when_unknown(self):
        self.assertEqual(sb.pick_model(self.base, "sk", None), "mock-model")
        self.assertEqual(sb.pick_model(self.base, "sk", "指定"), "指定")

    def test_measure_latency(self):
        body = sb.measure_latency(self.base, "sk", "mock-model")
        self.assertIsNotNone(body["first_token_s"])
        self.assertGreaterEqual(body["total_s"], 0)

    def test_measure_concurrency(self):
        body = sb.measure_concurrency(self.base, "sk", "mock-model", 2)
        self.assertEqual(body["success_rate"], 1.0)

    def test_check_params(self):
        body = sb.check_params(self.base, "sk", "mock-model")
        self.assertTrue(body["max_tokens"])
        self.assertTrue(body["json_mode"])
        self.assertTrue(body["stop"])

    def test_check_degradation_all_pass(self):
        body = sb.check_degradation(self.base, "sk", "mock-model")
        self.assertEqual(body["passed"], 4)

    def test_tool_check_latency(self):
        text = sb.tool_check_latency({"base_url": self.base, "api_key": "sk"})
        self.assertIn("延迟正常", text)

    def test_tool_bench_site(self):
        text = sb.tool_bench_site({"base_url": self.base, "api_key": "sk", "concurrency": 2})
        self.assertIn("站点体检报告", text)
        self.assertIn("参数兼容", text)
        self.assertIn("启发式", text)

    def test_tool_check_degradation(self):
        text = sb.tool_check_degradation({"base_url": self.base, "api_key": "sk"})
        self.assertIn("4/4", text)

    def test_tool_compare_sites(self):
        text = sb.tool_compare_sites({"sites": [
            {"name": "A", "base_url": self.base, "api_key": "sk"},
            {"name": "B", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk"}]})
        self.assertIn("站点横向对比", text)
        self.assertIn("失败", text)

    def test_compare_requires_two(self):
        self.assertIn("至少2个", sb.tool_compare_sites({"sites": [{"name": "A"}]}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
