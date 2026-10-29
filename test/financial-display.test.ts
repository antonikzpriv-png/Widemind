import test from "node:test";
import assert from "node:assert/strict";
import { displayUsd } from "../src/web/pages.ts";

test("financial display preserves exact USD, sub-cent charges and negative liabilities", () => {
  assert.equal(displayUsd("5000000"), "$5.00");
  assert.equal(displayUsd("1"), "$0.000001");
  assert.equal(displayUsd("-1000000"), "-$1.00");
  assert.equal(displayUsd("9007199254740993"), "$9007199254.740993");
  assert.equal(displayUsd("0"), "$0.00");
  for (const invalid of ["1.2", "-0", "01", "<script>"])
    assert.throws(() => displayUsd(invalid));
});
