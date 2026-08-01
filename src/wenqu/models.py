"""Stable data contracts shared by Wenqu library commands."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    description: str
    engine: str
    channel: str = "direct"

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class EngineFailure:
    engine: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class EngineSkip:
    engine: str
    reason: str
    configure: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class SearchEnvelope:
    query: str
    engines: tuple[str, ...]
    results: tuple[SearchResult, ...]
    partial_failures: tuple[EngineFailure, ...]
    skipped_engines: tuple[EngineSkip, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": "ok",
            "data": {
                "query": self.query,
                "engines": list(self.engines),
                "totalResults": len(self.results),
                "results": [item.as_dict() for item in self.results],
                "partialFailures": [item.as_dict() for item in self.partial_failures],
                "skippedEngines": [item.as_dict() for item in self.skipped_engines],
            },
            "error": None,
        }
