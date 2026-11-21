"""What the Rust engine (engine/scad) mirrors from this one, pinned.

Cache signatures must come out byte for byte the same in both engines, or
neither can reuse the other's saved results. engine/scad/src/signatures.rs
lists the feature types whose parameter names come from a few inputs only;
a new override here must be mirrored there.
"""

import simplecad.kernel  # noqa: F401 - registers every feature type
from simplecad.core.document import REGISTRY, Feature


def test_the_feature_types_with_their_own_parameter_names_are_the_ones_rust_knows():
    own = {name for name, klass in REGISTRY.items()
           if klass.parameter_names is not Feature.parameter_names}
    assert own == {"edge_finish", "face_texture", "text"}
