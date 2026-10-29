"""Customer-specific arithmetic calculators, verified in the existing code sandbox.

Models produce a bounded expression tree, never a privileged program. The actual
compiled JavaScript is exercised by Node inside CodeAdapter's networkless Docker
sandbox. The web host must display previews in an opaque sandboxed frame.
"""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import html
import json
import math
import re
import threading
import time
from typing import Any, Mapping

from .code import CodeAdapter, _PROXIES, _run, _limits as _code_limits
from .contracts import Quote, Reconciliation, ToolContext, ToolError, ToolResult, ToolSpec, Unavailable, Uncertain, Usage
from .files import FilesAdapter, _arguments_hash, _local_intent, _local_receipt, _reconcile_local, _SUBSIDY


_ID = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
_FORBIDDEN = {"constructor", "prototype", "__proto__", "then", "tojson"}
_OPERATIONS = {"add", "subtract", "multiply", "divide", "min", "max", "pow"}
_PROBES: dict[str, tuple[float, bool]] = {}
_PROBE_LOCKS: dict[str, threading.Lock] = {}
_PROBE_CACHE_LOCK = threading.Lock()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _text(value: Any, name: str, maximum: int = 2000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode()) > maximum or any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ToolError("invalid_input", f"{name} must be bounded, nonempty text.")
    return value.strip()


def _identifier(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value) or value.lower() in _FORBIDDEN:
        raise ToolError("invalid_input", "Calculator identifiers must be safe lowercase names.")
    return value


def _number(value: Any, name: str, maximum: float = 10**15) -> float | int:
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > maximum:
        raise ToolError("invalid_input", f"{name} must be a finite bounded number.")
    return value


def validate_scope(scope: Any) -> dict:
    """Validate the frozen acceptance contract independently of a model design."""
    required = {"title", "description", "category", "inputs", "outputs", "acceptance_cases", "requirements", "max_revisions"}
    if not isinstance(scope, dict) or set(scope) - required or not required <= set(scope) or scope.get("category") != "calculator":
        raise ToolError("invalid_input", "Supply the documented calculator scope; only arithmetic calculators are implemented.")
    _text(scope["title"], "title", 200)
    _text(scope["description"], "description", 3000)
    if type(scope["max_revisions"]) is not int or not 0 <= scope["max_revisions"] <= 2:
        raise ToolError("invalid_input", "max_revisions must be an integer from zero to two.")
    requirements = scope["requirements"]
    if not isinstance(requirements, list) or not 1 <= len(requirements) <= 12:
        raise ToolError("invalid_input", "Supply one to twelve observable calculator requirements.")
    for requirement in requirements:
        _text(requirement, "requirement", 500)
    inputs, outputs = scope["inputs"], scope["outputs"]
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 8 or not isinstance(outputs, list) or not 1 <= len(outputs) <= 8:
        raise ToolError("invalid_input", "Calculators support one to eight inputs and outputs.")
    input_ids, output_ids = [], []
    for item in inputs:
        if not isinstance(item, dict) or set(item) != {"id", "label", "min", "max", "default"}:
            raise ToolError("invalid_input", "Each input requires id, label, min, max and default.")
        input_ids.append(_identifier(item["id"]))
        _text(item["label"], "input label", 120)
        minimum = _number(item["min"], "minimum", 10**9)
        maximum = _number(item["max"], "maximum", 10**9)
        default = _number(item["default"], "default", 10**9)
        if not minimum < maximum or not minimum <= default <= maximum:
            raise ToolError("invalid_input", "Input defaults must fit a nonempty bounded range.")
    for item in outputs:
        if not isinstance(item, dict) or set(item) != {"id", "label"}:
            raise ToolError("invalid_input", "Each output requires id and label.")
        output_ids.append(_identifier(item["id"]))
        _text(item["label"], "output label", 120)
    if len(set(input_ids)) != len(input_ids) or len(set(output_ids)) != len(output_ids):
        raise ToolError("invalid_input", "Input and output identifiers must be distinct within each collection.")
    cases = scope["acceptance_cases"]
    if not isinstance(cases, list) or not 2 <= len(cases) <= 16:
        raise ToolError("invalid_input", "Freeze two to sixteen independently supplied acceptance cases before generation.")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or set(case) != {"name", "inputs", "expected", "tolerance"}:
            raise ToolError("invalid_input", "Each acceptance case requires name, inputs, expected and tolerance.")
        _text(case["name"], "case name", 200)
        if not isinstance(case["inputs"], dict) or set(case["inputs"]) != set(input_ids) or not isinstance(case["expected"], dict) or set(case["expected"]) != set(output_ids):
            raise ToolError("invalid_input", "Acceptance cases must specify every agreed input and output.")
        for item in inputs:
            value = _number(case["inputs"][item["id"]], "acceptance input", 10**9)
            if not item["min"] <= value <= item["max"]:
                raise ToolError("invalid_input", "Acceptance inputs must fit their agreed range.")
        for value in case["expected"].values():
            _number(value, "expected output")
        tolerance = _number(case["tolerance"], "tolerance", 1)
        if tolerance < 0:
            raise ToolError("invalid_input", "Acceptance tolerance cannot be negative.")
        seen.add(_json(case["inputs"]))
    if len(seen) < 2:
        raise ToolError("invalid_input", "Acceptance cases must exercise at least two distinct input combinations.")
    if len(_json(scope).encode()) > 24000:
        raise ToolError("input_limit", "Calculator scope exceeds its byte limit.")
    return scope


