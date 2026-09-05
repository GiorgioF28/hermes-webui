"""mcp_server.py deve funzionare sia con la libreria ``mcp`` 1.x sia con la 2.x.

hermes-agent 0.21 porta ``mcp`` da 1.26 a 2.x, dove il Server low-level non ha
piu' i decoratori ``@server.list_tools()`` / ``@server.call_tool()``: al loro
posto ``add_request_handler(method, params_type, handler)``. Il server MCP del
WebUI si registra nel modo giusto per la versione installata; questo test
verifica che gli handler di ``tools/list`` e ``tools/call`` esistano davvero,
qualunque sia la versione.
"""

from __future__ import annotations

import asyncio
import importlib
import sys

import mcp.types as mcp_types


def _fresh_module():
    sys.modules.pop("mcp_server", None)
    return importlib.import_module("mcp_server")


def _handler(server, method: str, request_type):
    if hasattr(server, "get_request_handler"):  # mcp 2.x
        return server.get_request_handler(method)
    return server.request_handlers.get(request_type)  # mcp 1.x


def test_tool_handlers_are_registered_for_the_installed_mcp_version():
    mod = _fresh_module()
    assert _handler(mod.server, "tools/list", mcp_types.ListToolsRequest) is not None
    assert _handler(mod.server, "tools/call", mcp_types.CallToolRequest) is not None


def test_call_tool_dispatch_reports_unknown_tools_as_json():
    mod = _fresh_module()
    out = asyncio.run(mod.call_tool("non_esiste", {}))
    assert len(out) == 1 and out[0].type == "text"
    assert "Unknown tool" in out[0].text


def test_list_tools_returns_the_declared_tools():
    mod = _fresh_module()
    tools = asyncio.run(mod.list_tools())
    assert tools == mod.TOOLS and len(tools) > 0
