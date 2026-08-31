import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { IdeError, loadSettings } from "../dist/config.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("settings prefer secret storage then OBSION_TOKEN and reject credential keys", () => {
  const settings = loadSettings(
    { baseUrl: "http://from-settings:8080", protocol: "rest" },
    { OBSION_TOKEN: "from-env", OBSION_URL: "http://from-env:8080" },
    "from-secret",
  );
  assert.equal(settings.baseUrl, "http://from-settings:8080");
  assert.equal(settings.protocol, "rest");
  assert.equal(settings.token, "from-secret");
  assert.throws(
    () => loadSettings({ token: "nope" }, {}),
    (error) => error instanceof IdeError && /must not contain credentials/.test(error.message),
  );
  assert.throws(
    () => loadSettings({ "obsion.token": "nope" }, {}),
    (error) => error instanceof IdeError,
  );
});

test("missing settings fall back to local App Server defaults", () => {
  const settings = loadSettings({}, {});
  assert.equal(settings.baseUrl, "http://127.0.0.1:8080");
  assert.equal(settings.protocol, "app-server");
  assert.equal(settings.token, undefined);
  assert.throws(
    () => loadSettings({ protocol: "harness" }, {}),
    (error) => error instanceof IdeError && /Protocol must be rest or app-server/.test(error.message),
  );
});

test("package.json does not contribute credential settings", () => {
  const manifest = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  const properties = manifest.contributes.configuration.properties;
  assert.deepEqual(Object.keys(properties).sort(), ["obsion.baseUrl", "obsion.protocol"]);
});

test("sources do not import a second Harness or control plane", () => {
  const forbidden = [
    "obsion.harness",
    "obsion/harness",
    "sqlalchemy",
    "fastapi",
    "from \"obsion\"",
    "obsion.db",
    "obsion.capabilities",
  ];
  const src = join(ROOT, "src");
  const violations = [];
  for (const name of readdirSync(src)) {
    if (!name.endsWith(".ts")) continue;
    const text = readFileSync(join(src, name), "utf8");
    for (const needle of forbidden) {
      if (text.includes(needle)) violations.push(`${name} contains ${needle}`);
    }
    if (
      name !== "extension.ts" &&
      (text.includes('from "vscode"') || text.includes("from 'vscode'") || text.includes("import * as vscode"))
    ) {
      violations.push(`${name} imports vscode`);
    }
  }
  assert.deepEqual(violations, []);
});