def _expression(node: Any, identifiers: set[str], depth: int = 0, count: list[int] | None = None) -> str:
    count = [0] if count is None else count
    count[0] += 1
    if depth > 8 or count[0] > 120:
        raise ToolError("input_limit", "Expression trees are bounded to depth eight and 120 nodes per output.")
    if type(node) in (int, float):
        return _json(_number(node, "expression constant", 10**9))
    if not isinstance(node, dict):
        raise ToolError("invalid_input", "Expressions must use documented arithmetic nodes, never executable source.")
    if set(node) == {"input"}:
        if not isinstance(node["input"], str) or node["input"] not in identifiers:
            raise ToolError("invalid_input", "Expression references an unknown input.")
        return "values[" + _json(node["input"]) + "]"
    if set(node) != {"op", "args"} or not isinstance(node["op"], str) or node["op"] not in _OPERATIONS or not isinstance(node["args"], list):
        raise ToolError("invalid_input", "Unsupported arithmetic expression node.")
    args = node["args"]
    if not 2 <= len(args) <= 8 or node["op"] in {"subtract", "divide", "pow"} and len(args) != 2:
        raise ToolError("invalid_input", "Arithmetic operations require their documented bounded argument count.")
    if node["op"] == "pow" and (type(args[1]) not in (int, float) or abs(_number(args[1], "exponent", 8)) > 8):
        raise ToolError("invalid_input", "Power requires a literal exponent between minus eight and eight.")
    rendered = [_expression(arg, identifiers, depth + 1, count) for arg in args]
    if node["op"] in {"min", "max", "pow"}:
        return "Math." + node["op"] + "(" + ",".join(rendered) + ")"
    symbol = {"add": "+", "subtract": "-", "multiply": "*", "divide": "/"}[node["op"]]
    return "(" + symbol.join("(" + arg + ")" for arg in rendered) + ")"


