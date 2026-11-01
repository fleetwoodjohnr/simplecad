"""The frozen app's entry point (PyInstaller cannot start from ``-m``)."""

import multiprocessing

# First: in a frozen build the geometry worker is this executable started again,
# and freeze_support is what makes that run the worker instead of a window.
multiprocessing.freeze_support()

from simplecad.app import main  # noqa: E402

raise SystemExit(main())
