"""The geometry process.

Rebuilds run here, in a child process, so the window stays live while OCCT
works. A thread cannot do this: the OCP bindings hold the GIL for the whole of
every kernel call, so a Python worker starves the UI thread instead of freeing
it (measured, and documented in docs/architecture.md). A separate process has no
GIL to share.

The child is **persistent**. It keeps its own ``Document`` and ``Rebuilder``, so
the rebuild cache survives between requests and an incremental edit still only
re-runs what changed. Each request carries the document's serialised state; only
bodies whose geometry actually changed are sent back, as B-Rep bytes.

This module is deliberately Qt-free -- it is what the child runs. The parent's
side lives in ``ui/geometry_client.py``.
"""

from __future__ import annotations

import io
import os
import traceback

#: Message kinds on the wire.
REBUILD = "rebuild"
PREVIEW = "preview"
SHUTDOWN = "shutdown"
RESULT = "result"
PREVIEWED = "previewed"
FAILED = "failed"
READY = "ready"


def serialise_shape(shape) -> bytes:
    from OCP.BRepTools import BRepTools

    stream = io.BytesIO()
    BRepTools.Write_s(shape, stream)
    return stream.getvalue()


def deserialise_shape(blob: bytes):
    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, io.BytesIO(blob), BRep_Builder())
    return None if shape.IsNull() else shape


def _portable_inputs(feature) -> dict:
    """Inputs that survive the pipe, so features can report their choices back."""
    out = {}
    for key, value in feature.inputs.items():
        if isinstance(value, (int, float, bool, str)) or value is None:
            out[key] = value
    return out


def build_preview(document, feature_state: dict):
    """Build what *feature_state* would produce, without recording anything.

    A preview is a question, not an edit: the document, its bodies and the
    rebuilder's cache all have to come out the far side untouched, or dragging a
    fillet handle would quietly rewrite the model it is previewing against.

    Deliberately the feature's own ``execute`` rather than a second, parallel
    implementation. It is the same question the committed feature will answer,
    and the two drifting apart is how a preview comes to show something the
    Apply button then does not produce.

    Returns the shape, or None when it cannot be built -- which for a fillet
    radius past what the edge can carry is an ordinary answer, not a fault.
    """
    from .document import BuildContext, Feature

    feature = Feature.from_dict(feature_state)
    context = BuildContext(document)
    # A copy: whatever the feature does to its context cannot reach the child's
    # own body table.
    context.bodies = {
        name: body.shape
        for name, body in document.bodies.items()
        if body.shape is not None
    }
    try:
        outputs = feature.execute(context)
    except BaseException:  # noqa: BLE001 - OCCT raises non-Exceptions
        return None
    if not outputs:
        return None
    if len(outputs) == 1:
        return next(iter(outputs.values()), None)
    from ..kernel.occ import compound

    return compound(outputs.values())


def build_preview_result(document, feature_state: dict) -> dict:
    """Return a preview shape together with the feature's honest outcome.

    ``build_preview`` predates tools that need to distinguish "no shape" from
    "the kernel rejected this operation".  Keep that small public helper for
    callers and tests, while the process protocol carries enough information
    for a panel to disable Apply and explain why a physical thread failed.
    """
    from .document import BuildContext, Feature

    try:
        feature = Feature.from_dict(feature_state)
        context = BuildContext(document)
        context.bodies = {
            name: body.shape
            for name, body in document.bodies.items()
            if body.shape is not None
        }
        preview_parts = getattr(feature, "preview_parts", None)
        parts = preview_parts(context) if callable(preview_parts) else None
        if parts is not None:
            shape = parts.get("result")
        else:
            outputs = feature.execute(context)
            if len(outputs) == 1:
                shape = next(iter(outputs.values()), None)
            elif outputs:
                from ..kernel.occ import compound

                shape = compound(outputs.values())
            else:
                shape = None
        return {
            "shape": shape,
            "parts": parts or {},
            "error": None if shape is not None else "This preview produced no solid.",
            "warnings": list(context.warnings),
            "inputs": _portable_inputs(feature),
            "message": feature.message,
        }
    except BaseException as exc:  # noqa: BLE001 - OCCT raises non-Exceptions
        return {
            "shape": None,
            "parts": {},
            "error": str(exc) or "The geometry kernel could not build this preview.",
            "warnings": [],
            "inputs": {},
            "message": "",
        }


