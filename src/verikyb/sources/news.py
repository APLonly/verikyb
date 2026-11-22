"""Adverse-media search behind an interface."""

from datetime import UTC, datetime
from typing import ClassVar, Protocol

from pydantic import AwareDatetime

from verikyb.models import DomainModel
from verikyb.sources.http import SourceClient, SourceUnavailableError


class NewsArticle(DomainModel):
    url: str
    title: str
    seen_at: AwareDatetime
    domain: str
    language: str | None = None
    source_country: str | None = None


class NewsSearch(Protocol):
    async def search(self, name: str) -> list[NewsArticle]: ...


class GdeltNews(SourceClient):
    name = "gdelt"
    base_url = "https://api.gdeltproject.org/api/v2"
    rate_limit: ClassVar[tuple[int, float]] = (1, 6.0)

    async def search(self, name: str) -> list[NewsArticle]:
        """Articles naming `name` exactly, newest first."""
        params = {
            "query": f'"{name.replace('"', "")}"',  # an exact phrase; a quote inside would end it
            "mode": "artlist",
            "format": "json",
            "maxrecords": "75",
            "sort": "datedesc",
        }
        data = await self._get_json("/doc/doc", params)
        if not isinstance(data, dict):  # a 404, or JSON that is not GDELT's answer
            raise SourceUnavailableError("gdelt did not answer the search")
        return [
            NewsArticle(
                url=article["url"],
                title=article["title"],
                seen_at=datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
                domain=article.get("domain", ""),
                language=article.get("language"),
                source_country=article.get("sourcecountry"),
            )
            for article in data.get("articles", [])
        ]
