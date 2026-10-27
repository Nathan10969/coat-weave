import { cpSync, readFileSync, realpathSync, rmSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

// Pi's published shrinkwrap bypasses the root override during npm ci.
// Backport only the audited compatible brace-expansion patch, from the locked
// root dependency. Keep this explicit; no third-party install hooks are enabled.
const root = realpathSync(fileURLToPath(new URL("../", import.meta.url)));
const sdk = join(root, "node_modules/@earendil-works/pi-coding-agent");
const source = join(root, "node_modules/brace-expansion");
const target = join(sdk, "node_modules/brace-expansion");
const packageAt = path => JSON.parse(readFileSync(join(path, "package.json"), "utf8"));
if (packageAt(sdk).version !== "0.86.0" || packageAt(source).version !== "5.0.12")
  throw new Error("Unexpected dependency versions; review the security backport before upgrading.");
const inside = relative(root, realpathSync(target));
if (inside.startsWith(`..${sep}`) || inside === "..") throw new Error("Unexpected dependency location.");
const version = packageAt(target).version;
if (version === "5.0.9") {
  rmSync(target, { recursive: true });
  cpSync(source, target, { recursive: true });
} else if (version !== "5.0.12") throw new Error("Unexpected nested dependency; review before modifying it.");
const require = createRequire(join(sdk, "package.json"));
const nestedRequire = createRequire(require.resolve("minimatch"));
if (nestedRequire("brace-expansion/package.json").version !== "5.0.12")
  throw new Error("The actual minimatch dependency is not the fixed version.");
console.log("Verified actual SDK brace-expansion 5.0.12; third-party install hooks remain disabled.");
