import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { cpSync, existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

test("standalone Milvus build and startup need neither Python nor parent KG sources", { timeout: 60000 }, () => {
  const source = fileURLToPath(new URL("..", import.meta.url));
  const root = mkdtempSync(join(tmpdir(), "coat-weave-standalone-"));
  try {
    for (const name of ["package.json", "package-lock.json", "tsconfig.json", "tsconfig.build.json", "src", "scripts", "node_modules"]) {
      cpSync(join(source, name), join(root, name), { recursive: true,
        filter: path => !path.endsWith("kg-tools-schema.json") });
    }
    // Any accidental Python invocation fails, even if the host has Python.
    mkdirSync(join(root, "bin"));
    writeFileSync(join(root, "bin", "python3"), "#!/bin/sh\nexit 99\n", { mode: 0o755 });
    const env = { PATH: `${join(root, "bin")}:${process.env.PATH}`, HOME: root, npm_config_update_notifier: "false" };
    const build = spawnSync("npm", ["run", "build"], { cwd: root, env, encoding: "utf8", timeout: 30000 });
    assert.equal(build.status, 0, build.stdout + build.stderr);
    assert.equal(existsSync(join(root, "dist/kg-tools-schema.json")), false);
    const smoke = spawnSync(process.execPath, ["scripts/smoke.mjs", "milvus"], { cwd: root, env, encoding: "utf8", timeout: 15000 });
    assert.equal(smoke.status, 0, smoke.stdout + smoke.stderr);
  } finally { rmSync(root, { recursive: true, force: true }); }
});
