"""Helpers for parallel work whose children must not outlive their owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Iterable
from typing import TypeVar

T = TypeVar("T")


async def cancel_and_wait(tasks: Iterable[asyncio.Future]) -> None:
    """Cancel children and await cleanup despite repeated caller cancellation."""
    children = list(tasks)
    for task in children:
        if not task.done():
            task.cancel()
    cleanup = asyncio.gather(*children, return_exceptions=True)
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    cleanup.result()


async def gather_cancel_on_error(*awaitables: Awaitable[T]) -> list[T]:
    """Gather in input order; cancel and await siblings on failure or cancellation."""
    tasks = [asyncio.ensure_future(awaitable) for awaitable in awaitables]
    batch = asyncio.gather(*tasks)
    try:
        return await asyncio.shield(batch)
    except BaseException:
        await cancel_and_wait(tasks)
        if not batch.cancelled():
            batch.exception()
        raise
