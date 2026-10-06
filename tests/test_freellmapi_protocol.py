"""Shadow's backup provider against a local server that speaks the same protocol as FreeLLMAPI:
POST /v1/chat/completions with a bearer key, model "auto" and OpenAI-style tool calls."""
import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from shadow.assistant import ShadowAssistant
from tests.test_ai_fallback import make_settings

KEY = "freellmapi-test-key"


class Router(BaseHTTPRequestHandler):
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append((self.path, self.headers.get("Authorization"), body))
        if self.path != "/v1/chat/completions":
            return self._send(404, {"error": {"message": "not found"}})
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            return self._send(401, {"error": {"message": "invalid unified key"}})
        has_tool_result = any(m["role"] == "tool" for m in body["messages"])
        if body.get("tools") and not has_tool_result and body["messages"][-1]["role"] == "user" \
                and "12*8" in body["messages"][-1]["content"]:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function",
                "function": {"name": "calculate", "arguments": json.dumps({"expression": "12*8"})}}]}
        elif has_tool_result:
            message = {"role": "assistant", "content": "12 marta 8 = 96"}
        else:
            message = {"role": "assistant", "content": "Salom!"}
        self._send(200, {"id": "x", "object": "chat.completion", "model": "auto",
                         "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]})

    def _send(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class FreeLLMProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        Router.requests = []
        self.server = HTTPServer(("127.0.0.1", 0), Router)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.server.server_port}/v1"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def assistant(self, key=KEY):
        return ShadowAssistant(make_settings(
            openai_api_key="", ai_base_url=self.base, ai_api_key=key, ai_model="auto", ai_name="FreeLLM"))

    async def test_plain_reply_uses_model_auto_and_the_unified_key(self):
        answer, files = await self.assistant().reply_with_files(
            chat_title="C", history="", message="Salom", directory=Path("."))
        self.assertEqual((answer, files), ("Salom!", []))
        path, auth, body = Router.requests[0]
        self.assertEqual((path, auth, body["model"]), ("/v1/chat/completions", f"Bearer {KEY}", "auto"))

    async def test_calculator_tool_call_round_trip(self):
        assistant = self.assistant()
        answer, _ = await assistant.reply_with_files(chat_title="C", history="", message="12*8 necha?", directory=Path("."))
        self.assertEqual(answer, "12 marta 8 = 96")
        self.assertEqual(len(Router.requests), 2)
        tool_message = Router.requests[1][2]["messages"][-1]
        self.assertEqual(tool_message["role"], "tool")
        self.assertIn("96", tool_message["content"])
        self.assertEqual(assistant.last_provider, "FreeLLM")

    async def test_wrong_key_is_reported_by_the_ai_check(self):
        result = await self.assistant(key="wrong").check()
        entry = result["models"][0]
        self.assertFalse(entry["replied"])
        self.assertEqual(entry["model"], "auto (FreeLLM)")
        self.assertIn("401", entry["detail"])


if __name__ == "__main__":
    unittest.main()
