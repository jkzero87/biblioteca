// Test stub of @deepseek-ai/dsh-llm: only what dsh-biblioteca-cue imports. The harness's createUserMessage adds
// a fresh id and role "user" and freezes the message; the stub does the same with a fixed id.
export function createUserMessage(input) {
  return Object.freeze({ id: "msg-test", role: "user", ...input });
}
