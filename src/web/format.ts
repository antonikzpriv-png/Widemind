export const escape = (value: unknown) =>
  String(value).replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ]!,
  );
/** Exact USD display; sub-cent charges remain visible without floating-point rounding. */
export function displayUsd(micros: string): string {
  if (!/^(0|-?[1-9][0-9]*)$/.test(micros) || micros.length > 129)
    throw new Error("Invalid financial display value");
  const value = BigInt(micros),
    absolute = value < 0n ? -value : value;
  let fraction = (absolute % 1000000n).toString().padStart(6, "0");
  fraction = fraction.endsWith("0000")
    ? fraction.slice(0, 2)
    : fraction.replace(/0+$/, "");
  return `${value < 0n ? "-" : ""}$${absolute / 1000000n}.${fraction}`;
}
