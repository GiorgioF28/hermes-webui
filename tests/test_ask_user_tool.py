# tests/test_ask_user_tool.py
import asyncio
import threading
import time
from api import ask_user_tool
from api import clarify


def test_run_ask_user_blocks_until_resolved():
    result_box = {}

    def _runner():
        result_box["res"] = asyncio.run(
            ask_user_tool._run_ask_user("sess-1", {"question": "A o B?", "options": ["A", "B"]})
        )

    t = threading.Thread(target=_runner); t.start()

    # Wait for the pending clarify to appear, then resolve it
    pending = None
    for _ in range(200):
        pending = clarify.get_pending("sess-1")
        if pending:
            break
        time.sleep(0.02)
    assert pending is not None
    assert pending.get("question") == "A o B?"
    assert list(pending.get("choices_offered")) == ["A", "B"]

    clarify.resolve_clarify_by_id("sess-1", pending["clarify_id"], "A")
    t.join(timeout=5)
    assert result_box["res"]["content"][0]["text"] == "A"


def test_build_ask_user_server_returns_config():
    server = ask_user_tool.build_ask_user_server("sess-X")
    assert server is not None
