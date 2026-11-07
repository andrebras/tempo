---
name: show
description: Shows one Tempo question with its options and thread. Typed by the user as /tempo:show 57.
argument-hint: "[number, such as 57 or 57b]"
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash show *)
---

The user typed /tempo:show to see this Tempo question:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash show --cwd "${CLAUDE_PROJECT_DIR}" <<'TEMPO_TYPED_TEXT'
$ARGUMENTS
TEMPO_TYPED_TEXT
```

If the output says nothing was recorded, tell the user why in one line and do nothing else.

Present it briefly: the title, the options with their letters (and which one is recommended),
and the latest entries of its thread. Don't act on it unless the user asks. Only the user
answers, so never run `tempo answer` or `tempo reply`.
