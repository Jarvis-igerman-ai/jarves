"""Shared fakes for the OpenAI Responses streaming API (SW-V4 M: consolidated from test_camera.py and test_tasks.py,
behaviour unchanged). Offline: no network, no key."""
from __future__ import annotations

import asyncio


class FakeStream:
    """An async-iterable event stream. `hang=True` stops after the events and never ends (a stalled stream)."""

    def __init__(self, events, hang=False):
        self.events, self.hang, self.closed = events, hang, False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e
        if self.hang:
            await asyncio.sleep(30)

    async def close(self):
        self.closed = True


class FakeResponses:
    """`client.responses` that hands out the scripted streams in order and records every request body."""

    def __init__(self, streams):
        self.streams, self.bodies = list(streams), []

    async def create(self, **body):
        self.bodies.append(body)
        return self.streams.pop(0)


class TextResponses:
    """`client.responses` that always answers one text (a vision/description reply) with fixed usage."""

    def __init__(self, text="זה ספל קפה לבן."):
        self.text, self.bodies = text, []

    async def create(self, **body):
        self.bodies.append(body)
        return FakeStream([{"type": "response.created", "response": {"id": "r1", "model": body["model"]}},
                           {"type": "response.output_text.delta", "delta": self.text},
                           {"type": "response.completed", "response": {"usage": {"input_tokens": 900,
                                                                                 "output_tokens": 20}}}])
