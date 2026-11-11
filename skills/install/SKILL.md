---
name: install
description: Sets Tempo up for the user's own terminal and tmux with `tempo install --tmux`, and shows the line the user adds to their tmux config themselves. Typed by the user as /tempo:install.
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --tmux --slash)
---

The user typed /tempo:install to use Tempo in their own terminal and tmux. Tempo has
already run `tempo install --tmux`, which links `~/.local/bin/tempo` to the copy of Tempo
that stays put across updates and, when tmux is there, turns the questions' sidebar on:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --tmux --slash
```

Tell the user what it did in a sentence or two, from the output above. If it says
`~/.local/bin` is not on their PATH, give them the line it prints to add to their shell's
config, and say they need a new terminal afterwards. If something else was already at
`~/.local/bin/tempo`, say what it is and that it was left alone, and stop there.

If the output has a line to add to the tmux config, show the user that line exactly, in a
code block, and the file it names. Say that Tempo didn't change that file: adding the line
is their own step, and it brings the sidebar back each time tmux starts. Then say whether
the sidebar is on now or comes on when tmux next starts, from the output. If the output
says the file already brings the sidebar back, or that tmux isn't installed, say so. Don't
change their files or run anything else yourself.
