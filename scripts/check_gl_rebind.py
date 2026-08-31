#!/usr/bin/env python
"""The 3D view must survive losing its GL context, and re-theme when told to.

The report was two complaints that turned out to be one bug: "I can add a shape
but then I click around and the options highlight and nothing actually changes",
and "dark theme turns the sidebar dark but the screen with the shape stays
white". Both are the viewport still being asked to draw through a GLX context
and an X11 window Qt had already replaced. OCCT never finds out -- it logs
``glXMakeCurrent() has failed!`` once and the picture stops moving, while the
event loop, the geometry process and every Qt widget carry on perfectly.

Three things are checked here, in the order they failed:

1. A theme switch reaches the 3D background at all. ``shot_theme.py`` only ever
   asserted that the *chrome* re-themed, which is exactly how a white viewport
   under a dark sidebar shipped.
2. The view comes back after its GL *context* is destroyed underneath it, rather
   than latching a failure for the rest of the session.
3. The view comes back after only its effective native *ancestor window* is
   replaced, the context surviving. This is the case the first round of this
   fix missed: it compared the context and nothing else, so a session whose
   window had been recreated logged ``glXMakeCurrent() has failed!`` with no
   context-lost line anywhere near it and never recovered. It is also the
   cheaper repair -- the scene stays on the GPU -- so this case additionally
   checks that the bodies were *not* thrown away and rebuilt.

Run:  .venv/bin/python scripts/check_gl_rebind.py
"""

from __future__ import annotations

import sys

from _harness import ROOT  # noqa: F401,E402 - puts the package on sys.path

REPORT: dict[str, object] = {}

#: How far the mean background has to move for a theme switch to count. The two
#: gradients are far apart (#FBFCFE/#C6D0DE against #2A2F3A/#15181E), so this is
#: only asking that something happened, not measuring a colour.
MIN_SHIFT = 40.0


def displayed_count(viewport) -> int:
    """How many model objects the interactive context is showing.

    The ViewCube and the grid are excluded: they are rebuilt by ``_init_occt``
    itself, so counting them would report success while every body was gone.
    """
    from OCP.AIS import AIS_ListOfInteractive

    if viewport.context is None:
        return 0
    displayed = AIS_ListOfInteractive()
    viewport.context.DisplayedObjects(displayed)
    scenery = {id(obj) for obj in viewport._grid.presentations}
    if viewport._view_cube is not None:
        scenery.add(id(viewport._view_cube))
    return sum(1 for obj in displayed if id(obj) not in scenery)


def background_level(viewport) -> float | None:
    """Mean brightness of the viewport's four corners, 0-255.

    Corners rather than the whole frame: the model sits in the middle and its
    own tint would drown out the background this is about. Read through OCCT's
    ``ToPixMap`` -- the same path thumbnails use -- because Qt's grab of a
    QOpenGLWidget is the thing that segfaults after a display change.
    """
    from PySide6.QtGui import QImage

    data = viewport.capture_png(320, 200)
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(data, "PNG"):
        return None
    inset = 6
    corners = [
        (inset, inset),
        (image.width() - 1 - inset, inset),
        (inset, image.height() - 1 - inset),
        (image.width() - 1 - inset, image.height() - 1 - inset),
    ]
    total = 0.0
    for x, y in corners:
        color = image.pixelColor(x, y)
        total += (color.red() + color.green() + color.blue()) / 3.0
    return total / len(corners)


