from __future__ import annotations

from ghostos.tools.base import Tool, ToolContext, ToolRegistry
from ghostos.tools.browser import BROWSER_TOOLS
from ghostos.tools.code import CODE_TOOLS
from ghostos.tools.shell import SHELL_TOOLS

__all__ = ["Tool", "ToolContext", "ToolRegistry", "default_registry"]


def default_registry() -> ToolRegistry:
    return ToolRegistry([*BROWSER_TOOLS, *SHELL_TOOLS, *CODE_TOOLS])
