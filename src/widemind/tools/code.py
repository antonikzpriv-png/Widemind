"""Bounded Python execution in an owner-provisioned, networkless Docker image."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import PurePosixPath, Path
import re
import selectors
import signal
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict
from typing import Mapping, Any

from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Uncertain, Usage


_SOCKET = "unix:///var/run/docker.sock"
_LABEL = "io.widemind.code"
_SELECTORS = ("DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH")
_PROXIES = ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY", "NO_PROXY")
_PIN = re.compile(r"(?:sha256:[0-9a-f]{64}|[a-zA-Z0-9][a-zA-Z0-9._/:\-]*@sha256:[0-9a-f]{64})\Z")
_KEEPER = "import os,time; os.makedirs('/work/output',exist_ok=True); time.sleep(86400)"
_PROGRAM = "import os,sys;os.makedirs('/work/output',exist_ok=True);exec(compile(sys.stdin.buffer.read(),'<widemind-code>','exec'),{'__name__':'__main__'})"

# This program runs in a separate isolated interpreter from the generated code.
# Stop remaining sandbox processes, then use descriptor-relative no-follow I/O.
_COLLECTOR = r'''
import base64,json,os,signal,stat,sys,time
limits=json.loads(sys.argv[1]); me=os.getpid()
for attempt in range(50):
    active=[]
    for name in os.listdir('/proc'):
        if not name.isdigit() or int(name) in (1,me): continue
        try:
            status=open('/proc/'+name+'/status').read()
            state=next(line for line in status.splitlines() if line.startswith('State:')).split()[1]
            if state not in ('T','t','Z','X'):
                os.kill(int(name),signal.SIGSTOP);active.append(name)
        except (FileNotFoundError,ProcessLookupError): pass
    if not active: break
    time.sleep(.01)
else: raise RuntimeError('Could not quiesce sandbox descendants')
flags=os.O_RDONLY|os.O_NOFOLLOW
root=os.open('/work/output',flags|os.O_DIRECTORY)
files=[];total=0;entries=0
def walk(fd,prefix=''):
    global total,entries
    for name in sorted(os.listdir(fd)):
        entries+=1
        if entries>limits['max_artifact_files']*4+32: raise ValueError('Too many artifact entries')
        if not name or name in ('.','..') or any(c in name for c in '\\:\x00') or name.endswith((' ','.')):
            raise ValueError('Unsafe artifact name')
        relative=prefix+name
        if len(relative.encode())>500: raise ValueError('Artifact path too long')
        info=os.stat(name,dir_fd=fd,follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child=os.open(name,flags|os.O_DIRECTORY,dir_fd=fd)
            try: walk(child,relative+'/')
            finally: os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink==1:
            if len(files)>=limits['max_artifact_files']: raise ValueError('Too many artifacts')
            child=os.open(name,flags|os.O_NONBLOCK,dir_fd=fd)
            try:
                actual=os.fstat(child)
                if not stat.S_ISREG(actual.st_mode) or actual.st_nlink!=1: raise ValueError('Unsafe artifact type')
                left=limits['max_artifact_bytes']-total
                if actual.st_size>left: raise ValueError('Artifacts exceed byte limit')
                chunks=[];count=0
                while True:
                    data=os.read(child,min(65536,left-count+1))
                    if not data:break
                    count+=len(data)
                    if count>left:raise ValueError('Artifacts exceed byte limit')
                    chunks.append(data)
                total+=count
                files.append({'path':relative,'data':base64.b64encode(b''.join(chunks)).decode('ascii')})
            finally:os.close(child)
        else: raise ValueError('Links and special artifacts are forbidden')
try:walk(root)
finally:os.close(root)
print(json.dumps(files,separators=(',',':')))
'''


def _relative(path: Any) -> str:
    if (not isinstance(path, str) or not path or len(path.encode()) > 500
            or any(c in path for c in "\\:\x00") or path.startswith("/")):
        raise ToolError("invalid_arguments", "Paths must be bounded workspace-relative paths.")
    pieces = path.split("/")
    if any(p in ("", ".", "..") or p.endswith((" ", ".")) for p in pieces):
        raise ToolError("invalid_arguments", "Path traversal or ambiguous names are forbidden.")
    if any(re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", p, re.I) for p in pieces):
        raise ToolError("invalid_arguments", "Reserved filesystem names are forbidden.")
    return PurePosixPath(path).as_posix()


def _limits(config: Mapping[str, Any]) -> dict:
    if os.name != "posix":
        raise Unavailable("The Docker code adapter requires a POSIX worker host.")
    if not isinstance(config.get("image"), str) or not _PIN.fullmatch(config["image"]):
        raise Unavailable("Configure a local owner-approved image by repo@sha256 digest or sha256 image ID; tags and automatic pulls are forbidden.")
    if config.get("owner_compute_subsidy") is not True:
        raise Unavailable("Explicitly configure owner_compute_subsidy=true; host compute is excluded from the operating wallet and must be disclosed.")
    ranges = {
        "wall_seconds": (20, 1, 120), "memory_bytes": (134217728, 33554432, 536870912),
        "cpu_millis": (500, 100, 2000), "pids": (32, 8, 128),
        "work_bytes": (16777216, 1048576, 67108864), "tmp_bytes": (8388608, 1048576, 33554432),
        "max_code_bytes": (65536, 1, 262144), "max_input_bytes": (8388608, 0, 16777216),
        "max_input_files": (8, 0, 16), "max_output_bytes": (65536, 1, 262144),
        "max_artifact_bytes": (8388608, 0, 16777216), "max_artifact_files": (16, 0, 64),
    }
    limits = {}
    for name, (default, minimum, maximum) in ranges.items():
        value = config.get(name, default)
        if type(value) is not int or not minimum <= value <= maximum:
            raise Unavailable(f"Owner configuration {name} must be an integer between {minimum} and {maximum}.")
        limits[name] = value
    return limits


def _arguments(arguments, limits):
    if not isinstance(arguments, Mapping) or set(arguments) - {"code", "inputs"}:
        raise ToolError("invalid_arguments", "code.execute accepts only code and explicit inputs.")
    code = arguments.get("code")
    inputs = arguments.get("inputs", [])
    if not isinstance(code, str) or not code.strip() or len(code.encode()) > limits["max_code_bytes"]:
        raise ToolError("invalid_arguments", "Python source is empty or exceeds its byte limit.")
    if not isinstance(inputs, list) or len(inputs) > limits["max_input_files"]:
        raise ToolError("invalid_arguments", "inputs must be a bounded list of workspace file paths.")
    paths = [_relative(p) for p in inputs]
    if len({p.casefold() for p in paths}) != len(paths):
        raise ToolError("invalid_arguments", "Duplicate input paths are forbidden.")
    return code.encode(), paths


def _run(args, *, check=lambda: None, deadline=None, data=b"", cap=65536, settle_on_cancel=False):
    """Bound both streams and stdin without buffering arbitrary child output."""
    deadline = deadline or time.monotonic() + 5
    check()
    env = {k: v for k, v in os.environ.items() if k not in _SELECTORS}
    try:
        process = subprocess.Popen(["docker", "--host=" + _SOCKET, *args],
            stdin=subprocess.PIPE if data else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, start_new_session=True)
    except (OSError, ValueError) as exc:
        raise Unavailable("Docker CLI/local daemon is unavailable; provision the managed Docker runtime.") from exc
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    offset = 0
    deferred = None
    try:
        for name in buffers:
            stream = getattr(process, name)
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        if data:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        while selector.get_map():
            if deferred is None:
                try:
                    check()
                except ToolError as exc:
                    if not settle_on_cancel: raise
                    deferred, deadline = exc, time.monotonic() + 5
            if time.monotonic() >= deadline:
                if settle_on_cancel and deferred is None:
                    deferred = ToolError("timeout", "Docker execution exceeded its wall-clock limit.")
                    deadline = time.monotonic() + 5
                elif settle_on_cancel:
                    raise Uncertain("Docker creation did not settle; reconcile the durable sandbox identity before retrying.")
                else:
                    raise ToolError("timeout", "Docker execution exceeded its wall-clock limit.")
            for key, _ in selector.select(.025):
                if key.data == "stdin":
                    try:
                        offset += os.write(key.fd, data[offset:offset + 65536])
                    except BrokenPipeError:
                        offset = len(data)
                    if offset == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    block = os.read(key.fd, 65536)
                    if not block:
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                    else:
                        if sum(map(len, buffers.values())) + len(block) > cap:
                            raise ToolError("output_limit", "Docker output exceeded its configured byte limit.")
                        buffers[key.data].extend(block)
        status = process.wait(timeout=max(.001, deadline - time.monotonic()))
        if deferred is not None: raise deferred
        return status, bytes(buffers["stdout"]), bytes(buffers["stderr"])
    finally:
        selector.close()
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class CodeAdapter:
    spec = ToolSpec("code.execute", "Run bounded Python with explicit read-only inputs in a networkless Docker sandbox.", "local")

    def quote(self, arguments, config):
        limits = _limits(config)
        _arguments(arguments, limits)
        return Quote(provider="docker-owner-compute", limits=limits)

    def availability(self, config, secrets):
        try:
            _limits(config)
            self._image(config, lambda: None, time.monotonic() + 5)
            return {"available": True, "provider": "docker", "funding": "owner_compute_subsidy", "image": config["image"]}
        except ToolError as exc:
            return {"available": False, "message": exc.message, "code": exc.code}

    @staticmethod
    def _identity(ctx):
        return hashlib.sha256((ctx.run_id + "\0" + ctx.action_id).encode()).hexdigest()

    @staticmethod
    def _image(config, check, deadline):
        status, body, _ = _run(["image", "inspect", "--format", "{{.Id}}", config["image"]], check=check, deadline=deadline)
        if status or not re.fullmatch(rb"sha256:[0-9a-f]{64}\s*", body):
            raise Unavailable("Pinned Docker image is not installed or the local daemon is unavailable. Provision the approved image manually; this adapter never pulls or uses host execution.")

    def _inspect(self, ctx, *, check=lambda: None):
        state = ctx.load_state()
        name = "widemind-code-" + self._identity(ctx)[:32]
        if state.get("code_container") not in (None, name):
            raise Uncertain("Recorded sandbox identity differs from this action; manual reconciliation required.")
        fmt = '{"id":{{json .Id}},"labels":{{json .Config.Labels}},"status":{{json .State.Status}}}'
        status, body, error = _run(["inspect", "--format", fmt, name], check=check)
        if status:
            if b"No such object" in error or b"No such container" in error:
                return None
            raise Uncertain("Docker sandbox state cannot be verified; restore the local daemon before reconciliation.")
        try:
            value = json.loads(body)
            if value["labels"].get(_LABEL) != self._identity(ctx) or not re.fullmatch(r"[0-9a-f]{64}", value["id"]):
                raise ValueError("identity")
            return value
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise Uncertain("Sandbox identity/ownership could not be verified; no unrelated container was changed.") from exc

    def _cleanup(self, ctx):
        found = self._inspect(ctx)
        if found:
            status, _, _ = _run(["rm", "--force", found["id"]])
            if status or self._inspect(ctx) is not None:
                raise Uncertain("Owned Docker sandbox could not be removed; reconcile it before another attempt.")

    @staticmethod
    def _cleanup_staging(state):
        value = state.get("code_staging_directory")
        if not value: return
        path = Path(value)
        if (path.parent != Path(tempfile.gettempdir()) or not re.fullmatch(r"widemind-code-input-[a-z0-9_]{8}", path.name)
                or path.is_symlink()):
            raise Uncertain("Recorded input staging identity is invalid; manual cleanup required.")
        if path.exists():
            if path.stat().st_uid != os.getuid():
                raise Uncertain("Input staging ownership changed; no unrelated files were removed.")
            for parent, folders, _ in os.walk(path, followlinks=False):
                Path(parent).chmod(0o700)
            shutil.rmtree(path)

    @staticmethod
    def _usage(start, **units):
        return Usage(units={"wall_ms": max(0, int((time.monotonic() - start) * 1000)), **units},
            details={"funding": "owner_compute_subsidy", "excluded_costs": "owner host compute, storage and image/network setup", "cpu_usage_measured": False})

    @staticmethod
    def _cached(state):
        record = state.get("code_result")
        if record:
            return ToolResult(record["data"], Usage(**record["usage"]), record.get("artifacts", []))
        return None

    def execute(self, arguments, ctx: ToolContext):
        ctx.check()
        limits = _limits(ctx.config)
        code, inputs = _arguments(arguments, limits)
        request_hash = hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()
        old = ctx.load_state()
        if old.get("code_container"):
            if old.get("code_request_sha256") != request_hash:
                raise Uncertain("Action arguments changed; do not reuse an execution action ID.")
            recovered = self.reconcile(arguments, ctx)
            if recovered.outcome == "completed":
                return recovered.result
            if recovered.outcome != "not_executed":
                raise Uncertain(recovered.detail)
        start = time.monotonic()
        deadline = min(ctx.deadline, start + limits["wall_seconds"])
        self._image(ctx.config, ctx.check, deadline)
        identity = self._identity(ctx)
        name = "widemind-code-" + identity[:32]
        input_bytes = 0
        launched = False
        with tempfile.TemporaryDirectory(prefix="widemind-code-input-") as directory:
            # Keep an owner-only parent; only its selected-input child is
            # readable by the nonroot sandbox identity through the RO bind.
            staging = Path(directory) / "inputs"
            staging.mkdir()
            for relative in inputs:
                ctx.check()
                data = ctx.workspace.read_bytes(relative, max_bytes=limits["max_input_bytes"] - input_bytes)
                input_bytes += len(data)
                if input_bytes > limits["max_input_bytes"]:
                    raise ToolError("input_limit", "Explicit inputs exceed the sandbox input byte limit.")
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o444)
            for folder in sorted(staging.rglob("*"), reverse=True):
                if folder.is_dir(): folder.chmod(0o555)
            staging.chmod(0o555)
            ctx.save_state({"code_container": name, "code_request_sha256": request_hash,
                "code_phase": "planned", "code_staging_directory": directory})
            ctx.mark_dispatched({"container": name, "ownership_sha256": identity, "image": ctx.config["image"], "request_sha256": request_hash})
            try:
                argv = ["create", "--pull=never", "--name", name, "--label", _LABEL + "=" + identity,
                    "--network", "none", "--user", "65532:65532", "--read-only", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "--pids-limit", str(limits["pids"]),
                    "--memory", str(limits["memory_bytes"]), "--memory-swap", str(limits["memory_bytes"]),
                    "--cpus", f'{limits["cpu_millis"] / 1000:.3f}', "--log-driver", "none", "--shm-size", "1048576",
                    "--ulimit", "nofile=64:64", "--ulimit", "core=0:0",
                    "--tmpfs", f'/work:rw,nosuid,nodev,size={limits["work_bytes"]},uid=65532,gid=65532,mode=0700',
                    "--tmpfs", f'/tmp:rw,nosuid,nodev,size={limits["tmp_bytes"]},uid=65532,gid=65532,mode=0700',
                    "--mount", f"type=bind,src={staging},dst=/inputs,readonly,bind-propagation=rprivate",
                    "--workdir", "/work", "--env", "HOME=/tmp"]
                for proxy in _PROXIES:
                    argv += ["--env", proxy + "=", "--env", proxy.lower() + "="]
                argv += ["--entrypoint", "/usr/local/bin/python3", ctx.config["image"], "-I", "-B", "-u", "-c", _KEEPER]
                # Do not abandon an in-flight create request: its daemon-side
                # commit can outlive a killed CLI and evade immediate cleanup.
                status, body, _ = _run(argv, check=ctx.check, deadline=deadline, settle_on_cancel=True)
                if status or not re.fullmatch(rb"[0-9a-f]{64}\s*", body):
                    raise Unavailable("Docker could not create the resource-limited sandbox; inspect the managed daemon and pinned image.")
                cid = body.decode().strip()
                launched = True
                ctx.save_state({"code_phase": "created", "code_container_id": cid})
                status, _, _ = _run(["start", cid], check=ctx.check, deadline=deadline)
                if status:
                    raise Unavailable("Docker could not start the hardened sandbox; host isolation/resource controls must be available.")
                ctx.save_state({"code_phase": "executing"})
                rc, out, err = _run(["exec", "-i", cid, "/usr/local/bin/python3", "-I", "-B", "-u", "-c", _PROGRAM],
                    check=ctx.check, deadline=deadline, data=code, cap=limits["max_output_bytes"])
                ctx.save_state({"code_phase": "collecting"})
                cap = limits["max_artifact_bytes"] * 2 + limits["max_artifact_files"] * 2048 + 8192
                status, payload, _ = _run(["exec", cid, "/usr/local/bin/python3", "-I", "-B", "-c", _COLLECTOR, json.dumps(limits)],
                    check=ctx.check, deadline=deadline, cap=cap)
                if status:
                    raise ToolError("artifact_rejected", "Sandbox artifacts were unsafe, exceeded limits, or could not be collected safely.")
                artifacts, artifact_bytes = self._artifacts(payload, ctx, limits, identity)
                result = ToolResult({"status": "completed" if rc == 0 else "failed", "exit_code": rc,
                    "stdout": out.decode("utf-8", "replace"), "stderr": err.decode("utf-8", "replace")},
                    self._usage(start, input_bytes=input_bytes, stdout_bytes=len(out), stderr_bytes=len(err), artifact_bytes=artifact_bytes), artifacts)
                ctx.save_state({"code_result": asdict(result), "code_phase": "completed"})
                return result
            except ToolError as exc:
                # A real interrupted execution must never be replayed under this ID.
                if launched:
                    failure = ToolResult({"status": exc.code, "error": exc.message}, self._usage(start, input_bytes=input_bytes))
                    ctx.save_state({"code_result": asdict(failure), "code_phase": "failed"})
                raise
            finally:
                try:
                    self._cleanup(ctx)
                finally:
                    staging.chmod(0o700)
                    for folder in staging.rglob("*"):
                        if folder.is_dir(): folder.chmod(0o700)

    @staticmethod
    def _artifacts(payload, ctx, limits, identity):
        try:
            values = json.loads(payload)
            if not isinstance(values, list) or len(values) > limits["max_artifact_files"]:
                raise ValueError("count")
            validated, seen, total = [], set(), 0
            for item in values:
                if not isinstance(item, dict) or set(item) != {"path", "data"}:
                    raise ValueError("schema")
                path = _relative(item["path"])
                if path.casefold() in seen: raise ValueError("duplicate")
                seen.add(path.casefold())
                data = base64.b64decode(item["data"], validate=True)
                total += len(data)
                if total > limits["max_artifact_bytes"]: raise ValueError("bytes")
                validated.append((path, data))
        except (ValueError, TypeError, KeyError, ToolError) as exc:
            raise ToolError("artifact_rejected", "Artifact stream failed path, type, count or byte validation.") from exc
        artifacts = []
        for path, data in validated:
            ctx.check()
            destination = f"artifacts/code/{identity[:32]}/{path}"
            record = ctx.workspace.write_bytes(destination, data, overwrite=False)
            artifacts.append({**record, "path": destination, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "trust": "untrusted"})
        return artifacts, total

    def reconcile(self, arguments, ctx: ToolContext):
        ctx.check()
        state = ctx.load_state()
        if not state.get("code_container"):
            return Reconciliation("not_executed", detail="No sandbox launch intent was recorded.")
        cached = self._cached(state)
        found = self._inspect(ctx, check=ctx.check)
        self._cleanup(ctx)
        self._cleanup_staging(state)
        if cached:
            return Reconciliation("completed", cached, "Recorded result recovered; owned sandbox cleanup verified.")
        if found and found["status"] == "created":
            ctx.save_state({"code_container": None, "code_phase": "never_started"})
            return Reconciliation("not_executed", detail="Docker confirms this owned container never started; removed safely.")
        return Reconciliation("uncertain", detail="Interrupted sandbox execution has no durable result. Owned container was removed; operator reconciliation is required before any new action.")
