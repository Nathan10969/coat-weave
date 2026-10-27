import { copyFileSync } from "node:fs";
copyFileSync(new URL("../src/patent-search/tool_schema.json", import.meta.url),
  new URL("../dist/patent-search/tool_schema.json", import.meta.url));
copyFileSync(new URL("../src/kg-tools-schema.json", import.meta.url),
  new URL("../dist/kg-tools-schema.json", import.meta.url));
