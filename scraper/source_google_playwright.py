from __future__ import annotations

from scraper.models import Search, SearchResult


def search(search_item: Search, *, currency: str, cabin: str, adults: int, fetched_at: str) -> SearchResult:
    del currency, cabin, adults
    return SearchResult(
        key=search_item.key,
        route_id=search_item.route_id,
        origin=search_item.origin,
        dest=search_item.dest,
        depart=search_item.depart,
        return_date=search_item.return_date,
        ok=False,
        error="playwright adapter is not built; set settings.source to fast_flights",
        options=[],
        cheapest=None,
        google_url="",
        fetched_at=fetched_at,
    )
