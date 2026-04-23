"""
Async Task Utilities — Event Loop Reuse for Celery Workers

Problem:
  Each Celery task calling asyncio.run() creates and destroys an event loop,
  which is expensive for short-lived tasks and prevents connection reuse.

Solution:
  Maintain a thread-local event loop that persists across task invocations.
  The loop is lazily created on first use and reused for subsequent tasks
  in the same worker thread.

Usage:
    from logmind.core.async_task import run_async

    @celery_app.task()
    def my_task():
        run_async(my_async_function())
"""

import asyncio
import threading

from logmind.core.logging import get_logger

logger = get_logger(__name__)

_thread_local = threading.local()


def _get_or_create_loop() -> asyncio.AbstractEventLoop:
    """
    Get or create a persistent event loop for the current thread.

    Returns an existing running loop if available, otherwise creates
    a new one and stores it in thread-local storage.
    """
    loop = getattr(_thread_local, "loop", None)

    if loop is not None and not loop.is_closed():
        return loop

    # Create a new event loop for this thread
    loop = asyncio.new_event_loop()
    _thread_local.loop = loop
    logger.debug("async_task_loop_created", thread=threading.current_thread().name)
    return loop


def run_async(coro):
    """
    Run an async coroutine in a Celery worker thread.

    Reuses the thread-local event loop instead of creating a new one
    each time (as asyncio.run() does). This allows connection pools
    and async resources to be shared across task invocations.

    Args:
        coro: An awaitable coroutine to execute.

    Returns:
        The result of the coroutine.

    Raises:
        Any exception raised by the coroutine.
    """
    loop = _get_or_create_loop()
    return loop.run_until_complete(coro)


def cleanup_loop():
    """
    Close the thread-local event loop.

    Call this in Celery worker_shutdown signal handler
    to cleanly release async resources.
    """
    loop = getattr(_thread_local, "loop", None)
    if loop is not None and not loop.is_closed():
        try:
            # Cancel all pending tasks
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            logger.info("async_task_loop_closed", thread=threading.current_thread().name)
        except Exception as e:
            logger.warning("async_task_loop_cleanup_error", error=str(e))
        finally:
            _thread_local.loop = None
