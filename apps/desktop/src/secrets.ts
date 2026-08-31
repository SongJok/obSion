import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

import { TOKEN_SECRET_KEY } from "./config.js";

export function defaultSecretPath(home: string): string {
  return `${home}/.config/obsion/desktop.secret`;
}

export class FileSecretStore {
  constructor(private readonly filePath: string) {}

  async get(key: string): Promise<string | undefined> {
    if (key !== TOKEN_SECRET_KEY) return undefined;
    try {
      const value = (await readFile(this.filePath, "utf8")).trim();
      return value || undefined;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return undefined;
      throw error;
    }
  }

  async store(key: string, value: string): Promise<void> {
    if (key !== TOKEN_SECRET_KEY) return;
    await mkdir(dirname(this.filePath), { recursive: true, mode: 0o700 });
    await writeFile(this.filePath, `${value}\n`, { encoding: "utf8", mode: 0o600 });
    await chmod(this.filePath, 0o600);
  }

  async delete(key: string): Promise<void> {
    if (key !== TOKEN_SECRET_KEY) return;
    try {
      await rm(this.filePath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }
}
