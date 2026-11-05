import ast
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
import aiohttp

source = Path(__file__).resolve().parents[1] / '__init__.py'
node = next(n for n in ast.parse(source.read_text(encoding='utf-8')).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_setup_entry')
node.returns = None
for arg in node.args.args: arg.annotation = None
module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
class NotReady(Exception): pass
class Client:
    error = None
    instance = None
    def __init__(self, **kwargs): self.closed=False; Client.instance=self
    async def authenticate(self): raise self.error
    async def close(self): self.closed=True
class Journal:
    @staticmethod
    def drain(*args): pass
    @staticmethod
    def unbind_entry(*args): pass
async def executor(fn,*args): return fn(*args)
ns = {'DOMAIN':'powmr_inverter','InverterApiClient':Client,'ConfigEntryNotReady':NotReady,'aiohttp':aiohttp,'asyncio':asyncio,'_LOGGER':logging.getLogger(__name__),'evidence_journal':Journal}
exec(compile(module, str(source), 'exec'), ns)
class SetupRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_wrapped_network_timeout_requests_ha_retry_and_closes(self):
        cause=TimeoutError('offline'); err=Exception('connection failed'); err.__cause__=cause; Client.error=err
        with self.assertRaises(NotReady):
            await ns['async_setup_entry'](SimpleNamespace(data={},async_add_executor_job=executor),SimpleNamespace(data={'email':'x','password':'x'},options={},unique_id='id',entry_id='id'))
        self.assertTrue(Client.instance.closed)
    async def test_non_network_failure_is_not_reclassified(self):
        Client.error=ValueError('invalid credentials')
        result=await ns['async_setup_entry'](SimpleNamespace(data={},async_add_executor_job=executor),SimpleNamespace(data={'email':'x','password':'x'},options={},unique_id='id',entry_id='id'))
        self.assertFalse(result); self.assertTrue(Client.instance.closed)
if __name__ == '__main__': unittest.main()
