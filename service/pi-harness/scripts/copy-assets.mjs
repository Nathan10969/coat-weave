import { copyFileSync } from "node:fs";
copyFileSync(new URL("../src/patent-search/tool_schema.json", import.meta.url),
  new URL("../dist/patent-search/tool_schema.json", import.meta.url));
