import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { patentMilvusTool } from "./tool.js";

export default function (pi: ExtensionAPI) {
	pi.registerTool(patentMilvusTool);
}
