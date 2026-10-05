import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("main entry owns private chat modules while OBS entry stays isolated", async () => {
  const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
  const obs = await readFile(new URL("../src/obs.js", import.meta.url), "utf8");
  assert.match(main, /coreChatTransport\.js/);
  assert.match(main, /conversationController\.js/);
  assert.match(main, /privateAvatarSignals\.js/);
  assert.doesNotMatch(obs, /coreChat|private|microphone|capability/i);
  assert.doesNotMatch(obs, /privateAvatarSignals/);
});

test("mock conversation is no longer imported by the main entry", async () => {
  const main = await readFile(new URL("../src/main.js", import.meta.url), "utf8");
  assert.doesNotMatch(main, /mockConversation/);
});
