---
name: show
description: Shows one Tempo question with its options and thread. Typed by the user as /tempo:show 57.
argument-hint: "[number, such as 57 or 57b]"
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash show *)
---

The user typed /tempo:show to see this Tempo question:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo slash show --cwd "${CLAUDE_PROJECT_DIR}" <<'TEMPO_END_OF_TYPED_TEXT_9C4F2A7E'
$ARGUMENTS
TEMPO_END_OF_TYPED_TEXT_9C4F2A7E
```

If the output says the question was not found ("Nothing was recorded: …"), tell the user why
in one line and do nothing else.

Present it briefly. Start with one paragraph that names the question by its number, as the
Tempo skill says: `**[Q57](link): Title**`, with the link from the output's last line (with
the page off, just `**Q57: Title**`). Put the options with their letters right under that
line, in the same paragraph, marking the recommended one; then the latest entries of its
thread. Keeping the options next to the number shows they belong to a question already in
Tempo. Don't act on it unless the user asks. Only the user answers, so never run
`tempo answer` or `tempo reply`.
