"""Portable, reversible entity changes used by history and the worker wire."""
import copy


COLLECTIONS = {"features": "id", "bodies": "id", "groups": "id", "parameters": "name"}


def make_patch(source, target):
    patch = {"values": {}, "collections": {}}
    for key in source.keys() | target.keys():
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
    for key, change in patch.get("collections", {}).items():
        identity = COLLECTIONS[key]
        items = {v.get(identity, v.get("name")): v for v in target.get(key, [])}
        for ident in change["remove"]:
            items.pop(ident, None)
        items.update(copy.deepcopy(change["set"]))
        order = change.get("order")
        target[key] = [items[k] for k in (order if order is not None else items) if k in items]
    return target
