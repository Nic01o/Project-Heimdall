"""Mock LCD driver: no hardware access, just keeps an in-memory character
buffer (one string per row) so tests (and dev machines) can assert on what
would be shown. Used on dev machines/CI and whenever a module's "driver"
setting is "mock" (the default).
"""

from __future__ import annotations


class MockLCDDriver:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self.rows = rows
        self.lines = [""] * rows

    async def write_line(self, row: int, text: str) -> None:
        self.lines[row] = text

    async def clear(self) -> None:
        self.lines = [""] * self.rows
