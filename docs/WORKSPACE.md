# Dedicated workspace and local deliverables

The Python adapters create actual private files, SVG documents, and (when the
optional Pillow package is installed) PNG images. These operations have been
verified locally. They do not publish files, deliver email, provide a public
download endpoint, or implement customer authentication.

Install from the repository with Python 3.11+:

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[test,graphics]'
.venv/bin/python -m pytest tests/test_workspace.py tests/test_files.py tests/test_graphics.py
```

The `graphics` extra is optional. SVG has no graphics dependency; a PNG request
without Pillow returns actionable `unavailable`, while SVG continues to work.

## Storage boundary

The trusted runtime constructs `DedicatedWorkspace(root, max_file_bytes=8000000,
max_total_bytes=64000000, max_files=1000, max_depth=16)`. `root` is a public
`pathlib.Path` for trusted integration code. All tool paths must be relative.
New directories use mode 0700 and files use 0600. An existing root and its
internal directory must belong to the gateway uid and grant no group/other
permissions. Choose a fresh dedicated subdirectory, never a repository, home
directory, configuration directory, credential directory, or accounting store.
The owner configuration must ensure these locations are disjoint.

The API provides `read_bytes`, `write_bytes`, `list_files` and `delete` with the
shared workspace protocol. Writes return path, SHA-256, byte length, and media
type. Writes create missing parent directories and reject existing files unless
`overwrite=True`. Delete removes ordinary files, never directories. Listing is
recursive and sorted, with at most 1000 returned entries. Its `limit` truncates
the result, so a list at its limit is not proof of a complete inventory.

Portable filenames start with an ASCII letter or digit and then use letters,
digits, spaces, dots, underscores, or hyphens, up to 128 characters per component
and 1024 characters per path. Hidden names, empty components, traversal,
backslashes, drive syntax, reserved DOS names, trailing dots/spaces, and control
characters are rejected. The private `.widemind` staging/lock directory is
inaccessible through the file API and omitted from listings.

Linux/POSIX directory-descriptor operations with `O_NOFOLLOW` prevent symlink
traversal, including swaps between path validation and opening. Reads reject
hard links, symlinks, devices, sockets, FIFOs, and different-device mount points.
Pinned directory descriptors prevent a concurrently substituted parent symlink
from redirecting a write. Staged writes use atomic linking for create and atomic
replacement for explicit overwrite; a concurrent create cannot be clobbered.
Cooperating processes serialize quota checks with a file lock. Lock acquisition
waits at most five seconds per thread/process lock stage and reports
`workspace_busy` if the workspace remains busy. A crash's staging file is
removed under the lock on restart or the next write.

The defaults bound file bytes, total committed bytes, count, depth, and scanned
directory entries (`max_files * max_depth`). A staged overwrite can temporarily
use one additional `max_file_bytes` beyond the committed byte quota. Enforce a
filesystem/container quota too when disk exhaustion must be prevented globally.

This library is an API boundary, **not an operating-system sandbox**. Code
running as the gateway uid can directly access `Path` or `os` and bypass it;
it can also rename or mutate entries during an operation. Quotas apply to users
of the API, not hostile direct filesystem writers. Run untrusted code/browser
processes in a separate container/uid with explicit mounts and resource limits.
Do not give those processes the gateway's uid, control paths, or credentials.
Same-device bind mounts and a privileged process are outside this API's threat
boundary. Container paths alone do not establish isolation from host resources.

## Files adapter

`FilesAdapter` registers tool `files`. Example arguments:

```json
{"operation":"create","path":"customer/report.txt","text":"Actual deliverable"}
```

Supported operations and fields:

| Operation | Fields |
| --- | --- |
| `create` | `path`, exactly one of `text` or `content_base64`, optional boolean `overwrite` |
| `read` | `path`, optional `encoding` (`utf-8` or `base64`) and `max_bytes` |
| `list` | optional directory `path` and `limit` (1–1000, default 100) |
| `delete` | `path` |
| `manifest` | output `path`, distinct source `paths`, optional boolean `overwrite` |

Unknown fields are rejected. UTF-8 is strict, and base64 must be canonical with
no whitespace. Owner configuration sets `enabled`, `max_input_bytes` (default
1,000,000; maximum 8,000,000), `max_read_bytes` (same), and
`max_manifest_files` (default 100; maximum 1000). Workspace quotas still apply.
Manifest sources are read one at a time and hashed, with cancellation checked
between files. A manifest is a snapshot of the bytes read; direct concurrent
filesystem mutations can make it stale.

Every output is private, untrusted task data. Artifact metadata and manifests
take `job_id` only from `ctx.job_id`; tool arguments cannot substitute another
customer. This association is not a customer authentication mechanism. Any
future delivery endpoint must verify job ownership independently, and verify
current artifact bytes against the committed manifest before delivery.
Text such as "change permissions" remains file content and grants no authority.

## Graphics adapter

`GraphicsAdapter` registers tool `graphics`. It accepts `path`, optional
`format` (`svg` or `png`, otherwise inferred from extension), integer `width`
and `height`, `background`, a list of `elements`, and boolean `overwrite`.

```json
{"path":"customer/banner.svg","width":640,"height":240,"background":"#ffffff","elements":[{"type":"rect","x":20,"y":20,"width":600,"height":200,"fill":"#2244aa"},{"type":"text","x":320,"y":135,"text":"Customer banner & logo","font_size":32,"anchor":"middle","fill":"#ffffff"}]}
```

| Primitive | Required geometry/content | Optional text settings |
| --- | --- | --- |
| `rect` | `x`, `y`, `width`, `height` | — |
| `circle` | `cx`, `cy`, `radius` | — |
| `line` | `x1`, `y1`, `x2`, `y2` | — |
| `text` | `x`, `y`, `text` | `font_size` (default 24), `anchor` (`start`, `middle`, `end`) |

All primitives accept `fill`, `stroke`, and `stroke_width`. Colors are `#RGB`,
`#RRGGBB`, `#RRGGBBAA`, or `none`. Shape fill defaults to black and stroke to
none; line stroke defaults to black. Numeric values must be finite. Font size
and stroke width are bounded at 256. Coordinates and sizes are bounded by four
times the configured maximum canvas dimension. SVG text is XML-escaped and
rendered using a generic sans-serif font; PNG uses Pillow's built-in font.
Multiline and font appearance can differ between these renderers. No arbitrary
SVG, scripts, event handlers, CSS, browser rendering, URL fetches, images, font
paths, or external resources are accepted. A caller-provided URL in text is
escaped text, never a fetched resource.

