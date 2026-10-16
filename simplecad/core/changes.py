"""Portable, reversible entity changes used by history and the worker wire."""
import copy


COLLECTIONS = {"features": "id", "bodies": "id", "groups": "id", "parameters": "name"}
#: Flat maps patched key by key. A project with thousands of named objects has
#: thousands of counters; copying all of them into every undo step was most of
#: each step's size.
KEYED = {"counters"}
#: Lists that only ever mean a set. The retired-ID list only grows, so copying
#: all of it into every delete's undo step and every wire patch made both grow
#: with the length of the session. Patched as additions and removals instead.
SETS = {"retired_ids"}


def make_patch(source, target):
    patch = {"values": {}, "collections": {}}
    for key in source.keys() | target.keys():
        if key in SETS and isinstance(source.get(key), list) and isinstance(target.get(key), list):
            old, new = set(source[key]), set(target[key])
            if old != new:
                patch.setdefault("sets", {})[key] = {"add": sorted(new - old),
                                                     "remove": sorted(old - new)}
            continue
        if key in KEYED and isinstance(source.get(key), dict) and isinstance(target.get(key), dict):
            old, new = source[key], target[key]
            changed = {k: copy.deepcopy(v) for k, v in new.items() if k not in old or old[k] != v}
            removed = [k for k in old if k not in new]
            if changed or removed:
                patch.setdefault("keyed", {})[key] = {"set": changed, "remove": removed}
            continue
        if key not in COLLECTIONS:
            if source.get(key) != target.get(key):
                patch["values"][key] = copy.deepcopy(target.get(key))
            continue
        identity = COLLECTIONS[key]
        old = {v.get(identity, v.get("name")): v for v in source.get(key, [])}
        new = {v.get(identity, v.get("name")): v for v in target.get(key, [])}
        changed = {k: copy.deepcopy(v) for k, v in new.items() if old.get(k) != v}
        removed = list(old.keys() - new.keys())
        order = list(new) if list(old) != list(new) else None
        if changed or removed or order is not None:
            patch["collections"][key] = {"set": changed, "remove": removed, "order": order}
    return patch


def apply_patch(source, patch):
    target = copy.deepcopy(source)
    target.update(copy.deepcopy(patch.get("values", {})))
    _apply_keyed(target, patch, copy.deepcopy)
    _apply_sets(target, patch)
    for key, change in patch.get("collections", {}).items():
        identity = COLLECTIONS[key]
        items = {v.get(identity, v.get("name")): v for v in target.get(key, [])}
        for ident in change["remove"]:
            items.pop(ident, None)
        items.update(copy.deepcopy(change["set"]))
        order = change.get("order")
        target[key] = [items[k] for k in (order if order is not None else items) if k in items]
    return target


def apply_patch_shared(source, patch):
    """Like :func:`apply_patch`, but shares unchanged entities with *source*.

    For holders that treat both *source* and *patch* as immutable -- the
    geometry worker's last-applied wire state, a freshly unpickled patch. A
    deep copy of a whole project per edit is exactly the cost this avoids.
    """
    target = dict(source)
    target.update(patch.get("values", {}))
    _apply_keyed(target, patch, lambda value: value)
    _apply_sets(target, patch)
    for key, change in patch.get("collections", {}).items():
        identity = COLLECTIONS[key]
        items = {v.get(identity, v.get("name")): v for v in target.get(key, [])}
        for ident in change["remove"]:
            items.pop(ident, None)
        items.update(change["set"])
        order = change.get("order")
        target[key] = [items[k] for k in (order if order is not None else items) if k in items]
    return target


def _apply_sets(target, patch):
    for key, change in patch.get("sets", {}).items():
        values = set(target.get(key) or ()) - set(change.get("remove", ()))
        target[key] = sorted(values | set(change.get("add", ())))


def _apply_keyed(target, patch, clone):
    for key, change in patch.get("keyed", {}).items():
        mapping = dict(target.get(key) or {})
        for item in change.get("remove", []):
            mapping.pop(item, None)
        mapping.update(clone(change.get("set", {})))
        target[key] = mapping
