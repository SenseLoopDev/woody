import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from woody.mcp_server import TOOLS, Server

ROOT = Path(__file__).resolve().parent.parent
CHARTER = json.loads((ROOT / "examples" / "woody_exp1.json").read_text())


def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def call(server, name, args, id_=1):
    resp = server.handle(rpc("tools/call", {"name": name, "arguments": args}, id_))
    return resp["result"]


def text(result):
    return result["content"][0]["text"]


class McpInProcess(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["WOODY_HOME"] = self.tmp.name
        os.environ["WOODY_OFFLINE"] = "1"
        self.server = Server()

    def tearDown(self):
        os.environ.pop("WOODY_HOME", None)
        os.environ.pop("WOODY_OFFLINE", None)
        self.tmp.cleanup()

    def test_initialize_negotiates_version_and_lists_tools(self):
        init = self.server.handle(rpc("initialize", {"protocolVersion": "2024-11-05"}))["result"]
        self.assertEqual(init["protocolVersion"], "2024-11-05")
        self.assertIn("tools", init["capabilities"])
        unknown = self.server.handle(rpc("initialize", {"protocolVersion": "1999-01-01"}))["result"]
        self.assertEqual(unknown["protocolVersion"], "2025-06-18")
        names = [t["name"] for t in self.server.handle(rpc("tools/list"))["result"]["tools"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("woody_review", names)
        for tool in TOOLS:
            self.assertEqual(tool["inputSchema"]["type"], "object")

    def test_notifications_get_no_reply_and_unknown_method_errors(self):
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(self.server.handle(rpc("nope"))["error"]["code"], -32601)
        self.assertEqual(self.server.handle(rpc("tools/call", {"name": "nope"}))["error"]["code"], -32602)
        self.assertEqual(self.server.handle(rpc("ping"))["result"], {})

    def test_full_loop_through_tools(self):
        self.assertIn("Set up woody-exp1", text(call(self.server, "woody_init_experiment", {"charter": CHARTER})))
        rows = [{"family_code": f"F{i:02d}", "band": "7-10" if i % 2 else "11+", "paid": True,
                 "channel": "nextdoor", "session": "S1"} for i in range(11)]
        self.assertIn("Added 11 booking", text(call(self.server, "woody_add_records", {"kind": "booking", "rows": rows})))
        for _ in range(6):
            call(self.server, "woody_add_record", {"kind": "survey", "fields": {"worth_price": 5, "rebook": True}})
        self.assertIn("Logged", text(call(self.server, "woody_log_note", {"text": "pulley toy was a hit", "about": "H3"})))
        self.assertIn("Registered", text(call(self.server, "woody_register_photo", {"file": "a.jpg", "consent": "B"})))
        review = text(call(self.server, "woody_review", {"today": "2026-11-10"}))
        self.assertIn("H1 Demand: 11", review)
        self.assertIn("Current reading: GO", review)
        early = call(self.server, "woody_conclude", {"save": True, "today": "2026-11-05"})
        self.assertTrue(early["isError"])
        self.assertIn("provisional", text(early))
        done = call(self.server, "woody_conclude", {"save": True, "today": "2026-11-10"})
        self.assertFalse(done["isError"])
        self.assertIn("Saved decision", text(done))
        self.assertIn("pulley", text(call(self.server, "woody_ask", {"question": "pulley toy"})))
        self.assertIn("1 decision", text(call(self.server, "woody_status", {})))

    def test_bad_inputs_become_tool_errors_not_crashes(self):
        bad = call(self.server, "woody_init_experiment", {"charter": {"experiment_id": "x"}})
        self.assertTrue(bad["isError"])
        self.assertIn("missing", text(bad))
        broken = json.loads(json.dumps(CHARTER))
        broken["decision_rules"][0]["all"][0]["h"] = "H99"
        result = call(self.server, "woody_init_experiment", {"charter": broken})
        self.assertTrue(result["isError"])
        self.assertIn("unknown hypothesis 'H99'", text(result))
        call(self.server, "woody_init_experiment", {"charter": CHARTER})
        self.assertTrue(call(self.server, "woody_register_photo", {"file": "a.jpg", "consent": "Z"})["isError"])
        self.assertIn("missing required", text(call(self.server, "woody_log_note", {})))
        self.assertTrue(call(self.server, "woody_add_record", {"kind": "booking", "fields": {"x": [1]}})["isError"])
        self.assertTrue(call(self.server, "woody_import_csv", {"kind": "booking", "path": "/etc/passwd"})["isError"])

    def test_second_experiment_in_same_folder_is_refused(self):
        call(self.server, "woody_init_experiment", {"charter": CHARTER})
        call(self.server, "woody_add_record", {"kind": "booking", "fields": {"paid": True}})
        other = json.loads((ROOT / "examples" / "pricing_test.json").read_text())
        result = call(Server(), "woody_init_experiment", {"charter": other})
        self.assertTrue(result["isError"])
        self.assertIn("already holds", text(result))


class McpOverStdio(unittest.TestCase):
    def test_round_trip_and_clean_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "WOODY_HOME": tmp, "WOODY_OFFLINE": "1", "PYTHONPATH": str(ROOT)}
            msgs = [
                rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "test", "version": "0"}}, 1),
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                rpc("tools/list", None, 2),
                rpc("tools/call", {"name": "woody_init_experiment", "arguments": {"charter": CHARTER}}, 3),
                rpc("tools/call", {"name": "woody_status", "arguments": {}}, 4),
            ]
            stdin = "\n".join(json.dumps(m) for m in msgs) + "\nnot json\n"
            proc = subprocess.run([sys.executable, "-m", "woody.mcp_server"], input=stdin, capture_output=True,
                                  text=True, env=env, timeout=30, cwd=tmp)
            lines = [l for l in proc.stdout.splitlines() if l.strip()]
            parsed = [json.loads(l) for l in lines]  # every stdout line must be protocol JSON
            self.assertTrue(all(p["jsonrpc"] == "2.0" for p in parsed))
            by_id = {p["id"]: p for p in parsed}
            self.assertEqual(by_id[1]["result"]["serverInfo"]["name"], "woody-brain")
            self.assertEqual(len(by_id[2]["result"]["tools"]), len(TOOLS))
            self.assertIn("Set up woody-exp1", by_id[3]["result"]["content"][0]["text"])
            self.assertIn("6 hypothesis", by_id[4]["result"]["content"][0]["text"])
            self.assertEqual(by_id[None]["error"]["code"], -32700)
            self.assertEqual(len(parsed), 5)  # 4 replies plus the parse error; the notification got none


if __name__ == "__main__":
    unittest.main()
