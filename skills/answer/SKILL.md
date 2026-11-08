---
name: answer
description: Answers a Tempo question from the session. Typed by the user as /tempo:answer 57a, optionally followed by a note.
argument-hint: "[code, such as 57a or 57 yes, then an optional note]"
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash answer *)
---

The user answered a Tempo question by typing /tempo:answer. Tempo has already recorded the
answer and marked it delivered, so no hook passes it on again:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash answer --cwd "${CLAUDE_PROJECT_DIR}" <<'TEMPO_END_OF_TYPED_TEXT_9C4F2A7E'
$ARGUMENTS
TEMPO_END_OF_TYPED_TEXT_9C4F2A7E
```

If the output says nothing was recorded, tell the user why in one line and do nothing else.

Act on the answer now, as the Tempo skill says under "Handling an answer", and tell the user
in chat what you did with it. If the note asks something, reply in the question with
`tempo context <number> "…"`. The answer is recorded: don't record it again. Only the user
answers, so never run `tempo answer` or `tempo reply`.
