"use strict";

// Shared hook-output contract for toolsmith's Node hook.
//
// Codex only accepts the `suppressOutput` field on UserPromptSubmit hooks
// ("suppressOutput field is only supported on UserPromptSubmit hooks"), so it
// MUST be omitted for PreToolUse / PostToolUse / Stop / unknown events. Every
// hook invocation should emit exactly one of these control objects on stdout.

function controlForEvent(eventName) {
  const out = { continue: true };
  if (String(eventName || "") === "UserPromptSubmit") {
    out.suppressOutput = true;
  }
  return out;
}

function emitControl(eventName, stream = process.stdout) {
  stream.write(`${JSON.stringify(controlForEvent(eventName))}\n`);
}

module.exports = { controlForEvent, emitControl };
