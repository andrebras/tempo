---
name: list
description: Lists this project's open Tempo questions. Typed by the user as /tempo:list.
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash list *)
---

The user typed /tempo:list to see this project's open questions in Tempo:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash list --cwd "${CLAUDE_PROJECT_DIR}" <<'TEMPO_END_OF_TYPED_TEXT_9C4F2A7E'
$ARGUMENTS
TEMPO_END_OF_TYPED_TEXT_9C4F2A7E
```

If the output says nothing was recorded, tell the user why in one line and do nothing else.

Show them to the user briefly, one line each with the number and title. Don't act on any of
them unless the user asks. Only the user answers, so never run `tempo answer` or `tempo reply`.
