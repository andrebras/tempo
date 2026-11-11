---
name: install
description: Sets Tempo up for the user's own terminal and tmux with `tempo install`, asking before it changes ~/.tmux.conf. Typed by the user as /tempo:install.
disable-model-invocation: true
allowed-tools: Bash("${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --slash), Bash(tempo install --tmux --slash), Bash(tempo add:*)
---

The user typed /tempo:install to use Tempo in their own terminal and tmux. Tempo has
already run `tempo install`, which links `~/.local/bin/tempo` to the copy of Tempo that
stays put across updates:

```!
"${CLAUDE_PLUGIN_ROOT}"/bin/tempo install --slash
```

Tell the user what it did in a sentence or two, from the output above. If it says
`~/.local/bin` is not on their PATH, give them the line it prints to add to their shell's
config, and say they need a new terminal afterwards. If something else was already at
`~/.local/bin/tempo`, say what it is and that it was left alone, and stop there.

If the output has a line starting "tmux is installed", Tempo can also set tmux up: it adds
three marked lines to the end of the tmux config file it names, so the questions' sidebar
comes back each time tmux starts, and turns the sidebar on. That changes one of the user's
files, so ask them first, with `tempo add` as the tempo skill says: a yes-or-no question,
with that line's file in the context. Only once they answer yes, run:

```bash
tempo install --tmux --slash
```

Then tell them what it did, from its output: which file got the lines (and, when that file
is the target of a symlink, that the link was left as it is), and whether the sidebar is on
now or comes on when tmux next starts. If they answer no, say that `tempo install --tmux`
does it later. Don't change their files or run anything else yourself.
