"""A stand-in for Home Assistant with the semantics the BLE code relies on.

The BLE runner persists two files (the attempt budget and the teardown
record). Both writes have to leave the event loop, and Home Assistant
reports a blocking file write inside the loop as a warning with a full
traceback dump -- that is what happened in production on 2026-10-26.

``async_add_executor_job`` here really runs the function in an executor
thread, exactly like Home Assistant does. A test therefore fails if the
production code writes the file on the event loop instead of going through
the executor: the call is recorded in ``executor_calls``.
"""
import asyncio
import types


class FakeHass:
    """The parts of Home Assistant the BLE runner uses."""

    def __init__(self, config_dir):
        self.config = types.SimpleNamespace(config_dir=str(config_dir))
        self.data = {}
        self.executor_calls = []
        self.task_calls = []

    async def async_add_executor_job(self, func, *args):
        self.executor_calls.append(func)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func, *args)

    def async_create_task(self, coro, name=None, eager_start=True):
        self.task_calls.append(coro)
        return asyncio.get_running_loop().create_task(coro)