Owner graphics configuration:

| Setting | Default | Hard maximum |
| --- | --- | --- |
| `max_width`, `max_height` | 2048 each | 4096 each |
| `max_pixels` | 4,000,000 | 16,777,216 |
| `max_elements` | 500 | 2000 |
| `max_text_characters` | 20,000 total | 100,000 |
| `max_raster_text_pixels` | 16,000,000 | 64,000,000 |
| `max_output_bytes` | 8,000,000 | 8,000,000 |

`enabled=false` disables the adapter. PNG text masks are bounded conservatively
before rendering using total characters, font size and stroke width; clipping a
long line outside the canvas does not evade that check. Cancellation/deadline
checks run before execution, between primitives and text lines, and before file
publication. Pillow's bounded image encoding is synchronous. SVG output is an
actual SVG artifact; `raster_rendered=false` makes no claim of a raster preview.
PNG output has actual PNG bytes and reports the Pillow renderer.

## Cost and interrupted actions

These local tools quote zero micro-USD and make no paid provider calls. Every
quote and usage record explicitly discloses owner-provided local compute and
storage, excluded from this tool charge with allocated cost unknown (`null`).
Zero tool charge does not establish zero hosting/storage expense or autonomous
profit. Account for any allocation separately through the trusted economy.

File mutations and graphics creation persist an argument hash and job binding
in trusted action state, followed by a completed private result receipt. Recovery
can return that receipt without repeating the mutation. A crash after file
replacement but before receipt persistence remains `uncertain`; the existence
of a path alone does not prove completion. Inspect actual bytes and the private
action record before resolving it. Tool results and file contents cannot write
configuration, credentials, the ledger, or action authorization.

The local tests verify bytes/hashes and PNG pixels, escaped SVG injection,
strict input bounds, quotas across workspace instances, symlink swaps before
and after descriptor opening, atomic create races, interrupted write cleanup,
private job metadata, cancellation, and completed-receipt recovery. They do not
claim verification of a container sandbox or live customer delivery.
