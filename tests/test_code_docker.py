"""Opt-in real controlled Docker tests; no pulls, host fallback, mail or publishing."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest

from widemind.tools.code import CodeAdapter, _SOCKET
from widemind.tools.contracts import ToolError
from test_code_adapter import context


IMAGE = os.environ.get("WIDEMIND_TEST_CODE_IMAGE")
pytestmark = pytest.mark.skipif(not IMAGE, reason="Explicit locally provisioned pinned WIDEMIND_TEST_CODE_IMAGE required")


def execute(tmp_path, code, **limits):
    ctx = context(tmp_path, image=IMAGE, **limits)
    return ctx, CodeAdapter().execute({"code": code}, ctx)


def assert_removed(ctx):
    assert CodeAdapter()._inspect(ctx) is None


def test_real_private_inputs_artifacts_no_network_readonly_and_nonroot(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDEMIND_HOST_SENTINEL", "not-for-generated-code")
    (tmp_path / "input.txt").write_text("controlled fixture")
    ctx = context(tmp_path, image=IMAGE)
    code = '''
import os,pathlib,socket
assert os.getuid()==65532
assert 'WIDEMIND_HOST_SENTINEL' not in os.environ
assert not os.environ.get('HTTP_PROXY') and not os.environ.get('HTTPS_PROXY')
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/workspace').exists()
for path in ('/forbidden-root-write','/inputs/input.txt'):
    try:open(path,'w').write('forbidden')
    except OSError:pass
    else:raise AssertionError('write boundary failed')
try:socket.create_connection(('1.1.1.1',80),timeout=.1)
except OSError:pass
else:raise AssertionError('network isolation failed')
pathlib.Path('/work/output/result.txt').write_text(pathlib.Path('/inputs/input.txt').read_text().upper())
print('real container verified')
'''
    result = CodeAdapter().execute({"code": code, "inputs": ["input.txt"]}, ctx)
    assert result.data["exit_code"] == 0, result.data
    assert (tmp_path / result.artifacts[0]["path"]).read_text() == "CONTROLLED FIXTURE"
    assert_removed(ctx)


@pytest.mark.parametrize("code", [
    "import os;os.symlink('/etc/passwd','/work/output/link')",
    "import os;open('/work/output/a','w').write('x');os.link('/work/output/a','/work/output/b')",
    "import os;os.mkfifo('/work/output/fifo')",
])
def test_real_collector_rejects_links_and_special_files(tmp_path, code):
    ctx = context(tmp_path, image=IMAGE)
    with pytest.raises(ToolError) as caught: CodeAdapter().execute({"code": code}, ctx)
    assert caught.value.code == "artifact_rejected"
    assert_removed(ctx)


def test_real_output_limit_and_cleanup(tmp_path):
    ctx = context(tmp_path, image=IMAGE, max_output_bytes=1024)
    with pytest.raises(ToolError) as caught: CodeAdapter().execute({"code": "print('x'*1000000)"}, ctx)
    assert caught.value.code == "output_limit"
    assert_removed(ctx)


def test_real_wall_timeout_and_cancellation_cleanup(tmp_path):
    ctx = context(tmp_path, image=IMAGE, wall_seconds=1)
    started = time.monotonic()
    with pytest.raises(ToolError) as caught: CodeAdapter().execute({"code": "while True: pass"}, ctx)
    assert caught.value.code == "timeout"
    assert time.monotonic()-started < 7
    assert_removed(ctx)
    ctx = context(tmp_path, image=IMAGE)
    stop = threading.Event(); ctx.cancelled = stop.is_set
    timer = threading.Timer(.5, stop.set); timer.start()
    try:
        with pytest.raises(ToolError) as caught: CodeAdapter().execute({"code": "while True: pass"}, ctx)
        assert caught.value.code == "cancelled"
    finally:timer.cancel()
    assert_removed(ctx)


def test_real_memory_and_filesystem_caps(tmp_path):
    ctx, result = execute(tmp_path, "x=bytearray(512*1024*1024)", memory_bytes=67108864)
    assert result.data["exit_code"] != 0
    assert_removed(ctx)
    ctx, result = execute(tmp_path, '''
try:
    with open('/work/oversize','wb') as out:out.write(b'x'*(4*1024*1024))
except OSError:print('filesystem cap verified')
else:raise AssertionError('tmpfs exceeded limit')
''', work_bytes=1048576)
    assert result.data["exit_code"] == 0 and "filesystem cap verified" in result.data["stdout"]
    assert_removed(ctx)


def test_real_pid_cap_and_quiescent_background_child(tmp_path):
    ctx, result = execute(tmp_path, '''
import subprocess,sys
children=[]
try:
    for i in range(100):children.append(subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']))
except OSError:print('pid cap verified',len(children))
finally:
    for child in children:child.kill();child.wait()
''', pids=8)
    assert result.data["exit_code"] == 0 and "pid cap verified" in result.data["stdout"]
    assert_removed(ctx)
