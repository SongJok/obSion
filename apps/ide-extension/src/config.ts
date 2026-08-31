export const TOKEN_SECRET_KEY = "obsion.token";

const FORBIDDEN_SETTING_KEYS = new Set([
  "token",
  "password",
  "secret",
  "api_key",
  "apikey",
  "bearer",
]);

export class IdeError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "IdeError";
  }
}

export type ExperienceProtocol = "rest" | "app-server";

export interface IdeSettings {
  baseUrl: string;
  protocol: ExperienceProtocol;
  token?: string;
  pollIntervalMs: number;
  waitTimeoutMs: number;
}

export function loadSettings(
  raw: Record<string, unknown>,
  env: Record<string, string | undefined>,
  secretToken?: string,
): IdeSettings {
  for (const key of Object.keys(raw)) {
    const compact = key.replaceAll(/[._-]/g, "").toLowerCase();
    const parts = key.toLowerCase().split(/[._-]/);
    if (
      FORBIDDEN_SETTING_KEYS.has(key.toLowerCase()) ||
      FORBIDDEN_SETTING_KEYS.has(compact) ||
      parts.some((part) => FORBIDDEN_SETTING_KEYS.has(part))
    ) {
      throw new IdeError(
        "Settings must not contain credentials. Use Obsion: Set Token or OBSION_TOKEN.",
      );
    }
  }
  const baseUrl = String(
    raw.baseUrl ?? env.OBSION_URL ?? env.OBSION_BASE_URL ?? "http://127.0.0.1:8080",
  ).replace(/\/$/, "");
  const protocol = String(raw.protocol ?? env.OBSION_PROTOCOL ?? "app-server").toLowerCase();
  if (protocol !== "rest" && protocol !== "app-server") {
    throw new IdeError("Protocol must be rest or app-server");
  }
  const envToken = env.OBSION_TOKEN?.trim();
  const stored = secretToken?.trim();
  const token = stored || envToken || undefined;
  return {
    baseUrl,
    protocol,
    ...(token ? { token } : {}),
    pollIntervalMs: 50,
    waitTimeoutMs: 120_000,
  };
}
