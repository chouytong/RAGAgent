from typing import Protocol


class Embedder(Protocol):
    @property
    def fingerprint(self) -> str: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
