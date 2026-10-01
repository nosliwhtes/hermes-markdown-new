"""Classic Hermes plugin registration for Markdown.new."""

from .adapter import MarkdownNewAdapter
from .provider import MarkdownNewWebProvider
from .tool import MarkdownNewTool, TOOL_SCHEMA


def register(ctx) -> None:
    adapter = MarkdownNewAdapter()
    provider = MarkdownNewWebProvider(adapter)
    tool = MarkdownNewTool(adapter)
    ctx.register_web_search_provider(provider)
    ctx.register_tool(
        name="markdown_new",
        toolset="markdown_new",
        schema=TOOL_SCHEMA,
        handler=tool.handle,
        check_fn=adapter.is_available,
        is_async=True,
        description=TOOL_SCHEMA["description"],
    )
    ctx.register_hook("pre_tool_call", tool.pre_tool_call)