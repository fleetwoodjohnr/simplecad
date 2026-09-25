"""Mutation-aware recipe containers; callbacks never own geometry."""
import copy


def observe(value, callback):
    if isinstance(value, dict):
        return RecipeDict(value, callback)
    if isinstance(value, list):
        return RecipeList(value, callback)
    return value


class RecipeDict(dict):
    def __init__(self, values=(), callback=lambda: None):
        self.callback = callback
        super().__init__((k, observe(v, callback)) for k, v in dict(values).items())

    def __setitem__(self, key, value):
        super().__setitem__(key, observe(value, self.callback))
        self.callback()

    def __delitem__(self, key):
        super().__delitem__(key)
        self.callback()

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def pop(self, key, *default):
        result = super().pop(key, *default)
        self.callback()
        return result

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def clear(self):
        super().clear()
        self.callback()

    def popitem(self):
        result = super().popitem()
        self.callback()
        return result

    def __ior__(self, values):
        self.update(values)
        return self

    def __deepcopy__(self, memo):
        return {copy.deepcopy(k, memo): copy.deepcopy(v, memo) for k, v in self.items()}


class RecipeList(list):
    def __init__(self, values=(), callback=lambda: None):
        self.callback = callback
        super().__init__(observe(v, callback) for v in values)

    def __setitem__(self, key, value):
        super().__setitem__(key, [observe(v, self.callback) for v in value] if isinstance(key, slice)
                            else observe(value, self.callback))
        self.callback()

    def __delitem__(self, key):
        super().__delitem__(key)
        self.callback()

    def append(self, value):
        super().append(observe(value, self.callback))
        self.callback()

    def extend(self, values):
        super().extend(observe(v, self.callback) for v in values)
        self.callback()

    def insert(self, index, value):
        super().insert(index, observe(value, self.callback))
        self.callback()

    def pop(self, index=-1):
        value = super().pop(index)
        self.callback()
        return value

    def remove(self, value):
        super().remove(value)
        self.callback()

    def clear(self):
        super().clear()
        self.callback()

    def reverse(self):
        super().reverse()
        self.callback()

    def sort(self, *args, **kwargs):
        super().sort(*args, **kwargs)
        self.callback()

    def __iadd__(self, values):
        self.extend(values)
        return self

    def __imul__(self, count):
        super().__imul__(count)
        self.callback()
        return self

    def __deepcopy__(self, memo):
        return [copy.deepcopy(v, memo) for v in self]
