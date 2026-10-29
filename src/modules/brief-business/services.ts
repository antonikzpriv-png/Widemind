import type { Json } from "../../contracts/index.ts";

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Expected an object");
  return value as Record<string, unknown>;
}
export function text(value: unknown, maximum = 1000): string {
  if (
    typeof value !== "string" ||
    !value.trim() ||
    value.length > maximum ||
    /[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(value)
  )
    throw new Error("Expected bounded text");
  return value.trim();
}
export function email(value: unknown): string {
  const result = text(value, 254).toLowerCase();
  if (!/^[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+$/.test(result))
    throw new Error("Invalid email");
  return result;
}
export function https(value: unknown): string {
  const result = text(value, 2048);
  const parsed = new URL(result);
  if (
    !/^https:\/\//i.test(result) ||
    parsed.protocol !== "https:" ||
    !parsed.hostname ||
    parsed.username ||
    parsed.password ||
    /\s/.test(result)
  )
    throw new Error("Expected an HTTPS URL");
  return result;
}
export function integer(
  value: unknown,
  minimum: number,
  maximum: number,
): number {
  if (
    !Number.isSafeInteger(value) ||
    Number(value) < minimum ||
    Number(value) > maximum
  )
    throw new Error("Invalid integer");
  return Number(value);
}
export interface Artifact {
  filename: string;
  mediaType: string;
  content: string;
  checks: Record<string, boolean | number>;
}
export interface BusinessServiceDefinition {
  name: string;
  capability: string;
  description: string;
  normalize(scope: unknown): Record<string, Json>;
  tool: string;
  artifact(scope: Record<string, Json>, result: unknown): Artifact;
}
export const researchBrief: BusinessServiceDefinition = {
  name: "research-brief",
  capability: "research",
  tool: "research.collect",
  description:
    "A private research brief with agreed sections and cited sources.",
  normalize(value) {
    const scope = record(value);
    const topic = text(scope.topic, 500);
    if (
      !Array.isArray(scope.sections) ||
      !scope.sections.length ||
      scope.sections.length > 8
    )
      throw new Error("Use one to eight sections");
    const sections = scope.sections.map((item) => text(item, 100));
    if (
      new Set(sections).size !== sections.length ||
      sections.some((item) => /[\r\n]/.test(item))
    )
      throw new Error("Section headings must be unique single lines");
    const minSources = integer(
      scope.minSources ?? scope.min_sources ?? 2,
      1,
      10,
    );
    const result: Record<string, Json> = { topic, sections, minSources };
    const supplied = scope.sourceUrls ?? scope.source_urls;
    if (supplied !== undefined) {
      if (
        !Array.isArray(supplied) ||
        supplied.length < minSources ||
        supplied.length > 10
      )
        throw new Error("Source URLs must meet the agreed source count");
      const urls = supplied.map(https);
      if (new Set(urls).size !== urls.length)
        throw new Error("Source URLs must be unique");
      result.sourceUrls = urls;
    }
    return result;
  },
  artifact(scope, value) {
    const result = record(value);
    if (
      !Array.isArray(result.sources) ||
      result.sources.length < Number(scope.minSources) ||
      result.sources.length > 30
    )
      throw new Error("Insufficient research sources");
    const sources = result.sources.map((item) => {
      const source = record(item);
      return {
        url: https(source.url),
        title: text(source.title, 500),
        excerpt: text(source.excerpt, 8000),
      };
    });
    const urls = new Set(sources.map((item) => item.url));
    if (
      urls.size !== sources.length ||
      !Array.isArray(result.sections) ||
      result.sections.length !== (scope.sections as Json[]).length
    )
      throw new Error("Research does not meet the agreed scope");
    const lines = [
      `# ${scope.topic}`,
      "",
      "Sourced research brief. Source summaries require human judgment.",
      "",
    ];
    result.sections.forEach((item, index) => {
      const section = record(item),
        citations = section.sourceUrls ?? section.source_urls;
      if (
        section.heading !== (scope.sections as Json[])[index] ||
        !Array.isArray(citations) ||
        !citations.length ||
        citations.some((url) => typeof url !== "string" || !urls.has(url))
      )
        throw new Error("Section citations do not match agreed sources");
      lines.push(
        `## ${section.heading}`,
        "",
        text(section.text, 20000),
        "",
        ...citations.map((url) => `Source: ${url}`),
        "",
      );
    });
    lines.push(
      "## Sources",
      "",
      ...sources.flatMap((source) => [
        source.title,
        source.url,
        `Excerpt: ${source.excerpt}`,
        "",
      ]),
    );
    return {
      filename: "research-brief.md",
      mediaType: "text/markdown",
      content: lines.join("\n"),
      checks: {
        scopeSections: true,
        citations: true,
        sourceCount: sources.length,
      },
    };
  },
};