def _summarise(report, document) -> dict:
    """A report the parent can act on without holding kernel objects."""
    return {
        "ok": report.ok,
        "summary": report.summary(),
        "warnings": list(report.warnings),
        "duration": report.duration,
        "rebuilt": list(report.rebuilt),
        # Feature state travels back so the timeline can show what needs
        # attention -- and so do outputs, which features assign to themselves
        # during execute. Without them the parent never learns which body a
        # feature produced, and dependency lookup, deletion and reordering all
        # read that.
        "features": {
            feature.id: {
                "state": feature.state.value,
                "message": feature.message,
                "outputs": list(feature.outputs),
                # Some features record what they chose (a thread designation,
                # say) back onto their own inputs.
                "inputs": _portable_inputs(feature),
            }
            for feature in document.features
        },
        "failures": {fid: str(err) for fid, err in report.failures.items()},
    }


def serve(connection) -> None:
    """The child's main loop. Runs until told to stop, or the pipe closes."""
    from .document import Document
    from .rebuild import Rebuilder

    # Importing the kernel registers every feature type by name.
    from .. import kernel  # noqa: F401

    document: Document | None = None
    rebuilder: Rebuilder | None = None
    #: body name -> the bytes last sent, so unchanged bodies are not resent.
    sent: dict[str, bytes] = {}

    # The child is where the fragile kernel work belongs, so it is also where a
    # crash is most likely. Arm it there too, or the parent sees only "the
    # geometry process went away".
    try:
        from .diagnostics import install as install_diagnostics

        install_diagnostics("geometry")
    except Exception:  # noqa: BLE001 - diagnostics must never stop the child
        pass

    connection.send({"kind": READY, "pid": os.getpid()})

    while True:
        try:
            message = connection.recv()
        except (EOFError, KeyboardInterrupt):
            return
        kind = message.get("kind")
        if kind == SHUTDOWN:
            return
        if kind == PREVIEW:
            # Answered even when the child has no document yet: that just means
            # there is nothing to preview against, and the empty answer is the
            # honest one.
            result = {
                "shape": None,
                "parts": {},
                "error": "The geometry engine is not ready yet.",
                "warnings": [],
                "inputs": {},
                "message": "",
            }
            if document is not None:
                result = build_preview_result(
                    document, message.get("feature") or {}
                )
            blob = None
            if result["shape"] is not None:
                blob = serialise_shape(result["shape"])
            parts = {
                name: serialise_shape(shape)
                for name, shape in result.get("parts", {}).items()
                if shape is not None
            }
            try:
                connection.send({
                    "kind": PREVIEWED,
                    "token": message.get("token"),
                    "shape": blob,
                    "parts": parts,
                    "error": result["error"],
                    "warnings": result["warnings"],
                    "inputs": result["inputs"],
                    "message": result["message"],
                })
            except (OSError, ValueError):
                return
            continue

        if kind != REBUILD:
            continue

        try:
            state = message["document"]
            stale = set(message.get("stale") or ())
            force = bool(message.get("force"))

            if document is None:
                document = Document.from_dict(state)
                rebuilder = Rebuilder(document)
            else:
                # Apply the new state onto the existing document so the
                # rebuilder's cache stays valid for untouched features.
                previous = {f.id: f.to_dict() for f in document.features}
                incoming = Document.from_dict(state)
                changed = {
                    fid for fid in set(previous) | {f.id for f in incoming.features}
                    if previous.get(fid)
                    != next((f.to_dict() for f in incoming.features if f.id == fid), None)
                }
                document.title = incoming.title
                document.parameters = incoming.parameters
                document.metadata = incoming.metadata
                document.features = incoming.features
                keep = {n for f in incoming.features for n in f.outputs}
                for name in list(document.bodies):
                    if name not in keep:
                        del document.bodies[name]
                        # Deliberately left in `sent`: the reply loop below
                        # compares against it to work out what the parent still
                        # has on screen and needs told to drop.
                rebuilder.invalidate(changed | stale)

            report = rebuilder.rebuild(force=force)

            bodies: dict[str, bytes | None] = {}
            removed: list[str] = []
            for name, body in document.bodies.items():
                if body.shape is None:
                    continue
                blob = serialise_shape(body.shape)
                if sent.get(name) != blob:
                    bodies[name] = blob
                    sent[name] = blob
            removed = [name for name in sent if name not in document.bodies]
            for name in removed:
                bodies[name] = None          # tell the parent to drop it
                sent.pop(name, None)

            connection.send({
                "kind": RESULT,
                "report": _summarise(report, document),
                "bodies": bodies,
                "removed": removed,
            })
        except BaseException as exc:  # noqa: BLE001 - the child must never die
            connection.send({
                "kind": FAILED,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })


def child_main(connection) -> None:
    """Entry point for the spawned process."""
    try:
        serve(connection)
    finally:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass
