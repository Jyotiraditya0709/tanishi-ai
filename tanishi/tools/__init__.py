"""
Tanishi Tools — Hands for the brain.

Available tool modules:
- web_search: Internet search + page fetching
- filesystem: Read, write, list, search files
- system_tools: Shell commands, system info, datetime
- self_improve: GitHub scanning, improvement proposals
"""

from typing import Any

from tanishi.tools.registry import ToolRegistry


def _register_tool_pack(registry: ToolRegistry, tools: list[Any]):
    for tool in tools:
        registry.register(tool)


def register_all_tools(brain, registry: ToolRegistry) -> dict[str, Any]:
    """
    Register all known tool packs with optional dependency guards.

    Returns metadata about loaded/missing optional packs.
    """
    from tanishi.tools.web_search import get_web_tools
    from tanishi.tools.filesystem import get_filesystem_tools
    from tanishi.tools.system_tools import get_system_tools
    from tanishi.tools.self_improve import get_self_improve_tools

    loaded_packs: list[str] = []
    missing_packs: list[str] = []
    mcp_manager = None

    _register_tool_pack(registry, get_web_tools())
    _register_tool_pack(registry, get_filesystem_tools())
    _register_tool_pack(registry, get_system_tools())
    _register_tool_pack(registry, get_self_improve_tools())
    loaded_packs.extend(["web_search", "filesystem", "system_tools", "self_improve"])

    optional_packs = [
        ("screenshot", "tanishi.tools.screenshot", "get_screenshot_tools"),
        ("email", "tanishi.tools.email_tools", "get_email_tools"),
        ("windows_auto", "tanishi.tools.windows_auto", "get_windows_tools"),
        ("browser_agent", "tanishi.tools.browser_agent", "get_browser_tools"),
        ("finance", "tanishi.tools.finance", "get_finance_tools"),
        ("multi_agent", "tanishi.tools.multi_agent", "get_multi_agent_tools"),
        ("autonomous_learn", "tanishi.tools.autonomous_learn", "get_learning_tools"),
    ]

    for pack_name, module_name, factory_name in optional_packs:
        try:
            module = __import__(module_name, fromlist=[factory_name])
            factory = getattr(module, factory_name)
            _register_tool_pack(registry, factory())
            loaded_packs.append(pack_name)
        except ImportError:
            missing_packs.append(pack_name)

    try:
        from tanishi.tools.mcp_client import get_mcp_tools, init_mcp_manager

        mcp_manager = init_mcp_manager(registry)
        _register_tool_pack(registry, get_mcp_tools())
        loaded_packs.append("mcp_client")
    except ImportError:
        missing_packs.append("mcp_client")

    if brain is not None:
        setattr(brain, "mcp_manager", mcp_manager)

    return {
        "tools_registered": len(registry.tools),
        "loaded_packs": loaded_packs,
        "missing_packs": missing_packs,
        "mcp_manager": mcp_manager,
    }
