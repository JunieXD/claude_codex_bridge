from __future__ import annotations


class CcbdClientError(RuntimeError):
    def __init__(self, message: str, *, retry_safe: bool = False) -> None:
        super().__init__(message)
        self.retry_safe = retry_safe


__all__ = ['CcbdClientError']
