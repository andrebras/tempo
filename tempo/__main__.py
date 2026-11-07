import os
import sys

# bin/tempo runs this file directly, so Python put its folder (tempo/) first on the
# search path, where cli.py, log.py and the rest would stand in for top-level modules of
# the same name. It comes off; PYTHONPATH supplies the folder that holds the package.
here = os.path.dirname(os.path.realpath(__file__))
if sys.path and os.path.realpath(sys.path[0] or os.curdir) == here:
    del sys.path[0]

from tempo.cli import main  # noqa: E402

sys.exit(main())
