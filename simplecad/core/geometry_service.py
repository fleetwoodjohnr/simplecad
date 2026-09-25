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
import time
import traceback

#: Message kinds on the wire.
SAVE = "save"
SAVED = "saved"
REBUILD = "rebuild"
SYNC = "sync"
PREVIEW = "preview"
SHUTDOWN = "shutdown"
RESULT = "result"
SYNCED = "synced"
PREVIEWED = "previewed"
FAILED = "failed"
READY = "ready"
PROGRESS = "progress"

#: How often the child may say which feature it is on, in seconds. A rebuild
#: that says nothing for two minutes is indistinguishable from a dead one, and
#: the parent used to kill it on exactly that evidence -- which turned a slow
#: model into a restart loop and then an in-process rebuild that froze the
#: window. See :meth:`simplecad.ui.geometry_client.GeometryClient._overdue`.
PROGRESS_INTERVAL = 0.2


def prepare_display(shape) -> None:
    """Mesh in the isolated worker; BRepTools carries triangles to the UI."""
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from ..kernel.tessellate import DEFAULT_ANGLE, DEFAULT_DEFLECTION

    BRepMesh_IncrementalMesh(shape, DEFAULT_DEFLECTION, False, DEFAULT_ANGLE, True)


def preview_signature(state: dict, edit_id=None) -> dict:
    return {**{key: state.get(key) for key in ("type", "inputs", "suppressed", "dropped")},
            "edit_id": edit_id or state.get("__edit_feature_id")}


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
    from .document import BuildContext, Document, Feature

    try:
        if "__inspect_edge" in feature_state:
            from .edge_edit import inspect_edge_finish
            request = feature_state["__inspect_edge"]
            inspection = inspect_edge_finish(document, request["body"], request["pick"])
            return {"shape": None, "parts": {}, "error": None,
                    "warnings": [], "inputs": {}, "message": "",
                    "inspection": inspection}
        edit_id = feature_state.get("__edit_feature_id")
        from .rebuild import Rebuilder
        feature = Feature.from_dict(feature_state)
        builder = document._rebuilder or Rebuilder(document)
        context = builder.context_for(feature, edit_id=edit_id)
        if feature_state.get("__remove_edge_finish"):
            return {"shape": context.shape(feature, "body"), "parts": {}, "error": None,
                    "warnings": [], "inputs": {}, "message": ""}
        base_face = None
        if edit_id and feature.type_name == "face_texture":
            base_face = context.resolve(feature, "face")
            if feature_state.get("__base_face_only"):
                return {
                    "shape": None, "parts": {"base_face": base_face},
                    "error": None, "warnings": [], "inputs": {}, "message": "",
                }
        preview_parts = getattr(feature, "preview_parts", None)
        parts = preview_parts(context) if callable(preview_parts) else None
        outputs = {}
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
        if base_face is not None:
            parts = dict(parts or {})
            parts["base_face"] = base_face
        return {
            "shape": shape,
            "parts": parts or {},
            "error": None if shape is not None else "This preview produced no solid.",
            "warnings": list(context.warnings),
            "inputs": _portable_inputs(feature),
            "message": feature.message,
            "_outputs": outputs,
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
        "signatures": dict(getattr(document, "_geometry_signatures", {})),
        "summary": report.summary(),
        "warnings": list(report.warnings),
        "duration": report.duration,
        "rebuilt": list(report.rebuilt),
        "sealed": list(report.sealed),
        "visited": report.visited, "cache_hits": report.cache_hits,
        "body_metadata": {name: body.to_dict() for name, body in document.bodies.items()},
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
                "output_ids": dict(feature.output_ids),
                "bindings": {n: r.to_dict() for n, r in feature.bindings.items()},
                # Some features record what they chose (a thread designation,
                # say) back onto their own inputs.
                "inputs": _portable_inputs(feature),
                "solved_sketch": feature.to_dict()["inputs"].get("sketch") if feature.type_name == "sketch" else None,
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
    sent_shapes: dict[str, object] = {}
    preview_cache = None
    revision = 0
    generation = 0
    sent_cache = set()
    sent_entries = set()

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
        if kind == SYNC:
            # A reopened project already has B-Rep bodies in the window. Seed
            # the worker with those exact shapes so previews can run without
            # re-executing its entire feature history just to open the file.
            try:
                incoming = Document.from_dict(message["document"])
                blobs = message.get("bodies") or {}
                if set(blobs) != set(incoming.bodies):
                    raise ValueError("The cached project geometry is incomplete.")
                for name, blob in blobs.items():
                    shape = deserialise_shape(blob)
                    if shape is None:
                        raise ValueError(f"The cached geometry for {name} is invalid.")
                    incoming.bodies[name].shape = shape
                document = incoming
                document._all_assets.update(message.get("assets", {}))
                document.assets = {sha: document._all_assets[sha]
                                   for sha in message["document"].get("assets", [])
                                   if sha in document._all_assets}
                document._geometry_fragments = message.get("fragments", [])
                rebuilder = Rebuilder(document)
                rebuilder.store.merge(message.get("cache"))
                sent_cache = set(rebuilder.store.blobs)
                sent_entries = set(rebuilder.store.entries)
                # The shapes just adopted *are* the result of this history, so
                # the next edit builds only itself instead of replaying every
                # feature behind it.
                sealed = rebuilder.adopt_baseline() if not message.get("cache") else True
                from .result_store import signatures
                document._geometry_signatures = signatures(document)
                rebuilder.store.adopt_display(document)
                sent = dict(blobs)
                sent_shapes = {name: body.shape for name, body in document.bodies.items()}
                revision = message.get("revision", 0)
                generation = message.get("generation", generation)
                preview_cache = None
                connection.send({
                    "kind": SYNCED, "revision": message.get("revision", 0),
                    "sealed": len(document.features) if sealed else 0,
                })
            except BaseException as exc:  # noqa: BLE001 - report and recover in worker
                connection.send({
                    "kind": FAILED, "revision": message.get("revision", 0),
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                })
            continue
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
            if document is not None and message.get("revision", revision) == revision:
                result = build_preview_result(
                    document, message.get("feature") or {}
                )
                if result.get("_outputs") and (message.get("feature") or {}).get("type") in {"boolean", "face_texture"}:
                    preview_cache = (
                        document.to_dict(), preview_signature(message["feature"]), result,
                    )
            blob = None
            if result["shape"] is not None:
                prepare_display(result["shape"])
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
                    "revision": message.get("revision", revision),
                    "generation": message.get("generation", 0),
                    "shape": blob,
                    "parts": parts,
                    "error": result["error"],
                    "warnings": result["warnings"],
                    "inputs": result["inputs"],
                    "message": result["message"],
                    "inspection": result.get("inspection"),
                })
            except (OSError, ValueError):
                return
            continue

        if kind == SAVE:
            try:
                from .project import save
                snapshot = Document.from_dict(message["document"])
                snapshot._all_assets.update(message.get("assets", {}))
                if document is not None:
                    snapshot._all_assets.update(document._all_assets)
                snapshot.assets = {sha: snapshot._all_assets[sha]
                                   for sha in message["document"].get("assets", [])
                                   if sha in snapshot._all_assets}
                snapshot._history_data = message.get("history", {})
                snapshot._retained_cache_keys = {key for stack in ("undo", "redo")
                    for action in snapshot._history_data.get(stack, []) for key in action.get("cache_keys", [])}
                if rebuilder is not None:
                    snapshot._geometry_store = rebuilder.store
                    # Saving an older immutable snapshot must not evict the
                    # current editing session's results from the shared store.
                    snapshot._retained_cache_keys.update(rebuilder.store.entries)
                    previous = {b.id: b for b in document.bodies.values()}
                    for body in snapshot.bodies.values():
                        if body.id in previous:
                            body.shape = previous[body.id].shape
                else:
                    from .result_store import ResultStore
                    snapshot._geometry_store = ResultStore()
                snapshot._geometry_store.merge(message.get("cache"))
                for name, blob in message.get("bodies", {}).items():
                    if name in snapshot.bodies and blob:
                        snapshot.bodies[name].shape = deserialise_shape(blob)
                evaluator = Rebuilder(snapshot)
                evaluator.rebuild(on_feature=lambda f, i, n: connection.send({
                    "kind": PROGRESS, "revision": message.get("revision", 0),
                    "feature": f.name, "index": i, "total": n,
                }))
                path = save(snapshot, message["path"], message.get("thumbnail"))
                connection.send({"kind": SAVED, "token": message.get("token"), "path": path,
                                 "generation": message.get("generation"), "error": None})
            except BaseException as exc:
                connection.send({"kind": SAVED, "token": message.get("token"),
                                 "generation": message.get("generation"), "error": str(exc)})
            continue

        if kind != REBUILD:
            continue

        try:
            if "patch" in message:
                from .changes import apply_patch
                if document is None or message.get("base_revision") != revision:
                    raise ValueError("The geometry update refers to an obsolete document revision.")
                state = apply_patch(document.to_dict(), message["patch"])
            else:
                state = message["document"]
            stale = set(message.get("stale") or ())
            force = bool(message.get("force"))
            full = bool(message.get("full"))

            incoming_generation = message.get("generation", generation)
            if incoming_generation != generation:
                document = None
                preview_cache = None
                generation = incoming_generation
                sent_cache.clear()
                sent_entries.clear()
                sent.clear()
                sent_shapes.clear()

            if document is None:
                document = Document.from_dict(state)
                document._all_assets.update(message.get("assets", {}))
                document.assets = {sha: document._all_assets[sha]
                                   for sha in state.get("assets", []) if sha in document._all_assets}
                rebuilder = Rebuilder(document)
                rebuilder.store.merge(message.get("cache"))
            else:
                # Apply the new state onto the existing document so the
                # rebuilder's cache stays valid for untouched features.
                previous = {f.id: f.to_dict() for f in document.features}
                incoming = Document.from_dict(state)
                incoming_states = {f.id: f.to_dict() for f in incoming.features}
                changed = {
                    fid for fid in set(previous) | set(incoming_states)
                    if previous.get(fid)
                    != incoming_states.get(fid)
                }
                old_parameters = {p["name"]: p for p in document.parameters.to_list()}
                new_parameters = {p["name"]: p for p in incoming.parameters.to_list()}
                for parameter in old_parameters.keys() | new_parameters.keys():
                    if old_parameters.get(parameter) != new_parameters.get(parameter):
                        changed.update(incoming.features_using_parameter(parameter))
                changed = document.dependents_of(changed | stale) | incoming.dependents_of(changed | stale)
                document.title = incoming.title
                document.parameters = incoming.parameters
                document.metadata = incoming.metadata
                existing = {f.id: f for f in document.features}
                document.features = [existing[f.id] if previous.get(f.id) == incoming_states.get(f.id) else f
                                     for f in incoming.features]
                document.groups = incoming.groups
                document._counters = incoming._counters
                document._retired_ids.update(incoming._retired_ids)
                document._all_assets.update(message.get("assets", {}))
                document.assets = {sha: document._all_assets[sha]
                                   for sha in state.get("assets", []) if sha in document._all_assets}
                keep = {n for f in incoming.features for n in f.outputs}
                for name in list(document.bodies):
                    if name not in keep:
                        del document.bodies[name]
                        # Deliberately left in `sent`: the reply loop below
                        # compares against it to work out what the parent still
                        # has on screen and needs told to drop.
                rebuilder.invalidate(changed | stale)

            document._retained_cache_keys = set(message.get("retained_cache_keys", []))
            fragments = {
                item["features"][-1]["id"]: item
                for item in getattr(document, "_geometry_fragments", [])
            }
            fragments.update({item["features"][-1]["id"]: item for item in message.get("fragments", [])})
            document._geometry_fragments = list(fragments.values())
            reused_preview = None
            if preview_cache is not None and not force:
                old, signature, preview_result = preview_cache
                incoming_features = state["features"]
                edit_id = signature.get("edit_id")
                feature = document.feature(edit_id) if edit_id else (document.features[-1] if document.features else None)
                old_features = {f["id"]: f for f in old["features"]}
                candidate_id = feature.id if feature is not None else None
                unaffected = [f for f in incoming_features if f["id"] != candidate_id]
                old_unaffected = [f for f in old["features"] if f["id"] != candidate_id]
                if (feature is not None and unaffected == old_unaffected
                        and state["parameters"] == old["parameters"]
                        and preview_signature(feature.to_dict(), edit_id) == signature):
                    rebuilder.seed_result(feature, preview_result["_outputs"], preview_result["message"])
                    reused_preview = preview_result
            preview_cache = None

            last_progress = 0.0

            def report_progress(feature, index: int, total: int) -> None:
                nonlocal last_progress
                now = time.monotonic()
                if index and now - last_progress < PROGRESS_INTERVAL:
                    return
                last_progress = now
                try:
                    connection.send({
                        "kind": PROGRESS,
                        "revision": message.get("revision", 0),
                        "id": feature.id,
                        "feature": feature.name or feature.label,
                        "index": index,
                        "total": total,
                    })
                except (OSError, ValueError):
                    pass

            report = rebuilder.rebuild(force=force, on_feature=report_progress)
            if reused_preview is not None:
                report.warnings.extend(reused_preview["warnings"])
            revision = message.get("revision", 0)

            bodies: dict[str, bytes | None] = {}
            removed: list[str] = []
            # A skipped older result may have contained a deletion that the
            # parent never saw. A full reply must report those removals too.
            previously_sent = set(sent)
            if full:
                previously_sent.update(
                    entry["name"] for entry in state.get("bodies", [])
                )
            for name, body in document.bodies.items():
                if body.shape is None:
                    continue
                if sent_shapes.get(name) is body.shape and name in sent:
                    blob = sent[name]
                else:
                    prepare_display(body.shape)
                    blob = serialise_shape(body.shape)
                if full or sent.get(name) != blob:
                    bodies[name] = blob
                    sent[name] = blob
                sent_shapes[name] = body.shape
            removed = [name for name in previously_sent if name not in document.bodies]
            for name in removed:
                bodies[name] = None          # tell the parent to drop it
                sent.pop(name, None)
                sent_shapes.pop(name, None)

            connection.send({
                "kind": RESULT,
                "revision": message.get("revision", 0),
                "report": _summarise(report, document),
                "bodies": bodies,
                "removed": removed,
                "preview_reused": reused_preview is not None,
                "generation": generation,
                "cache": rebuilder.store.export(() if full else sent_cache, () if full else sent_entries),
            })
            sent_cache.update(rebuilder.store.blobs)
            sent_entries.update(rebuilder.store.entries)
            sent_cache.intersection_update(rebuilder.store.blobs)
            sent_entries.intersection_update(rebuilder.store.entries)
        except BaseException as exc:  # noqa: BLE001 - the child must never die
            connection.send({
                "kind": FAILED,
                "revision": message.get("revision", 0),
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
