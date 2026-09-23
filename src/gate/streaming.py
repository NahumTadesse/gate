import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

SSE_HEADERS = {"cache-control": "no-cache", "x-accel-buffering": "no"}


class SSEUsageParser:
    """Watches a relayed SSE byte stream and remembers the last usage object seen.

    Chunks are fed in as they arrive from upstream, so events may be split
    across chunk boundaries; bytes are buffered until a blank line ends each
    event.
    """

    def __init__(self) -> None:
        self._buffer = b""
        self.usage: dict[str, Any] | None = None

    @property
    def at_event_boundary(self) -> bool:
        return not self._buffer

    def feed(self, chunk: bytes) -> None:
        self._buffer = (self._buffer + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in self._buffer:
            event, _, self._buffer = self._buffer.partition(b"\n\n")
            self._handle_event(event)

    def _handle_event(self, event: bytes) -> None:
        data = b"\n".join(
            line[len(b"data:") :].removeprefix(b" ")
            for line in event.split(b"\n")
            if line.startswith(b"data:")
        )
        if not data or data == b"[DONE]":
            return
        try:
            payload = json.loads(data)
        except ValueError:
            return
        if isinstance(payload, dict) and isinstance(payload.get("usage"), dict):
            self.usage = payload["usage"]


def sse_event(payload: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(payload)}\n\n".encode()


class UpstreamStreamingResponse(StreamingResponse):
    """A StreamingResponse that always runs on_close when it ends.

    on_close releases the upstream stream (and records the request). Starlette
    does not close the body iterator when the client disconnects, so without
    this the upstream request would keep generating (billed) tokens until it
    finished on its own.
    """

    def __init__(
        self,
        *args: Any,
        on_close: Callable[[], Awaitable[object]],
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.on_close()
