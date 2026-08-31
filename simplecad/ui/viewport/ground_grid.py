"""The ground plane grid.

Judging depth in an orbiting 3D view is hard without a reference surface: two
bodies at different depths project to the same place, and dragging one gives no
sense of how far it moved. A grid on Z=0 fixes both, and doubles as a build
volume cue -- the major squares are 50 mm and the sheet spans the Centauri
Carbon's 256 mm bed.

The three traps this module exists to handle, all of them OCCT-side:

* ``SetInfiniteState`` keeps the grid out of ``FitAll``'s bounding box, so
  "zoom to fit" still frames the model rather than the grid.
* ``SetZLayer(BotOSD)`` draws it behind everything, so it never z-fights with a
  body sitting flat on the plate.
* ``Deactivate`` removes it from selection, so clicking empty space stays empty
  space instead of picking a grid line.
"""

from __future__ import annotations

from ..theme import Palette, rgb

#: Half-width of the sheet, in mm. Covers the 256 mm bed with room around it.
EXTENT = 150.0
#: Fine spacing, in mm.
MINOR = 10.0
#: Emphasised spacing, in mm. Must be a whole multiple of MINOR.
MAJOR = 50.0

_MINOR_WIDTH = 1.0
_MAJOR_WIDTH = 1.6
_AXIS_WIDTH = 2.0


def _mix(first: str, second: str, amount: float) -> tuple[float, float, float]:
    """Blend two theme colours, *amount* of the way from first to second."""
    a, b = rgb(first), rgb(second)
    return tuple(x + (y - x) * amount for x, y in zip(a, b))


def _compound(edges):
    """Gather edges into one compound, so each band is a single presentation."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for edge in edges:
        builder.Add(compound, edge)
    return compound


def _line(start, end):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.gp import gp_Pnt

    return BRepBuilderAPI_MakeEdge(gp_Pnt(*start), gp_Pnt(*end)).Edge()


def _bands():
    """Build the three line sets: (minor, major, axes), each a compound.

    A coordinate belongs to exactly one band -- the axes are not also drawn as
    major lines, and major lines are not also drawn as minor ones. Overdrawing
    them would double the line weight where they coincide and make the emphasis
    read as an artefact.
    """
    minor, major, axes = [], [], []
    steps = int(round(EXTENT / MINOR))
    for step in range(-steps, steps + 1):
        offset = step * MINOR
        along_x = ((-EXTENT, offset, 0.0), (EXTENT, offset, 0.0))
        along_y = ((offset, -EXTENT, 0.0), (offset, EXTENT, 0.0))
        if step == 0:
            axes.append(("x", _line(*along_x)))
            axes.append(("y", _line(*along_y)))
        elif abs(offset) % MAJOR < 1e-9:
            major.extend((_line(*along_x), _line(*along_y)))
        else:
            minor.extend((_line(*along_x), _line(*along_y)))
    return (
        _compound(minor),
        _compound(major),
        _compound([edge for _axis, edge in axes if _axis == "x"]),
        _compound([edge for _axis, edge in axes if _axis == "y"]),
    )


class GroundGrid:
    """The grid's presentations, and the context they live in."""

    def __init__(self) -> None:
        self._objects: list = []
        self._context = None
        self._visible = True

    @property
    def visible(self) -> bool:
        return self._visible

    @property
    def presentations(self) -> list:
        """The AIS objects the grid owns.

        The viewport needs to tell scenery from model when it works out what to
        orbit around -- a grid stretching to 500 mm would otherwise drag every
        pivot toward the world origin.
        """
        return list(self._objects)

    def attach(self, context, palette: Palette) -> None:
        """Build the presentations and display them in *context*."""
        from OCP.AIS import AIS_Shape

        self._context = context
        self._objects = [AIS_Shape(shape) for shape in _bands()]
        for obj in self._objects:
            # Excluded from FitAll: framing the model must not frame the grid.
            obj.SetInfiniteState(True)
        self.apply_palette(palette)
        self.redisplay()

    def detach(self) -> None:
        """Forget the presentations and the context that owned them.

        Called when the GL context is rebuilt underneath the viewport: these
        objects carry buffers uploaded to a context that no longer exists, so
        they are dropped and built again rather than re-displayed.
        """
        self._objects = []
        self._context = None

    def redisplay(self) -> None:
        """(Re-)display the grid. Also the hook after ``context.RemoveAll``."""
        from OCP.Graphic3d import Graphic3d_ZLayerId_BotOSD

        if self._context is None:
            return
        for obj in self._objects:
            if self._visible:
                # Display mode 0 is wireframe; selection mode -1 means none, so
                # the grid is drawn but never picked.
                self._context.Display(obj, 0, -1, False)
                obj.SetZLayer(Graphic3d_ZLayerId_BotOSD)
                self._context.Deactivate(obj)
            else:
                self._context.Erase(obj, False)

    def set_visible(self, visible: bool) -> None:
        self._visible = bool(visible)
        self.redisplay()

    def apply_palette(self, palette: Palette) -> None:
        """Re-colour without rebuilding the geometry."""
        from OCP.Quantity import Quantity_Color, Quantity_TOC_sRGB

        if not self._objects:
            return
        # Major lines are the grid colour lifted toward the text colour, so the
        # emphasis holds in both themes without a second palette entry.
        colors = (
            rgb(palette.grid),
            _mix(palette.grid, palette.text_faint, 0.55),
            rgb(palette.grid_axis_x),
            rgb(palette.grid_axis_y),
        )
        widths = (_MINOR_WIDTH, _MAJOR_WIDTH, _AXIS_WIDTH, _AXIS_WIDTH)
        for obj, color, width in zip(self._objects, colors, widths):
            obj.SetColor(Quantity_Color(*color, Quantity_TOC_sRGB))
            obj.SetWidth(width)
            if self._context is not None:
                self._context.Redisplay(obj, False)