def validate_design(scope: dict, design: Any) -> dict:
    if not isinstance(design, dict) or set(design) - {"outputs", "accent", "heading", "description"} or "outputs" not in design:
        raise ToolError("invalid_input", "Design must contain bounded output expressions and optional visual text/color.")
    outputs = design["outputs"]
    if not isinstance(outputs, list) or len(outputs) != len(scope["outputs"]):
        raise ToolError("invalid_input", "Design must implement every agreed output exactly once.")
    if [row.get("id") if isinstance(row, dict) else None for row in outputs] != [row["id"] for row in scope["outputs"]]:
        raise ToolError("invalid_input", "Design output identities must match the frozen scope and order.")
    for row in outputs:
        if set(row) != {"id", "expression"}:
            raise ToolError("invalid_input", "Output designs accept only id and expression.")
        _expression(row["expression"], {item["id"] for item in scope["inputs"]})
    accent = design.get("accent", "#b7e5b4")
    if not isinstance(accent, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        raise ToolError("invalid_input", "Calculator accent must be a six-digit hex color.")
    for name in ("heading", "description"):
        if name in design:
            _text(design[name], name, 200 if name == "heading" else 3000)
    return design


def _compile_js(scope: dict, design: dict) -> str:
    definition = {"inputs": scope["inputs"], "outputs": scope["outputs"]}
    expressions = ",\n".join(_json(row["id"]) + ": " + _expression(row["expression"], {i["id"] for i in scope["inputs"]}) for row in design["outputs"])
    return """/* Customer-specific calculator source. No network, eval or dependencies. */
(function(){'use strict';
const definition=DEFINITION;
function calculate(values){
 if(!values||typeof values!=='object'||Array.isArray(values)||Object.keys(values).length!==definition.inputs.length)throw new Error('Provide every required input.');
 for(const field of definition.inputs){const value=values[field.id];if(!Object.hasOwn(values,field.id)||typeof value!=='number'||!Number.isFinite(value)||value<field.min||value>field.max)throw new Error(field.label+' must be a finite number from '+field.min+' to '+field.max+'.');}
 const result={EXPRESSIONS};
 for(const value of Object.values(result)){if(!Number.isFinite(value)||Math.abs(value)>1e15)throw new Error('Calculation is undefined or outside the supported numeric range.');}
 return result;
}
const api=Object.freeze({calculate,definition});
if(typeof module!=='undefined'&&module.exports)module.exports=api;
if(typeof globalThis!=='undefined')globalThis.WideMindCalculator=api;
if(typeof document!=='undefined'){
 const form=document.getElementById('calculator-form'),status=document.getElementById('calculator-status');
 function render(){try{const values={};for(const field of definition.inputs){const input=form.elements.namedItem(field.id);if(input.value.trim()==='')throw new Error(field.label+' is required.');values[field.id]=Number(input.value);}const valuesOut=calculate(values);for(const field of definition.outputs)document.getElementById('output-'+field.id).textContent=String(Number(valuesOut[field.id].toPrecision(12)));status.textContent='Calculated successfully.';status.dataset.state='success';}catch(error){for(const field of definition.outputs)document.getElementById('output-'+field.id).textContent='—';status.textContent=error.message;status.dataset.state='error';}}
 form.addEventListener('submit',event=>{event.preventDefault();render();});form.addEventListener('input',render);form.addEventListener('reset',()=>setTimeout(render,0));render();
}
})();
""".replace("EXPRESSIONS", expressions).replace("DEFINITION", _json(definition).replace("<", "\\u003c"))


def compile_files(scope: dict, design: dict) -> dict[str, tuple[str, str, str]]:
    """Compile validated model data; no model-supplied source is executed here."""
    validate_scope(scope)
    validate_design(scope, design)
    script = _compile_js(scope, design)
    accent = design.get("accent", "#b7e5b4")
    css = """:root{color-scheme:light;font-family:system-ui,sans-serif;color:#17251f;background:#eef2ee}*{box-sizing:border-box}body{margin:0;padding:clamp(16px,5vw,48px)}main{max-width:840px;margin:auto;background:white;border:1px solid #d8e1d9;border-radius:20px;padding:clamp(20px,5vw,44px)}h1{font-size:clamp(28px,5vw,42px);line-height:1.1;letter-spacing:-.04em;margin:8px 0 16px}p{line-height:1.6;color:#4c5f52}form{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:18px;margin-top:24px}label{display:grid;gap:8px;font-size:14px}input{width:100%;font:inherit;padding:13px;border:1px solid #809889;border-radius:8px;background:#fff;color:#17251f}.controls{grid-column:1/-1;display:flex;gap:12px;flex-wrap:wrap}button{font:600 14px system-ui;min-height:44px;padding:12px 20px;border-radius:8px;border:1px solid #647a6b;background:ACCENT;color:#10251a;cursor:pointer}button[type=reset]{background:#fff}.outputs{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));gap:16px;margin-top:28px}.output{padding:20px;background:#f0f5f0;border-radius:12px;min-width:0}.output span{display:block;color:#47604e;font-size:13px}.output output{display:block;font-size:30px;line-height:1.4;overflow-wrap:anywhere;margin-top:8px}#calculator-status{font-size:13px;min-height:40px}#calculator-status[data-state=error]{color:#992b20}:focus-visible{outline:3px solid #293d30;outline-offset:3px}footer{margin-top:24px;border-top:1px solid #d8e1d9;padding-top:15px;font-size:12px;color:#65766a}
""".replace("ACCENT", accent)
    channels = [int(accent[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    luminance = sum(weight * (channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4)
                    for weight, channel in zip((.2126, .7152, .0722), channels))
    if luminance < .25:
        css += "button[type=submit]{color:#fff}button[type=reset]{color:#10251a}\n"
    title = html.escape(design.get("heading", scope["title"]))
    description = html.escape(design.get("description", scope["description"]))
    fields = "".join(f'<label for="input-{item["id"]}">{html.escape(item["label"])}<input id="input-{item["id"]}" name="{item["id"]}" type="number" step="any" min="{item["min"]}" max="{item["max"]}" value="{item["default"]}" required></label>' for item in scope["inputs"])
    outputs = "".join(f'<div class="output"><span>{html.escape(item["label"])}</span><output id="output-{item["id"]}" aria-live="polite">—</output></div>' for item in scope["outputs"])
    document = f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; connect-src \'none\'; img-src \'none\'; form-action \'none\'; base-uri \'none\'"><title>{html.escape(scope["title"])}</title><style>{css}</style></head><body><main><p>YOUR CUSTOM CALCULATOR</p><h1>{title}</h1><p>{description}</p><form id="calculator-form" novalidate>{fields}<div class="controls"><button type="submit">Calculate</button><button type="reset">Reset</button></div></form><div class="outputs">{outputs}</div><p id="calculator-status" role="status"></p><footer>Runs locally in your browser. No information is uploaded. Results use standard JavaScript numeric precision.</footer><noscript>This calculator needs JavaScript enabled to calculate results.</noscript></main><script>{script}</script></body></html>'
    readme = "# " + scope["title"] + "\n\n" + scope["description"] + "\n\nOpen index.html in a current browser. Enter values in the agreed ranges; results update as you type. Calculate submits the same computation and Reset restores defaults. No installation, external library, network or account is required.\n\nFiles: index.html is the self-contained runnable application; calculator.js and calculator.css are editable source; scope.json records the accepted inputs, outputs and independent acceptance cases.\n\n## Embed safely\n\nServe index.html from a separate static origin and use an iframe with sandbox=\"allow-scripts\". Do not add allow-same-origin, navigation, form, popup or download permissions. Never serve generated pages in an operator-authenticated application origin. A parent may alternatively use an opaque sandboxed srcdoc frame.\n\n## Numerical limits\n\nThis category supports arithmetic calculators over finite numeric inputs, not arbitrary applications, networking, file access, accounts or a financial ledger. Division by zero and non-finite/very large outputs display an explicit error. Outputs use IEEE-754 numbers; it is not exact monetary accounting.\n\n## Agreed requirements\n\n" + "\n".join("- " + item for item in scope["requirements"]) + "\n\n## Acceptance cases\n\n" + "\n".join("- " + case["name"] + ": " + _json({"inputs": case["inputs"], "expected": case["expected"], "tolerance": case["tolerance"]}) for case in scope["acceptance_cases"]) + "\n"
    return {"index.html": ("text/html", document, "original"), "calculator.js": ("text/javascript", script, "source"), "calculator.css": ("text/css", css, "source"), "README.md": ("text/markdown", readme, "instructions"), "scope.json": ("application/json", _json(scope) + "\n", "source")}


_HARNESS = r"""
'use strict';
const fs=require('fs'),vm=require('vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
const sandbox={module:{exports:{}},globalThis:{}};
vm.runInNewContext(payload.source,sandbox,{timeout:500});
const calculate=sandbox.module.exports.calculate;
if(typeof calculate!=='function')throw new Error('Missing calculator implementation');
const fields=payload.scope.inputs,outputs=payload.scope.outputs;
const valid=result=>{if(!result||Object.keys(result).length!==outputs.length||outputs.some(field=>!Object.hasOwn(result,field.id)||typeof result[field.id]!=='number'||!Number.isFinite(result[field.id])))throw new Error('Invalid output contract');};
let cases=0,rejections=0,boundaries=0;
for(const test of payload.scope.acceptance_cases){const actual=calculate(test.inputs);valid(actual);for(const field of outputs){if(Math.abs(actual[field.id]-test.expected[field.id])>test.tolerance)throw new Error('Acceptance failed: '+test.name+' / '+field.id);}cases++;}
const defaults=Object.fromEntries(fields.map(field=>[field.id,field.default]));
valid(calculate(defaults));
const reject=value=>{let rejected=false;try{calculate(value);}catch(error){rejected=true;}if(!rejected)throw new Error('Invalid input was accepted');rejections++;};
reject(null);reject([]);reject({});reject({...defaults,unexpected:1});
for(const field of fields){for(const bad of [null,true,'2',NaN,Infinity,-Infinity,field.min-Math.max(1,Math.abs(field.min)*.001),field.max+Math.max(1,Math.abs(field.max)*.001)])reject({...defaults,[field.id]:bad});const missing={...defaults};delete missing[field.id];reject(missing);
for(const edge of [field.min,field.max]){try{valid(calculate({...defaults,[field.id]:edge}));}catch(error){if(!String(error.message).includes('undefined or outside the supported numeric range'))throw error;}boundaries++;}}
console.log(JSON.stringify({version:1,passed:true,acceptance_cases:cases,invalid_inputs_rejected:rejections,boundaries_checked:boundaries,source_sha256:require('crypto').createHash('sha256').update(payload.source).digest('hex')}));
"""


def verifier_arguments(scope: dict, script: str) -> dict:
    payload = base64.b64encode(_json({"scope": scope, "source": script}).encode()).decode()
    harness = base64.b64encode(_HARNESS.encode()).decode()
    code = "import base64,subprocess,sys\n" + "payload=base64.b64decode(" + repr(payload) + ")\n" + "runner=base64.b64decode(" + repr(harness) + ").decode()\n" + "result=subprocess.run(['/usr/bin/node','-e',runner],input=payload,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=8,env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','LANG':'C.UTF-8'})\n" + "sys.stdout.buffer.write(result.stdout[:16000])\nsys.stderr.buffer.write(result.stderr[:2000])\nsys.exit(result.returncode)\n"
    return {"code": code, "inputs": []}


class _StateNamespace:
    def __init__(self, parent: ToolContext, name: str):
        self.parent, self.name = parent, name

    def load(self, _id):
        return dict(self.parent.load_state().get(self.name, {}))

    def update(self, _id, patch):
        state = self.load(_id)
        state.update(patch)
        self.parent.save_state({self.name: state})

    def dispatched(self, _id, intent):
        self.update(_id, {"external_intent": dict(intent)})
        self.parent.mark_dispatched({"operation": "software_verification", "child": dict(intent)})


def _child(ctx: ToolContext, name: str, config: dict) -> ToolContext:
    return replace(ctx, action_id=ctx.action_id + ":" + name, config=config, state=_StateNamespace(ctx, name))


def _probe_node(config: Mapping[str, Any]) -> bool:
    """Free owner capability check; no customer source, workspace or credentials."""
    # A readiness probe must not impose a stricter resource envelope than the
    # actual authorized code runner. Docker startup can itself exceed five
    # seconds under load, even though the installed Node runtime is healthy.
    limits = _code_limits(config)
    image = config["image"]
    key = _json({"image": image, "limits": limits})

    def cached_result():
        with _PROBE_CACHE_LOCK:
            cached = _PROBES.get(key)
        if cached and time.monotonic() - cached[0] < (300 if cached[1] else 2):
            return cached[1]
        return None

    cached = cached_result()
    if cached is not None:
        return cached
    with _PROBE_CACHE_LOCK:
        lock = _PROBE_LOCKS.setdefault(key, threading.Lock())
    # Capability requests arrive concurrently from web/worker/business clients.
    # Only one probe per effective owner configuration may launch a container;
    # waiters reuse its result rather than starting duplicate Docker work.
    with lock:
        cached = cached_result()
        if cached is not None:
            return cached
        args = ["run", "--rm", "--pull=never", "--network=none", "--user=65532:65532", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=" + str(limits["pids"]), "--memory=" + str(limits["memory_bytes"]),
            "--memory-swap=" + str(limits["memory_bytes"]), "--cpus=" + f'{limits["cpu_millis"] / 1000:.3f}',
            "--log-driver=none", "--entrypoint=/usr/bin/node"]
        for proxy in _PROXIES:
            args.extend(["--env", proxy + "=", "--env", proxy.lower() + "="])
        args.extend([image, "--version"])
        try:
            status, output, _ = _run(args, deadline=time.monotonic() + min(limits["wall_seconds"], 15), cap=1024)
            result = status == 0 and re.fullmatch(rb"v\d+\.\d+\.\d+\s*", output) is not None
        except ToolError:
            result = False
        with _PROBE_CACHE_LOCK:
            _PROBES[key] = time.monotonic(), result
        return result


class CreativeSoftwareAdapter:
    spec = ToolSpec("creative.software", "Compile and sandbox-verify a customer-specific interactive arithmetic calculator.", "local")

    @staticmethod
    def _settings(config):
        if config.get("enabled") is not True or config.get("verification_runtime") != "node" or not isinstance(config.get("code_settings"), dict):
            raise Unavailable("Enable creative.software with verification_runtime=node and the owner-approved code.execute settings.")
        settings = dict(config["code_settings"])
        if settings.get("enabled") is not True:
            raise Unavailable("The owner must separately enable the existing code.execute capability.")
        settings["max_output_bytes"] = min(settings.get("max_output_bytes", 65536), 16000)
        return settings

    def availability(self, config, secrets):
        try:
            settings = self._settings(config)
            health = CodeAdapter().availability(settings, secrets)
            if health.get("available") is not True:
                raise Unavailable(health.get("message", "Code sandbox unavailable."))
            if not _probe_node(settings):
                raise Unavailable("The pinned code sandbox must contain a working /usr/bin/node runtime.")
            return {"available": True, "category": "interactive-arithmetic-calculator", "verification": "generated-javascript-in-networkless-docker"}
        except ToolError as exc:
            return {"available": False, "reason": exc.message}

    def _prepare(self, arguments, config):
        if not isinstance(arguments, dict) or set(arguments) != {"scope", "design"}:
            raise ToolError("invalid_input", "Supply the frozen calculator scope and a bounded model design.")
        files = compile_files(arguments["scope"], arguments["design"])
        total = sum(len(value[1].encode()) for value in files.values())
        if total > min(config.get("max_artifact_bytes", 400000), 400000):
            raise ToolError("output_limit", "Compiled calculator files exceed their bounded artifact total.")
        maximum = config.get("max_output_bytes", 1000000)
        if type(maximum) is not int or not 1 <= maximum <= 8000000:
            raise Unavailable("Configure a positive bounded max_output_bytes for calculator receipts.")
        estimate = {"files": [{"filename": name, "content": row[1], "sha256": "0" * 64, "media_type": row[0], "size": len(row[1].encode()), "encoding": "utf-8", "role": row[2]} for name, row in files.items()], "content": files["index.html"][1], "design": arguments["design"]}
        if len(_json(estimate).encode()) + 8192 > maximum:
            raise ToolError("output_limit", "Calculator receipt would exceed the authorized output cap before execution.")
        settings = self._settings(config)
        verification = verifier_arguments(arguments["scope"], files["calculator.js"][1])
        CodeAdapter().quote(verification, settings)
        return files, settings, verification

    def quote(self, arguments, config):
        files, settings, _ = self._prepare(arguments, config)
        return Quote(provider="docker-owner-compute", limits={"category": "calculator", "files": len(files), "acceptance_cases": len(arguments["scope"]["acceptance_cases"]), "wall_seconds": settings.get("wall_seconds", 20), "cost_disclosure": dict(_SUBSIDY), "generation": "separately-accounted-ai-action"})

    @staticmethod
    def _root(ctx):
        return "deliveries/software/" + hashlib.sha256(ctx.action_id.encode()).hexdigest()

    def _files(self, files, ctx, *, write, allow_missing=False):
        records, artifacts = [], []
        for index, (filename, (media_type, content, role)) in enumerate(files.items()):
            ctx.check()
            path = self._root(ctx) + "/" + filename
            encoded = content.encode()
            digest = hashlib.sha256(encoded).hexdigest()
            try:
                actual = ctx.workspace.read_bytes(path, max_bytes=len(encoded) + 1)
                if actual != encoded:
                    raise Uncertain("A completed calculator stage has conflicting artifact bytes; preserve and inspect it.")
                artifact = {"path": path, "sha256": digest, "size_bytes": len(encoded), "media_type": media_type, "job_id": ctx.job_id, "visibility": "private"}
            except ToolError as exc:
                if exc.code != "not_found":
                    raise
                if not write:
                    if allow_missing:
                        continue
                    raise Uncertain("Some staged calculator source files are missing; no execution or file mutation was repeated.") from exc
                child = _child(ctx, "software_file_" + str(index), {"enabled": True, "max_input_bytes": 400000})
                result = FilesAdapter().execute({"operation": "create", "path": path, "text": content}, child)
                artifact = result.artifacts[0]
            artifacts.append(artifact)
            records.append({"filename": filename, "media_type": media_type, "encoding": "utf-8", "content": content, "sha256": digest, "size": len(encoded), "role": role})
        return records, artifacts

    def _finish(self, arguments, ctx, result, records, artifacts):
        checks = {"source_created": True, "isolated_execution": True}
        errors = []
        try:
            report = json.loads(result.data.get("stdout", ""))
            if not isinstance(report, dict) or result.data.get("exit_code") != 0 or report.get("passed") is not True or report.get("version") != 1:
                raise ValueError("Sandbox acceptance checks failed.")
            source = next(row["content"] for row in records if row["filename"] == "calculator.js")
            if report.get("source_sha256") != hashlib.sha256(source.encode()).hexdigest():
                raise ValueError("Sandbox receipt does not identify the generated source.")
            for key in ("acceptance_cases", "invalid_inputs_rejected", "boundaries_checked"):
                if type(report.get(key)) is not int or report[key] <= 0:
                    raise ValueError("Sandbox verification report is incomplete.")
            if report["acceptance_cases"] != len(arguments["scope"]["acceptance_cases"]) or report["boundaries_checked"] != len(arguments["scope"]["inputs"]) * 2:
                raise ValueError("Sandbox checks do not cover the frozen contract.")
            checks.update(acceptance_cases=report["acceptance_cases"], invalid_inputs_rejected=report["invalid_inputs_rejected"], boundaries_checked=report["boundaries_checked"], generated_javascript_executed=True)
        except (ValueError, TypeError, KeyError, StopIteration):
            errors.append("The generated JavaScript did not pass all frozen acceptance, input and boundary checks in the isolated runtime.")
        if errors:
            data = {"verified": False, "checks": {**checks, "acceptance_passed": False}, "errors": errors}
        else:
            document = next(row["content"] for row in records if row["filename"] == "index.html")
            data = {"verified": True, "filename": "index.html", "media_type": "text/html", "content": document, "checks": checks, "files": records, "preview": {"filename": "index.html", "media_type": "text/html", "sandbox": "allow-scripts"}, "design": arguments["design"]}
        value = ToolResult(data, Usage(0, units=dict(result.usage.units), details={**dict(result.usage.details), "generation": "separately budget-authorized model action", **dict(_SUBSIDY)}), artifacts=artifacts)
        _local_receipt(arguments, ctx, value)
        return value

    def execute(self, arguments, ctx: ToolContext):
        ctx.check()
        recovered = _reconcile_local(arguments, ctx)
        if recovered.outcome == "completed":
            return recovered.result
        files, settings, verification = self._prepare(arguments, ctx.config)
        expected = {"arguments_sha256": _arguments_hash(arguments), "files": {name: hashlib.sha256(row[1].encode()).hexdigest() for name, row in files.items()}}
        previous = ctx.load_state().get("software_intent")
        if previous and previous != expected:
            raise Uncertain("Calculator action input changed after a durable stage; use a separately approved revision.")
        _local_intent(arguments, ctx)
        ctx.save_state({"software_intent": expected})
        records, artifacts = self._files(files, ctx, write=True)
        child = _child(ctx, "software_code", settings)
        result = CodeAdapter().execute(verification, child)
        return self._finish(arguments, ctx, result, records, artifacts)

    def reconcile(self, arguments, ctx: ToolContext):
        ctx.check()
        cached = _reconcile_local(arguments, ctx)
        if cached.outcome == "completed":
            return cached
        files, settings, verification = self._prepare(arguments, ctx.config)
        intent = ctx.load_state().get("software_intent")
        if not intent:
            return Reconciliation("not_executed", detail="No calculator artifact or sandbox intent was recorded.")
        if intent["arguments_sha256"] != _arguments_hash(arguments):
            return Reconciliation("uncertain", detail="Calculator scope/design differs from its durable intent.")
        try:
            not_started = not ctx.load_state().get("software_code", {}).get("code_container")
            records, artifacts = self._files(files, ctx, write=False, allow_missing=not_started)
            if not_started:
                return Reconciliation("not_executed", detail="Sandbox execution never started; existing source stages have verified immutable hashes and the original action can safely finish them.")
            recovered = CodeAdapter().reconcile(verification, _child(ctx, "software_code", settings))
            if recovered.outcome == "completed" and recovered.result:
                return Reconciliation("completed", self._finish(arguments, ctx, recovered.result, records, artifacts), "Recovered generated files and the existing sandbox result without another execution.")
            if recovered.outcome == "not_executed":
                return Reconciliation("not_executed", detail="Source stages are immutable and complete; code sandbox was proven not to have executed.")
            return Reconciliation("uncertain", detail=recovered.detail)
        except ToolError as exc:
            return Reconciliation("uncertain", detail=exc.message)
