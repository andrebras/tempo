---
name: install
description: Puts the tempo command on the user's own shell's PATH with `tempo install`. Typed by the user as /tempo:install.
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --slash)
---

The user typed /tempo:install to use the `tempo` command in their own shell. Tempo has
already run `tempo install`, which links `~/.local/bin/tempo` to the copy of Tempo that
stays put across updates:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --slash
```

Tell the user what it did in a sentence or two, from the output above. If it says
`~/.local/bin` is not on their PATH, give them the line it prints to add to their shell's
config, and say they need a new terminal afterwards. If something else was already at
`~/.local/bin/tempo`, say what it is and that it was left alone. Don't change their files or
run anything else yourself.
