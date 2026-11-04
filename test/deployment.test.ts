import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";

const units = ["deployment", "deploy"].flatMap((dir) =>
  readdirSync(dir)
    .filter((name) => /\.(service|timer)$/.test(name))
    .map((name) => ({
      dir,
      name,
      text: readFileSync(join(dir, name), "utf8"),
    })),
);
function sourceText(): string {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (
        ["node_modules", "__pycache__", ".venvs", "tests", "test"].includes(
          entry.name,
        )
      )
        continue;
      const path = join(dir, entry.name);
      if (entry.isDirectory()) walk(path);
      else if (/\.(ts|mjs|py)$/.test(entry.name))
        files.push(readFileSync(path, "utf8"));
    }
  };
  for (const dir of ["src", "services", "scripts"]) walk(dir);
  return files.join("\n");
}

test("deployment units run files and modules that exist", () => {
  assert.ok(units.length >= 10);
  for (const unit of units) {
    for (const match of unit.text.matchAll(
      /\/opt\/widemind\/dist\/(\S+)\.js/g,
    )) {
      const source = join("src", match[1]! + ".ts");
      assert.ok(existsSync(source), `${unit.name} runs missing ${source}`);
    }
    for (const match of unit.text.matchAll(
      /\.venvs\/(\w+)\/bin\/python -I -m (\S+)/g,
    )) {
      const [, venv, module] = match;
      const roots = {
        business: "services/business",
        tools: "services/tools/src",
        economy: "services/economy/src",
      } as Record<string, string>;
      const path = join(roots[venv!]!, ...module!.split("."));
      assert.ok(
        existsSync(path + ".py") || existsSync(join(path, "__main__.py")),
        `${unit.name} starts missing ${module}`,
      );
    }
    if (unit.name.endsWith(".timer"))
      assert.ok(
        units.some(
          (other) => other.name === unit.name.replace(".timer", ".service"),
        ),
        `${unit.name} has no service`,
      );
    if (unit.name.endsWith(".service"))
      assert.match(unit.text, /UMask=0077/, unit.name);
  }
});

test("every variable in the example environment is read by the code", () => {
  const code = sourceText();
  const names = [
    ...readFileSync("deployment/widemind.env.example", "utf8").matchAll(
      /^([A-Z][A-Z0-9_]+)=/gm,
    ),
  ].map((m) => m[1]!);
  assert.ok(names.length > 20);
  for (const name of names)
    assert.ok(code.includes(name), `${name} is not read anywhere`);
  assert.doesNotMatch(
    readFileSync("deployment/widemind.env.example", "utf8"),
    /=[A-Za-z0-9]{32,}$/m,
    "real-looking secret",
  );
});
