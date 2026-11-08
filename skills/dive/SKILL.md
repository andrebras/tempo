---
name: dive
description: Asks for more on a Tempo question. Typed by the user as /tempo:dive 57, optionally followed by what to dig into.
argument-hint: "[number, such as 57, then an optional follow-up]"
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash dive *)
---

The user asked for more on a Tempo question by typing /tempo:dive. Tempo has already recorded
the follow-up and marked it delivered, so no hook passes it on again:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash dive --cwd "${CLAUDE_PROJECT_DIR}" <<'TEMPO_END_OF_TYPED_TEXT_9C4F2A7E'
$ARGUMENTS
TEMPO_END_OF_TYPED_TEXT_9C4F2A7E
```

If the output says nothing was recorded, tell the user why in one line and do nothing else.

Dig into the follow-up and answer it in the question itself, as the Tempo skill says under
"Handling a follow-up": `tempo reword <number> --note "Why it changed" --context "…"` (plus
`--option` and `--recommended` when those change). Research what you need; a subagent is fine
for that. Then tell the user in chat that the question was updated, with its link. The
follow-up is recorded: don't record it again. Only the user answers or follows up, so never
run `tempo answer` or `tempo reply`.
