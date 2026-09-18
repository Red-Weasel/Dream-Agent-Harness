"""Native helpers must work before a web framework imports AnyIO submodules."""
import subprocess
import sys


def test_thread_helper_works_in_fresh_process_and_keeps_session_context():
    program = '''
import asyncio
from dream.tools.context import ToolContext, bind_context, ctx, in_thread
async def main():
    with bind_context(ToolContext(None, None, None, 'isolated')):
        value = await in_thread(lambda: ctx().session_id)
        assert value == 'isolated'
        print(value)
asyncio.run(main())
'''
    result = subprocess.run([sys.executable, '-c', program], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'isolated'
