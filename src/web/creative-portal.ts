/** Customer scope entry and artifact controls; generated content runs only in a sandbox frame. */
export const creativeScopeFields = `
<div id="graphic-details" hidden><h3>Your graphic</h3><p>These requirements become part of your quote. Required words, size and colors stay fixed during included revisions. Visual preferences guide the design; judge those preferences in the preview.</p><div class="form-grid"><label>Width in pixels<input name="graphic_width" type="number" min="128" max="2048" value="1000" disabled required></label><label>Height in pixels<input name="graphic_height" type="number" min="128" max="2048" value="700" disabled required></label></div><p>The current graphic typeface supports printable ASCII letters, numbers and punctuation; other scripts are unavailable.</p><label>Exact words to include, one per line<textarea name="graphic_text" rows="4" maxlength="4000" disabled></textarea></label><label>Colors, separated by commas<input name="graphic_palette" value="#152b3b, #f8f1e7, #f0ac57" maxlength="200" disabled required></label><label>Visual requirements, one per line<textarea name="graphic_requirements" rows="3" maxlength="2000" disabled>Keep all text inside the canvas
Use a clear readable hierarchy</textarea></label><label>Included change rounds<select name="graphic_revisions" disabled><option>0</option><option selected>1</option><option>2</option></select></label></div>
<div id="calculator-details" hidden><h3>Your interactive calculator</h3><p>Describe the calculation in your brief, then name its inputs, results and examples. These examples are checked by running the finished calculator before delivery.</p><details open><summary>Inputs and ranges</summary><p>Each input needs a short name, a visible label, a minimum, a maximum and a starting value. Edit this example to match your project.</p><label>Inputs<textarea name="calculator_inputs" rows="8" maxlength="5000" disabled required>[{"id":"quantity","label":"Quantity","min":0,"max":1000,"default":2},{"id":"price","label":"Price per item","min":0,"max":10000,"default":25}]</textarea></label><label>Results<textarea name="calculator_outputs" rows="4" maxlength="2000" disabled required>[{"id":"total","label":"Total price"}]</textarea></label></details><details open><summary>Examples the calculator must pass</summary><p>For each example, supply input values and the result you expect. The names must match the inputs and results above. The tolerance is the allowed rounding difference.</p><label>Examples<textarea name="calculator_examples" rows="9" maxlength="7000" disabled required>[{"name":"Two items","inputs":{"quantity":2,"price":25},"expected":{"total":50},"tolerance":0.000001},{"name":"No items","inputs":{"quantity":0,"price":25},"expected":{"total":0},"tolerance":0.000001}]</textarea></label></details><fieldset><legend>Supported interface requirements</legend><p>Use your brief to explain the calculation. These are the interface features this calculator offering supports.</p><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Calculate the agreed results" checked disabled>Calculate the agreed results</label><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Update results when an input changes" checked disabled>Update results when an input changes</label><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Allow resetting inputs to their starting values" checked disabled>Allow resetting inputs to their starting values</label><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Reject invalid or out-of-range inputs" checked disabled>Reject invalid or out-of-range inputs</label><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Work on narrow screens" checked disabled>Work on narrow screens</label><label class="creative-feature"><input type="checkbox" name="calculator_requirement" value="Support keyboard entry" checked disabled>Support keyboard entry</label></fieldset><label>Included change rounds<select name="calculator_revisions" disabled><option>0</option><option selected>1</option><option>2</option></select></label></div>`;

