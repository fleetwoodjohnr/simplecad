import multiprocessing

# Before anything else: in a frozen build the geometry process is started by
# re-running this executable, and freeze_support is what turns that run into
# the worker instead of a second window.
multiprocessing.freeze_support()

from .app import main  # noqa: E402

raise SystemExit(main())
