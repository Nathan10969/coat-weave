import { officialPiFactory } from "./pi.js";
import { createPiHttpServer } from "./server.js";
import { prepareLocalState, serverConfiguration } from "./config.js";

const config = serverConfiguration(process.env);
prepareLocalState(config, process.env);
const api = createPiHttpServer({
  factory: officialPiFactory(config.cwd, config.agentDir, config.toolMode),
  token: config.token,
  chatModels: [config.chatModel],
  completionTimeoutMs: config.timeout,
  eventLogPath: config.logPath,
});
api.server.listen(config.port, config.host, () => console.log(`CoatWeave API listening at http://${config.host}:${config.port}`));
let closing = false;
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => {
  if (closing) return;
  closing = true;
  void api.close().then(() => process.exit(0), () => process.exit(1));
});
