import assert from "node:assert/strict";
import test from "node:test";
import { mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { DesktopError, loadSettings } from "../dist/config.js";
import { FileSecretStore, defaultSecretPath } from "../dist/secrets.js";

test("secret file is created with owner-only permissions", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-"));
  const path = join(dir, "desktop.secret");
  const store = new FileSecretStore(path);
  await store.store("obsion.token", "desktop-secret-token");
  assert.equal(await store.get("obsion.token"), "desktop-secret-token");
  const mode = (await stat(path)).mode & 0o777;
  assert.equal(mode, 0o600);
  const raw = await readFile(path, "utf8");
  assert.match(raw, /desktop-secret-token/);
  await store.delete("obsion.token");
  assert.equal(await store.get("obsion.token"), undefined);
});

test("default secret path stays out of config.toml", () => {
  assert.equal(defaultSecretPath("/tmp/home"), "/tmp/home/.config/obsion/desktop.secret");
});

test("JSON desktop config with a credential key is rejected before loadSettings", async () => {
  const dir = await mkdtemp(join(tmpdir(), "obsion-desktop-cfg-"));
  const path = join(dir, "desktop.json");
  await writeFile(path, JSON.stringify({ baseUrl: "http://127.0.0.1:8080", token: "nope" }));
  const document = JSON.parse(await readFile(path, "utf8"));
  assert.throws(
    () => loadSettings(document, {}),
    (error) => error instanceof DesktopError && /must not contain credentials/.test(error.message),
  );
});
