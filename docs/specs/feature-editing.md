# Editing a feature's dimensions

## Before

The rebuild engine re-ran everything downstream of a changed feature, but only
four features had an editor:

- face textures
- fillets and chamfers
- joints
- arrangements

A box's width, a hole's depth, a pull's distance or a move's offset could only
be changed by undoing back to it.

## Now

**Edit parameters** (`ui/tools/properties.py`) is one generic panel.

| Opened from | It edits |
|---|---|
| A History row (right-click → *Edit parameters…*) | That feature |
| One selected body (context bar *Edit parameters*, or command search) | The steps that made it. A *Step* list chooses which, newest first selected |

### Which inputs become controls

`editable_inputs()` decides:

| Input | Control |
|---|---|
| A number or expression | Value field. Its dimension comes from the feature's `dimensions`; otherwise count-like names (`count`, `rows`, `sides`, …) are plain numbers and everything else is a length. An undeclared string that only parses as an angle is an angle |
| `True` / `False` | Checkbox |
| References, body names, lists, choice strings | Not shown. They belong to the feature's own tool |
| A feature with its own editor (texture, edge finish, joint, arrangement, hinge) | Routed to that editor |

### Values

- **Expressions are kept as typed.** `plate * 2` stays an expression and
  follows the parameter.
- An unchanged field keeps its original stored value.
- A changed plain number stays a number of the same type, so an `int` count
  stays an `int`.

### Preview and apply

- **Preview** runs the edited feature in the geometry process, as joints do,
  and ghosts its result.
- **Apply** is one undo step named "Edit <feature>". The feature is replaced in
  place, keeping its id, bindings and outputs, and everything that depends on
  it rebuilds.
- **A value the model cannot take** marks the feature failed, with its message.
  The body keeps its last good shape, and undo restores the old value.

## Acceptance (`tests/test_edit_parameters.py`)

- Box height 3 → 10 rebuilds a 2 mm pull on its top face: the result is 12 mm
  tall. Undo gives 5 mm again.
- `plate * 2` is stored as written and builds 8 mm tall.
- A shell thicker than the part fails with a message, and the last good shape
  stays.
- `12 bananas` is refused in the field.
- The context bar offers Edit parameters for a body.

## Known limits

- Choice inputs (hole form, thread standard, joint style) are edited in their
  own tools, not here.
- The preview shows the edited step's own result, not every downstream step.
  Apply rebuilds all of them.
