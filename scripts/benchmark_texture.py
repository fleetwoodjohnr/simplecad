"""Time face-texture previews the way the geometry worker builds them.

Each case runs ``build_preview_result`` (what a ``PREVIEW`` request executes),
then meshes and serialises the result exactly as the reply does. ``layout_ms``
is the 2-D layout alone: the panel recomputes it on the GUI thread, so it is
the per-keystroke cost the window pays before any kernel work.

Run:
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_texture.py
    PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B scripts/benchmark_texture.py --case hex60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from simplecad import kernel  # noqa: E402,F401 - registers feature types
from simplecad.core.document import BodyRef, Document  # noqa: E402
from simplecad.core.geometry_service import (  # noqa: E402
    build_preview_result, prepare_display, serialise_shape,
)
from simplecad.core.naming import make_ref, sub_shapes  # noqa: E402
from simplecad.core.rebuild import Rebuilder  # noqa: E402
from simplecad.kernel.detect import analyse_cylinder, analyse_plane  # noqa: E402
from simplecad.kernel.face_texture import FaceTextureFeature, layout_for_face  # noqa: E402
from simplecad.kernel.occ import is_valid  # noqa: E402
from simplecad.kernel.primitives import BoxFeature, CylinderFeature  # noqa: E402

CASES = {
    "hex30": ("plate", 30, {"pattern": "hexagon", "mode": "raised", "size": 3, "spacing": 1}),
    "hex60": ("plate", 60, {"pattern": "hexagon", "mode": "raised", "size": 3, "spacing": 1}),
    "dots60": ("plate", 60, {"pattern": "dots", "mode": "recessed", "size": 3, "spacing": 1}),
    "cyldots": ("cylinder", 10, {"pattern": "dots", "mode": "raised", "size": 3, "spacing": 2}),
}


def _face(shape, kind):
    if kind == "plate":
        return max((f for f in sub_shapes(shape, "face")
                    if (info := analyse_plane(f)) is not None and info.normal[2] > 0.9),
                   key=lambda f: analyse_plane(f).area)
    return next(f for f in sub_shapes(shape, "face") if analyse_cylinder(f) is not None)


def run(case: str) -> dict:
    kind, size, settings = CASES[case]
    document = Document("Bench")
    if kind == "plate":
        producer = document.add_feature(BoxFeature(
            inputs={"width": size, "depth": size, "height": 5}, outputs=["Part"]))
    else:
        producer = document.add_feature(CylinderFeature(
            inputs={"radius": size, "height": 20}, outputs=["Part"]))
    assert Rebuilder(document).rebuild().ok
    shape = document.body("Part").shape
    face = _face(shape, kind)
    inputs = {"body": BodyRef("Part"), "face": make_ref(shape, face, producer.id, body="Part"),
              "depth": 0.6, **settings}
    feature = FaceTextureFeature(inputs=inputs, outputs=["Part"])

    start = time.perf_counter()
    layout = layout_for_face(face, {**settings, "depth": 0.6})
    layout_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    result = build_preview_result(document, feature.to_dict())
    preview_s = time.perf_counter() - start
    out = {"case": case, "marks": len(layout.motifs), "layout_ms": round(layout_ms, 1),
           "preview_s": round(preview_s, 2), "error": result.get("error")}
    built = result.get("shape")
    if built is None:
        return out
    start = time.perf_counter()
    prepare_display(built)
    out["mesh_s"] = round(time.perf_counter() - start, 2)
    start = time.perf_counter()
    blob = serialise_shape(built)
    out["serialise_s"] = round(time.perf_counter() - start, 2)
    out["blob_mb"] = round(len(blob) / 1e6, 2)
    out["faces"] = len(sub_shapes(built, "face"))
    start = time.perf_counter()
    out["valid"] = is_valid(built)
    out["check_s"] = round(time.perf_counter() - start, 2)
    out["total_s"] = round(preview_s + out["mesh_s"] + out["serialise_s"], 2)
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    args = parser.parse_args()
    for case in args.case or list(CASES):
        print(json.dumps(run(case)), flush=True)


if __name__ == "__main__":
    main()
