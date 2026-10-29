# Docker code execution

`CodeAdapter` implements the version-one `code.execute` contract. It runs real
Python code in an owner-provisioned Linux Docker image. There is no host-code
fallback and no image pull or package installation in the adapter.

Configuration belongs outside the agent workspace:

```json
{
  "image": "python@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0",
  "owner_compute_subsidy": true,
  "wall_seconds": 20,
  "memory_bytes": 134217728,
  "cpu_millis": 500,
  "pids": 32,
  "work_bytes": 16777216,
  "tmp_bytes": 8388608,
  "max_output_bytes": 65536,
  "max_input_bytes": 8388608,
  "max_artifact_bytes": 8388608,
  "max_artifact_files": 16
}
```

That official Python digest was explicitly pulled for a controlled development
test; it is not an automatically downloaded runtime dependency. Operators must
approve and provision their own reviewed local image, including its license,
contents and update policy. An immutable image ID is also accepted. The image
must contain `/usr/local/bin/python3` and its standard library. Availability
reports missing configuration, CLI, image or daemon access independently.
Images declaring Docker volumes are rejected: those mounts would bypass tmpfs
storage bounds. Baked environment is restricted to validated standard binary
PATH directories, supported LANG values, public GPG fingerprints, Python
version/SHA metadata, and boolean Python buffering/bytecode settings. Unknown
variables, duplicate keys and surprising values are rejected before launch.
The remaining image filesystem still requires owner review for secrets.

Input arguments are `{"code": "...Python source...", "inputs": ["relative/file"]}`.
No task argument can choose an image, executable, Docker option, environment,
host path or resource allowance. Code can read selected files under `/inputs`
and write deliverables under `/work/output`. Returned workspace artifacts use
an action-derived prefix, carry SHA-256 and size, and remain untrusted private
task data. Nonzero program exits are returned explicitly rather than called
successful work.

The container has network `none`, UID/GID 65532, a read-only root filesystem,
all Linux capabilities dropped, `no-new-privileges`, bounded PID/memory/CPU
resources, disabled swap and Docker logs, a bounded `/dev/shm`, and bounded
`/work` and `/tmp` tmpfs mounts. It receives no Docker socket, ledger,
credentials, whole workspace or unrelated host mount. Only explicitly selected
input bytes are staged in an owner-private temporary directory and bound
read-only. Docker proxy defaults are explicitly cleared inside the container;
host selectors cannot redirect the CLI away from the managed local socket.

The generated source travels over bounded stdin. Both output streams share a
byte cap. Cancellation, authorization and deadlines are checked while the CLI
runs; process groups and owned containers are cleaned up. Cancellation during
container creation is settled for up to five extra seconds before cleanup,
because abandoning its CLI can otherwise leave a daemon-side create commit.
An unresponsive create remains uncertain and requires reconciliation.

Artifact collection runs a separate isolated Python interpreter in the live
container. It stops remaining sandbox descendants and reads regular files using
directory descriptors and `O_NOFOLLOW`; links, special files, ambiguous paths,
duplicate names and over-limit byte/count streams are rejected. Host-side
validation completes before any workspace writes. The Docker daemon in this
environment cannot archive tmpfs content with `docker cp`; this collector was
verified against real tmpfs output instead. The owned container and its anonymous volumes are removed afterwards;
read-only input binds are not deleted by Docker volume cleanup.

Before create, the adapter persists an action-derived container name, ownership
label, request hash and launch intent. Reconciliation checks those labels before
removing a container; it never removes an unrelated name collision. A durable
result is recovered without rerunning code. Docker's `created` state is
affirmative evidence that code did not start. An absent container after an
interrupted action is insufficient proof: the outcome remains uncertain and is
not replayed automatically. Private input staging can also be reclaimed from
the durable state during reconciliation.

Usage records measured wall milliseconds and input/output/artifact byte counts.
No actual CPU consumption is claimed. Operating-wallet cost is zero only under
the explicit owner-compute subsidy setting; host compute, image storage and
setup/download costs are excluded subsidies, not autonomous earnings or free
infrastructure. This adapter does not implement paid remote compute tariffs.

Run focused synthetic tests without Docker:

```sh
PYTHONPATH=src:tests python -m pytest -q tests/test_code_adapter.py
```

Run controlled real tests only after explicitly provisioning a pinned image:

```sh
WIDEMIND_TEST_CODE_IMAGE='python@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0' PYTHONPATH=src:tests python -m pytest -q tests/test_code_docker.py
```

Real tests exercise private read-only inputs, UID/rootfs/network isolation,
artifacts, link rejection, output/time/cancellation limits, memory, tmpfs and
PID limits. Without the explicit image variable they skip with a reason.
Docker shares the host kernel and is not a proof against kernel exploits;
production hostile-code workloads require a separately hardened dedicated
worker/VM and an audited Docker daemon/image. POSIX worker hosts are supported;
Windows hosts, customer production workloads and paid compute are unverified.
