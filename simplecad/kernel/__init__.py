"""Geometry kernel.

Importing this package registers every built-in feature type, which is what
lets a saved document be loaded back by name.
"""

from . import (  # noqa: F401
    advanced, construction, fasteners, importing, operations, primitives,
    sketch_features, split, vent,
)
