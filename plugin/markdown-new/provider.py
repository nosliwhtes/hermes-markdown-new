"""Native Hermes WebSearchProvider normalization for Markdown.new."""

from typing import Any, Dict, List, Optional

from agent.web_search_provider import WebSearchProvider

from .adapter import MarkdownNewAdapter


class MarkdownNewWebProvider(WebSearchProvider):
    NAME = "markdown-new"
    DISPLAY_NAME = "Markdown.new"

    def __init__(self, adapter: Optional[MarkdownNewAdapter] = None) -> None:
        self.adapter = adapter or MarkdownNewAdapter()

    @property
    def name(self) -> str:
        return self.NAME

    @property
    def display_name(self) -> str:
        return self.DISPLAY_NAME

    def is_available(self) -> bool:
        return self.adapter.is_available()

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            result = self.adapter.search(query, n=min(max(int(limit), 1), 5))
            rows = []
            for index, item in enumerate(result.get("results") or []):
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or item.get("link") or ""),
                        "description": str(
                            item.get("description") or item.get("snippet") or ""
                        ),
                        "position": item.get("position", index + 1),
                    }
                )
            return {"success": True, "data": {"web": rows}}
        except Exception as error:
            return {"success": False, "error": str(error)}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        del kwargs
        rows = []
        for url in urls:
            try:
                result = self.adapter.convert_url(url)
                content = result.get("content")
                if not isinstance(content, str):
                    raise RuntimeError("service response is missing content")
                metadata = result.get("metadata")
                row = {
                    "url": url,
                    "title": str(result.get("title") or ""),
                    "content": content,
                    "raw_content": content,
                }
                if isinstance(metadata, dict):
                    row["metadata"] = metadata
                rows.append(row)
            except Exception as error:
                rows.append(
                    {
                        "url": url,
                        "title": "",
                        "content": "",
                        "raw_content": "",
                        "error": str(error),
                    }
                )
        return rows
