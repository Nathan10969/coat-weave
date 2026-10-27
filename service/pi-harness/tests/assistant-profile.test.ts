import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { DefaultResourceLoader, SettingsManager } from "@earendil-works/pi-coding-agent";
import { appendCompletionSystemPrompt, isInitialGreeting } from "../src/assistant-profile.js";

test("only whole-message greetings without conversation history qualify for fixed copy", () => {
  for (const text of ["你好！", "您好", "  你好呀！\n", "Hi!", "HELLO", "嗨", "下午好。", "哈喽！"]) {
    assert.equal(isInitialGreeting(text, []), true, text);
    assert.equal(isInitialGreeting(text, [{ role: "user", text: "之前的问题" }]), false, text);
    assert.equal(isInitialGreeting(text, [{ role: "assistant", text: "之前的回答" }]), false, text);
  }
  for (const text of ["", "你好，帮我检索材料专利", "你好！\n帮我做预测", "Hello, compare these patents", "请回复你好", "你好，不能访问实验数据怎么办？"]) {
    assert.equal(isInitialGreeting(text, []), false, text);
  }
});

test("SDK loads project prompts and preserves upstream constraints before coat-weave-pi identity policy", async () => {
  const directory = await mkdtemp(join(tmpdir(), "coatweave-profile-test-"));
  const cwd = join(directory, "project");
  const agentDir = join(directory, "agent");
  try {
    await mkdir(join(cwd, ".pi"), { recursive: true });
    await mkdir(agentDir);
    await writeFile(join(cwd, ".pi", "SYSTEM.md"), "Project research and evidence rules.");
    await writeFile(join(cwd, ".pi", "APPEND_SYSTEM.md"), "Project citation rules.");
    await writeFile(join(agentDir, "SYSTEM.md"), "Account fallback.");
    const upstream = "你是磷酸铁锂工厂设计顾问。请用中文，保留引用，只使用授权资料。";
    const loader = new DefaultResourceLoader({ cwd, agentDir, settingsManager: SettingsManager.inMemory(),
      noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
      appendSystemPromptOverride: base => appendCompletionSystemPrompt(base, upstream),
    });
    await loader.reload();
    assert.equal(loader.getSystemPromptSource()?.path, join(cwd, ".pi", "SYSTEM.md"));
    assert.equal(loader.getSystemPrompt(), "Project research and evidence rules.");
    const prompts = loader.getAppendSystemPrompt();
    assert.deepEqual(prompts.slice(0, -1), ["Project citation rules.", upstream]);
    assert.match(prompts.at(-1)!, /You are CoatWeave/);
    assert.match(prompts.at(-1)!, /Never\s+invent/);
    assert.match(prompts.at(-1)!, /reference data/);
  } finally { await rm(directory, { recursive: true, force: true }); }
});
