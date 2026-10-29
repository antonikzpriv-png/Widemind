/** Startup-only separation of the central Stripe RPC and integrated service protocols. */
const SERVICE_SETTINGS = [
  "WIDEMIND_ECONOMY_OPERATOR_TOKEN",
  "WIDEMIND_ECONOMY_GATEWAY_TOKEN",
  "WIDEMIND_ECONOMY_AGENT_TOKEN",
  "WIDEMIND_BUSINESS_URL",
  "WIDEMIND_BUSINESS_TOKEN",
  "WIDEMIND_TOOLS_URL",
  "WIDEMIND_TOOLS_TOKEN",
] as const;
const TOOL_IDENTITIES = {
  "gmail.read": ["gmail", "WIDEMIND_BUSINESS_GMAIL_ACCOUNT"],
  "gmail.send": ["gmail", "WIDEMIND_BUSINESS_GMAIL_ACCOUNT"],
  "research.collect": ["openai", "WIDEMIND_BUSINESS_OPENAI_ACCOUNT"],
  "commerce.checkout": ["commerce", "WIDEMIND_COMMERCE_ACCOUNT"],
  "commerce.fulfill": ["commerce", "WIDEMIND_COMMERCE_ACCOUNT"],
} as const;
type Mode = "sandbox" | "live";
function present(value: string | undefined): value is string {
  return typeof value === "string" && value.trim().length > 0;
}
function conflict(message: string): never {
  throw new Error(`Central brief-business protocol conflict: ${message}`);
}

/** No HTTP, account creation, financial reads, or environment mutation. */
export function assertCentralBusinessProtocol(env: NodeJS.ProcessEnv): void {
  if (SERVICE_SETTINGS.some((key) => present(env[key])))
    conflict(
      "use unprefixed central role credentials without business/tools services",
    );
  if (
    present(env.WIDEMIND_PAYMENT_PROVIDER) &&
    env.WIDEMIND_PAYMENT_PROVIDER !== "stripe"
  )
    conflict(
      "the central commerce bridge requires the Stripe payment provider",
    );

  const modes: Mode[] = [];
  const declared = (
    value: string | undefined,
    aliases: Record<string, Mode>,
    source: string,
  ) => {
    if (!present(value)) return;
    const mode = Object.hasOwn(aliases, value) ? aliases[value] : undefined;
    if (!mode) conflict(`invalid ${source}`);
    modes.push(mode);
  };
  declared(
    env.WIDEMIND_COMMERCE_MODE,
    { sandbox: "sandbox", live: "live" },
    "commerce mode",
  );
  declared(
    env.WIDEMIND_STRIPE_MODE,
    { test: "sandbox", live: "live" },
    "Stripe mode",
  );
  declared(
    env.WIDEMIND_BUSINESS_MODE,
    { test: "sandbox", live: "live" },
    "business mode",
  );
  declared(
    env.WIDEMIND_BUSINESS_TOOLS_PROFILE,
    { "provider-test": "sandbox", live: "live" },
    "business tool profile",
  );

  if (present(env.WIDEMIND_RUNTIME_TOOL_PROFILES)) {
    let profiles: unknown;
    try {
      profiles = JSON.parse(env.WIDEMIND_RUNTIME_TOOL_PROFILES);
    } catch {
      conflict("invalid central runtime tool profiles");
    }
    if (!profiles || typeof profiles !== "object" || Array.isArray(profiles))
      conflict("central runtime tool profiles must be an object");
    for (const [tool, [provider, accountSetting]] of Object.entries(
      TOOL_IDENTITIES,
    )) {
      const value: unknown = (profiles as Record<string, unknown>)[tool];
      if (value === undefined) continue; // Empty/retired profiles remain reconciliation-only.
      if (!value || typeof value !== "object" || Array.isArray(value))
        conflict("invalid central business tool identity");
      const profile = value as Record<string, unknown>;
      if (
        Object.keys(profile).some(
          (key) => !["provider", "account", "mode"].includes(key),
        ) ||
        profile.provider !== provider ||
        typeof profile.account !== "string" ||
        !profile.account.trim() ||
        (present(env[accountSetting]) &&
          profile.account !== env[accountSetting]) ||
        typeof profile.mode !== "string"
      )
        conflict(
          "central business provider/account identity differs from its tool profile",
        );
      declared(
        profile.mode,
        { sandbox: "sandbox", test: "sandbox", live: "live" },
        "runtime business tool mode",
      );
    }
  }
  if (new Set(modes).size > 1)
    conflict("Stripe, commerce, runtime tool, and provider modes must agree");
}