export const creativePortalScript = String.raw`
function lines(value) {
  return value
    .split(/\r?\n/)
    .map((item) => item.trim())
    .filter(Boolean);
}
function jsonField(form, name, label) {
  try {
    return JSON.parse(form.elements[name].value);
  } catch {
    throw new Error(
      label +
        " must use valid JSON. Keep the example format and edit its values.",
    );
  }
}
function updateCreativeFields() {
  const form = $("#commerce-request");
  if (!form) return;
  const service = form.elements.service.value;
  form.elements.description.maxLength = [
    "custom-graphic",
    "interactive-calculator",
  ].includes(service)
    ? 2000
    : 4000;
  for (const [id, enabled] of [
    ["graphic-details", service === "custom-graphic"],
    ["calculator-details", service === "interactive-calculator"],
  ]) {
    const group = $("#" + id);
    group.hidden = !enabled;
    for (const field of group.querySelectorAll("input,textarea,select"))
      field.disabled = !enabled;
  }
}
function creativeRequestScope(form) {
  const scope = {
    title: form.elements.title.value,
    description: form.elements.description.value,
  };
  if (form.elements.service.value === "custom-graphic")
    return validateCreativeScope({
      ...scope,
      width: Number(form.elements.graphic_width.value),
      height: Number(form.elements.graphic_height.value),
      required_text: lines(form.elements.graphic_text.value).length
        ? lines(form.elements.graphic_text.value)
        : [scope.title],
      palette: form.elements.graphic_palette.value
        .split(",")
        .map((value) => value.trim())
        .filter(Boolean),
      visual_requirements: lines(form.elements.graphic_requirements.value),
      max_revisions: Number(form.elements.graphic_revisions.value),
    });
  if (form.elements.service.value === "interactive-calculator")
    return validateCreativeScope({
      ...scope,
      category: "calculator",
      inputs: jsonField(form, "calculator_inputs", "Inputs"),
      outputs: jsonField(form, "calculator_outputs", "Results"),
      acceptance_cases: jsonField(form, "calculator_examples", "Examples"),
      requirements: Array.from(
        form.querySelectorAll('[name="calculator_requirement"]:checked'),
        (field) => field.value,
      ),
      max_revisions: Number(form.elements.calculator_revisions.value),
    });
  return scope;
}
function validateCreativeScope(scope) {
  if (scope.category !== "calculator") {
    if (
      !Number.isInteger(scope.width) ||
      !Number.isInteger(scope.height) ||
      scope.width < 128 ||
      scope.height < 128 ||
      scope.width > 2048 ||
      scope.height > 2048 ||
      scope.width * scope.height > 4000000
    )
      throw new Error(
        "Choose a canvas between 128 and 2048 pixels per side, with at most four million pixels.",
      );
    if (
      !scope.palette.length ||
      scope.palette.some((color) => !/^#[0-9a-fA-F]{6}$/.test(color))
    )
      throw new Error(
        "Use six-digit colors such as #152b3b, separated by commas.",
      );
    if (
      !scope.required_text.length ||
      scope.required_text.length > 12 ||
      scope.required_text.some(
        (value) => value.length > 200 || !/^[\x20-\x7e]+$/.test(value),
      )
    )
      throw new Error(
        "Include one to twelve required text lines using printable ASCII letters, numbers and punctuation, at most 200 characters per line.",
      );
    return scope;
  }
  if (
    !Array.isArray(scope.inputs) ||
    scope.inputs.length < 1 ||
    scope.inputs.length > 8 ||
    !Array.isArray(scope.outputs) ||
    scope.outputs.length < 1 ||
    scope.outputs.length > 8
  )
    throw new Error("Provide one to eight inputs and one to eight results.");
  if (!scope.requirements.length)
    throw new Error("Choose at least one supported interface requirement.");
  const ids = new Set();
  for (const field of [...scope.inputs, ...scope.outputs]) {
    if (
      !field ||
      !/^[a-z][a-z0-9_]{0,31}$/.test(field.id) ||
      ids.has(field.id) ||
      ["constructor", "prototype", "then", "tojson"].includes(field.id) ||
      typeof field.label !== "string" ||
      !field.label.trim()
    )
      throw new Error(
        "Use unique short names starting with a lowercase letter, and a visible label, for each input and result.",
      );
    ids.add(field.id);
  }
  for (const input of scope.inputs)
    if (
      ![input.min, input.max, input.default].every(
        (value) => Number.isFinite(value) && Math.abs(value) <= 1e9,
      ) ||
      input.min >= input.max ||
      input.default < input.min ||
      input.default > input.max
    )
      throw new Error(
        "Each input needs a valid range and a starting value inside that range.",
      );
  if (
    !Array.isArray(scope.acceptance_cases) ||
    scope.acceptance_cases.length < 2 ||
    scope.acceptance_cases.length > 16
  )
    throw new Error(
      "Provide two to sixteen numerical examples the finished calculator must pass.",
    );
  const seen = new Set();
  for (const test of scope.acceptance_cases) {
    if (
      !test ||
      typeof test.name !== "string" ||
      !test.name.trim() ||
      !test.inputs ||
      !test.expected ||
      !Number.isFinite(test.tolerance) ||
      test.tolerance < 0 ||
      test.tolerance > 0.01
    )
      throw new Error(
        "Every example needs a name, input values, expected results and a nonnegative tolerance.",
      );
    if (
      scope.inputs.some(
        (input) =>
          !Number.isFinite(test.inputs[input.id]) ||
          test.inputs[input.id] < input.min ||
          test.inputs[input.id] > input.max,
      ) ||
      scope.outputs.some((output) => !Number.isFinite(test.expected[output.id]))
    )
      throw new Error(
        "Example values must match the agreed input ranges and name every result.",
      );
    const key = JSON.stringify(
      scope.inputs.map((input) => test.inputs[input.id]),
    );
    if (seen.has(key))
      throw new Error("Use distinct input values in each numerical example.");
    seen.add(key);
  }
  return scope;
}
function appendCreativeScope(box, scope) {
  if (scope.width && scope.height)
    box.append(
      node("p", "Canvas: " + scope.width + " × " + scope.height + " pixels"),
    );
  if (Array.isArray(scope.required_text))
    box.append(node("p", "Required words: " + scope.required_text.join(" · ")));
  if (Array.isArray(scope.palette))
    box.append(node("p", "Colors: " + scope.palette.join(", ")));
  if (scope.max_revisions !== undefined)
    box.append(node("p", "Included change rounds: " + scope.max_revisions));
  for (const key of ["visual_requirements", "requirements"])
    if (Array.isArray(scope[key])) {
      const list = node("ul");
      for (const value of scope[key]) list.append(node("li", value));
      box.append(list);
    }
  if (Array.isArray(scope.inputs)) {
    box.append(node("h4", "Inputs"));
    for (const field of scope.inputs)
      box.append(
        node(
          "p",
          field.label +
            " (" +
            field.id +
            "): " +
            field.min +
            " to " +
            field.max +
            "; starts at " +
            field.default,
        ),
      );
  }
  if (Array.isArray(scope.outputs))
    box.append(
      node(
        "p",
        "Results: " +
          scope.outputs
            .map((field) => field.label + " (" + field.id + ")")
            .join(", "),
      ),
    );
  if (Array.isArray(scope.acceptance_cases)) {
    const examples = node("details");
    examples.append(node("summary", "Agreed calculation checks"));
    for (const test of scope.acceptance_cases)
      examples.append(
        node(
          "p",
          test.name +
            ": " +
            JSON.stringify(test.inputs) +
            " → " +
            JSON.stringify(test.expected) +
            "; tolerance " +
            test.tolerance,
        ),
      );
    box.append(examples);
  }
}
function appendFulfillment(card, value) {
  const labels = {
    budget: "Authorizing the agreed work",
    generate: "Preparing your custom design",
    verify: "Checking the deliverable",
    ready: "Preparing private delivery",
    complete: "Delivered",
    needs_review: "Needs operator review",
    payment_hold: "Waiting for payment review",
    expired: "Included change period has ended",
  };
  card.append(
    node(
      "p",
      labels[value.stage] || "Fulfillment: " + (value.status || "pending"),
    ),
  );
  if (Array.isArray(value.errors) && value.errors.length) {
    const details = node("details");
    details.append(node("summary", "Work needs attention"));
    for (const error of value.errors)
      details.append(
        node("p", typeof error === "string" ? error : JSON.stringify(error)),
      );
    card.append(details);
  }
}
function downloadBytes(content, encoding, filename, mediaType) {
  const bytes =
    encoding === "base64"
      ? Uint8Array.from(atob(content), (c) => c.charCodeAt(0))
      : content;
  const url = URL.createObjectURL(
    new Blob([bytes], { type: mediaType || "application/octet-stream" }),
  );
  const link = node("a");
  link.href = url;
  link.download = filename || "delivery.txt";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function privateDownload(card, label, body, status) {
  const button = node("button", label);
  button.type = "button";
  button.onclick = async () => {
    button.disabled = true;
    try {
      const artifact = await api("/api/modules/business/delivery", body);
      downloadBytes(
        artifact.content,
        artifact.encoding,
        artifact.filename,
        artifact.mediaType,
      );
    } catch (error) {
      status.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
  card.append(button);
}
function previewControl(card, body, status) {
  const open = node("button", "Open isolated preview");
  open.type = "button";
  const frame = node("iframe");
  frame.name = "widemind-preview-" + randomId();
  frame.title = "Private deliverable preview";
  frame.setAttribute("sandbox", "allow-scripts");
  frame.setAttribute("credentialless", "");
  frame.setAttribute("referrerpolicy", "no-referrer");
  frame.setAttribute("width", "100%");
  frame.setAttribute("height", "420");
  frame.hidden = true;
  const form = node("form");
  form.hidden = true;
  form.method = "POST";
  form.action = "/api/modules/business/preview";
  form.target = frame.name;
  for (const [name, value] of Object.entries(body)) {
    if (value === undefined) continue;
    const field = node("input");
    field.type = "hidden";
    field.name = name;
    field.value = String(value);
    form.append(field);
  }
  open.onclick = () => {
    frame.hidden = false;
    form.submit();
    status.textContent =
      "Preview opened in an isolated frame. It cannot access your request page or operator session.";
  };
  card.append(open, form, frame);
}
function appendArtifactVersions(card, order, token, status) {
  const versions = Array.isArray(order.artifactVersions)
    ? order.artifactVersions
    : [];
  if (!versions.length) {
    privateDownload(
      card,
      "Download your delivery",
      { token, orderId: order.orderId },
      status,
    );
    return;
  }
  for (const version of versions) {
    const group = node("details");
    group.open = version === versions[versions.length - 1];
    group.append(
      node(
        "summary",
        "Delivery version " + version.version + " · " + when(version.createdAt),
      ),
    );
    const files = Array.isArray(version.files) ? version.files : [];
    for (const file of files) {
      const item = node("div");
      item.append(
        node(
          "p",
          file.filename + " · " + file.role + " · " + file.size + " bytes",
        ),
      );
      if (typeof file.sha256 === "string") {
        const digest = node("small", "SHA-256 " + file.sha256);
        digest.className = "creative-digest";
        item.append(digest);
      }
      const body = {
        token,
        orderId: order.orderId,
        version: version.version,
        filename: file.filename,
      };
      privateDownload(item, "Download " + file.filename, body, status);
      if (
        file.mediaType === "text/html" ||
        file.mediaType === "image/png" ||
        file.mediaType === "image/svg+xml"
      )
        previewControl(item, body, status);
      group.append(item);
    }
    const manifest = node("button", "Download private manifest");
    manifest.type = "button";
    manifest.onclick = async () => {
      try {
        const value = await api("/api/modules/business/manifest", {
          token,
          orderId: order.orderId,
          version: version.version,
        });
        downloadBytes(
          JSON.stringify(value, null, 2),
          "utf-8",
          "manifest-v" + version.version + ".json",
          "application/json",
        );
      } catch (error) {
        status.textContent = error.message;
      }
    };
    group.append(manifest);
    if (version.checks) {
      const details = node("details");
      details.append(
        node("summary", "Recorded acceptance checks"),
        node("pre", JSON.stringify(version.checks, null, 2)),
      );
      group.append(details);
    }
    card.append(group);
  }
}
function appendChangeRequest(card, order, token, status) {
  if (!order.revision && !Array.isArray(order.artifactVersions)) return;
  const box = node("details");
  box.append(node("summary", "Request a change"));
  const remaining = order.revision?.remaining ?? 0;
  if (order.revision?.deadline)
    box.append(
      node("p", "Included change deadline: " + when(order.revision.deadline)),
    );
  box.append(
    node(
      "p",
      remaining +
        " included change round(s) remain. Included changes keep the accepted scope and budget. New requirements need a separate quote.",
    ),
  );
  const form = node("form");
  const kind = node("select");
  kind.name = "kind";
  for (const [value, label] of [
    ["revision", "Included revision"],
    ["scope_change", "New scope and separate quote"],
  ]) {
    const option = node("option", label);
    option.value = value;
    if (value === "revision" && order.revision?.available !== true)
      option.disabled = true;
    kind.append(option);
  }
  if (order.revision?.available !== true) kind.value = "scope_change";
  const feedback = node("textarea");
  feedback.name = "feedback";
  feedback.required = true;
  feedback.maxLength = 2000;
  feedback.rows = 4;
  const feedbackLabel = node("label", "What should change?");
  feedbackLabel.append(feedback);
  const scope = node("textarea");
  scope.name = "scope";
  scope.rows = 12;
  scope.maxLength = 14000;
  scope.value = JSON.stringify(order.acceptedScope ?? {}, null, 2);
  const scopeGroup = node("div"),
    scopeLabel = node("label", "New complete scope");
  scopeLabel.append(
    node(
      "p",
      "For new requirements, provide the full scope in the same format as your accepted quote. This creates a separately quoted request.",
    ),
    scope,
  );
  scopeGroup.append(scopeLabel);
  scopeGroup.hidden = kind.value !== "scope_change";
  scope.disabled = scopeGroup.hidden;
  kind.onchange = () => {
    scopeGroup.hidden = kind.value !== "scope_change";
    scope.disabled = scopeGroup.hidden;
  };
  const kindLabel = node("label", "Type of change");
  kindLabel.append(kind);
  const submit = node("button", "Submit change request");
  const resultText = node("p");
  resultText.setAttribute("role", "status");
  form.append(kindLabel, feedbackLabel, scopeGroup, submit, resultText);
  let pendingId = null,
    pendingFingerprint = null;
  form.onsubmit = async (event) => {
    event.preventDefault();
    submit.disabled = true;
    try {
      const requestedScope =
        kind.value === "scope_change" ? JSON.parse(scope.value) : undefined;
      const fingerprint = JSON.stringify([
        kind.value,
        feedback.value,
        requestedScope,
      ]);
      if (!pendingId || pendingFingerprint !== fingerprint) {
        pendingId = randomId();
        pendingFingerprint = fingerprint;
      }
      const result = await api("/api/modules/business/change-request", {
        token,
        orderId: order.orderId,
        requestId: pendingId,
        kind: kind.value,
        feedback: feedback.value,
        ...(requestedScope ? { scope: requestedScope } : {}),
      });
      if (result.customerToken) {
        const link = node("a", "Open your new scope request");
        link.href =
          "/commerce#customer=" + encodeURIComponent(result.customerToken);
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        resultText.replaceChildren(
          node(
            "span",
            "New request recorded. No additional work or payment is assumed. ",
          ),
          link,
        );
      } else {
        resultText.textContent =
          "Change request recorded. Refresh the private workspace to follow the next version.";
      }
    } catch (error) {
      resultText.textContent =
        error instanceof SyntaxError
          ? "The new scope must use valid JSON."
          : error.message;
    } finally {
      submit.disabled = false;
    }
  };
  box.append(form);
  card.append(box);
}
`;

export const creativePortalCss = `
.creative-digest {
  display: block;
  overflow-wrap: anywhere;
}
.creative-feature {
  display: flex;
  align-items: center;
  gap: 10px;
}
.creative-feature input {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
}
.creative-feature {
  font-size: 13px;
}
#calculator-details fieldset {
  border: 1px solid #333;
  border-radius: 10px;
  padding: 14px;
  margin: 14px 0;
  min-width: 0;
}
#calculator-details legend {
  font-size: 13px;
  color: #bbb;
}
`;
