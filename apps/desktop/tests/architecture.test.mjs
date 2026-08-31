import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { DesktopError, loadSettings } from "../dist/config.js";
import { parseArgs } from "../dist/main.js";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

test("settings prefer the secret file then OBSION_TOKEN and reject credential keys", () => {
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
    (error) => error instanceof DesktopError && /must not contain credentials/.test(error.message),
  );
  assert.throws(
    () => loadSettings({ "obsion.token": "nope" }, {}),
    (error) => error instanceof DesktopError,
  );
});

test("missing settings fall back to local App Server defaults", () => {
  const settings = loadSettings({}, {});
  assert.equal(settings.baseUrl, "http://127.0.0.1:8080");
  assert.equal(settings.protocol, "app-server");
  assert.equal(settings.token, undefined);
  assert.throws(
    () => loadSettings({ protocol: "harness" }, {}),
    (error) => error instanceof DesktopError && /Protocol must be rest or app-server/.test(error.message),
  );
});

test("package.json does not contribute credential settings", () => {
  const manifest = JSON.parse(readFileSync(join(ROOT, "package.json"), "utf8"));
  assert.deepEqual(Object.keys(manifest.dependencies), ["@obsion/sdk"]);
  const dump = JSON.stringify(manifest);
  assert.equal(dump.includes("obsion.token"), false);
  assert.equal(dump.includes("obsion.password"), false);
  assert.equal(Object.hasOwn(manifest, "optionalDependencies"), false);
});

test("parser treats ask as a headless Experience command", () => {
  const args = parseArgs(["--url", "http://127.0.0.1:8080", "ask", "你好"]);
  assert.equal(args.command, "ask");
  assert.equal(args.question, "你好");
  assert.equal(args.url, "http://127.0.0.1:8080");
});

test("sources do not import a second Harness, control plane, or Electron outside the host", () => {
  const forbidden = [
    "obsion.harness",
    "obsion/harness",
    "sqlalchemy",
    "fastapi",
    'from "obsion"',
    "obsion.db",
    "obsion.capabilities",
  ];
  const electronImport = ['from "electron"', "from 'electron'", "import(\"electron\")"];
  const src = join(ROOT, "src");
  const violations = [];
  for (const name of readdirSync(src)) {
    if (!name.endsWith(".ts")) continue;
    const text = readFileSync(join(src, name), "utf8");
    for (const needle of forbidden) {
      if (text.includes(needle)) violations.push(`${name} contains ${needle}`);
    }
    if (name !== "electron-main.ts" && name !== "electron.d.ts") {
      for (const needle of electronImport) {
        if (text.includes(needle)) violations.push(`${name} imports electron`);
      }
    }
  }
  assert.deepEqual(violations, []);
});
