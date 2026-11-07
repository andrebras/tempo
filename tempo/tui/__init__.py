"""`tempo tui`: the page's questions in the terminal, in panels side by side, keyboard
only. model.py holds what the screen shows and what its keys do, text.py turns the page's
light Markdown into wrapped, styled lines, and view.py draws it all onto a canvas; none
of them needs a terminal. screen.py is the thin curses layer that shows it."""
