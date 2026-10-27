import { copyFileSync, existsSync, rmSync } from "node:fs";
copyFileSync(new URL("../src/patent-search/tool_schema.json", import.meta.url),
  new URL("../dist/patent-search/tool_schema.json", import.meta.url));
const kgSource = new URL("../src/kg-tools-schema.json", import.meta.url);
const kgBuild = new URL("../dist/kg-tools-schema.json", import.meta.url);
if (existsSync(kgSource)) copyFileSync(kgSource, kgBuild);
else rmSync(kgBuild, { force: true });