def main() -> int:
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtGui import QSurfaceFormat
    from PySide6.QtWidgets import QApplication, QWidget

    from simplecad.kernel.primitives import BoxFeature
    from simplecad.ui.main_window import MainWindow
    from simplecad.ui.theme import Mode
    from simplecad.ui.viewport.occt_view import default_surface_format

    QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QApplication.instance() or QApplication(sys.argv[:1])

    window = MainWindow(Mode.LIGHT)
    window.setGeometry(40, 40, 1100, 720)
    viewport = window.stage.viewport
    rebinds = {"n": 0}

    def count_rebind() -> None:
        rebinds["n"] += 1

    viewport.rebound.connect(count_rebind)

    def settle(ms: int = 400) -> None:
        """Let Qt deliver whatever it has, including a paint."""
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.start(ms)
        while deadline.isActive():
            app.processEvents()
        viewport.repaint()
        app.processEvents()

    def run() -> None:
        window.add_feature(
            BoxFeature(inputs={"width": 60, "depth": 40, "height": 20},
                       outputs=["Base"])
        )
        window.wait_for_rebuild()
        viewport.fit_all()
        settle(700)

        # 1. Light -> dark has to reach the 3D background.
        light = background_level(viewport)
        window.toggle_theme()
        settle()
        dark = background_level(viewport)
        REPORT["light_background"] = None if light is None else round(light, 1)
        REPORT["dark_background"] = None if dark is None else round(dark, 1)
        REPORT["theme_reaches_viewport"] = (
            light is not None and dark is not None and light - dark >= MIN_SHIFT
        )

        # 2. Destroy the context underneath OCCT. Reparenting is what actually
        # does it: it is also the real trigger, since the translucent overlays
        # stacked on the stage are what make Qt hand this widget a new native
        # window in the first place.
        before = viewport._bound_winid
        context = viewport.context
        stage = window.stage
        viewport.setParent(None)
        app.processEvents()
        viewport.setParent(stage)
        viewport.setGeometry(stage.rect())
        viewport.lower()
        viewport.show()
        settle(700)
        REPORT["ancestor_handle_after_context_loss"] = viewport._bound_winid
        REPORT["rebinds"] = rebinds["n"]
        REPORT["survived_context_loss"] = viewport.is_ready
        REPORT["failure"] = viewport.failure
        # A rebuild means a new AIS context, so what has to be checked is that
        # the model came back into it -- the window re-displays on ``rebound``.
        # Asserting the old context object survived would only be asserting that
        # the fix that does not work is still in place.
        REPORT["context_is_new"] = viewport.context is not context
        REPORT["bodies_redisplayed"] = displayed_count(viewport) > 0
        REPORT["renders_after_loss"] = background_level(viewport) is not None
        REPORT["winid_before"] = before

        # 3. Now replace only the effective native ancestor handle, keeping the
        # context. A second top-level supplies a genuine, compatible X11 Window
        # without making the viewport itself native. Injecting that handle is a
        # deterministic version of a compositor replacing the top-level
        # surface, and exercises the cheap SetNativeHandle/SetWindow path.
        rebuilds_before = rebinds["n"]
        winid_before_reseat = viewport._bound_winid
        bodies_before = displayed_count(viewport)
        context_before_reseat = viewport.context
        donor = QWidget()
        donor.setAttribute(Qt.WA_DontShowOnScreen)
        donor.create()
        donor_handle = int(donor.winId())
        viewport._native_window_handle = lambda: donor_handle
        settle(700)
        REPORT["window_replaced"] = viewport._bound_winid != winid_before_reseat
        REPORT["viewport_stays_non_native"] = not viewport.testAttribute(
            Qt.WA_NativeWindow
        )
        REPORT["survived_window_loss"] = viewport.is_ready and viewport.failure is None
        REPORT["renders_after_window_loss"] = background_level(viewport) is not None
        # A surviving context keeps every presentation, so a re-seat must not
        # have gone the expensive way round and re-displayed everything.
        REPORT["kept_bodies"] = displayed_count(viewport) >= bodies_before
        REPORT["reused_scene"] = rebinds["n"] == rebuilds_before
        REPORT["kept_context"] = viewport.context is context_before_reseat

        # 4. And the theme still works afterwards.
        window.toggle_theme()          # dark -> light
        settle()
        back_to_light = background_level(viewport)
        REPORT["light_again"] = None if back_to_light is None else round(back_to_light, 1)
        REPORT["theme_works_after_loss"] = (
            back_to_light is not None
            and dark is not None
            and back_to_light - dark >= MIN_SHIFT
        )
        app.quit()

    viewport.ready.connect(lambda: QTimer.singleShot(200, run))
    window.show()
    app.exec()

    print("--- gl rebind ---")
    for key, value in REPORT.items():
        print(f"  {key}: {value}")
    ok = all(
        REPORT.get(key)
        for key in (
            "theme_reaches_viewport",
            "rebinds",
            "survived_context_loss",
            "context_is_new",
            "bodies_redisplayed",
            "window_replaced",
            "viewport_stays_non_native",
            "survived_window_loss",
            "renders_after_window_loss",
            "kept_bodies",
            "kept_context",
            "reused_scene",
            "renders_after_loss",
            "theme_works_after_loss",
        )
    )
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
