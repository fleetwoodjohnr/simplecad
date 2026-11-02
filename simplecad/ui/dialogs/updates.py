"""Settings -> Updates: see what is installed and what is out, and install it.

The network work runs on a plain thread and reports back through Qt signals,
so the window never waits on GitHub. Install is a separate, explicit click,
and anything that needs a restart asks first.
"""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout, QWidget,
)


class UpdateWorker(QObject):
    """Runs one update job off the GUI thread."""

    checked = Signal(object)        # Release or None
    downloaded = Signal(str)        # verified file path
    progress = Signal(int)
    failed = Signal(str)

    def check(self) -> None:
        threading.Thread(target=self._check, daemon=True).start()

    def _check(self) -> None:
        from ...core import updates

        try:
            self.checked.emit(updates.latest_release())
        except updates.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001 - a dead thread would leave "Checking…" forever
            self.failed.emit(f"The update check failed ({exc}).")

    def download(self, release, kind: str) -> None:
        threading.Thread(target=self._download, args=(release, kind), daemon=True).start()

    def _download(self, release, kind: str) -> None:
        import os

        from ...core import updates
        from ...core.paths import data_dir

        try:
            path = updates.download_verified(
                release, kind, os.path.join(data_dir(), "updates"),
                progress=self.progress.emit,
            )
            self.downloaded.emit(path)
        except updates.UpdateError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"The download failed ({exc}). Nothing was installed.")


class UpdatesTab(QWidget):
    def __init__(self, window, parent=None) -> None:
        from ...core import updates
        from ...core.settings import update_settings

        super().__init__(parent)
        self.window_ = window
        self.release = None
        self.kind = updates.install_kind()
        self.worker = UpdateWorker(self)
        self.worker.checked.connect(self._checked)
        self.worker.failed.connect(self._failed)
        self.worker.progress.connect(lambda value: self.bar.setValue(value))
        self.worker.downloaded.connect(self._downloaded)
        stored = update_settings()

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.current = QLabel(updates.current_version())
        self.latest = QLabel(stored["latest"] or "—")
        self.last_checked = QLabel(self._when(stored["last_checked"]))
        form.addRow("Installed", self.current)
        form.addRow("Latest", self.latest)
        form.addRow("Last checked", self.last_checked)
        layout.addLayout(form)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setPlaceholderText("Release notes appear here after a check.")
        layout.addWidget(self.notes, 1)
        self.status = QLabel(updates.notify_only_reason(self.kind))
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.hide()
        layout.addWidget(self.bar)
        buttons = QHBoxLayout()
        self.check_button = QPushButton("Check for updates")
        self.check_button.clicked.connect(self.check)
        self.install_button = QPushButton("Download and install")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install)
        buttons.addWidget(self.check_button)
        buttons.addWidget(self.install_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.auto = QCheckBox("Check for updates automatically (at most once a day)")
        self.auto.setChecked(stored["check_automatically"])
        self.auto.toggled.connect(self._auto_changed)
        layout.addWidget(self.auto)

    @staticmethod
    def _when(epoch) -> str:
        if not epoch:
            return "Never"
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))

    def _auto_changed(self, on: bool) -> None:
        from ...core.settings import set_update_setting

        set_update_setting("check_automatically", bool(on))

    def check(self) -> None:
        self.check_button.setEnabled(False)
        self.status.setText("Checking GitHub…")
        self.worker.check()

    def _checked(self, release) -> None:
        from ...core import updates
        from ...core.settings import set_update_setting

        now = time.time()
        set_update_setting("last_checked", now)
        self.last_checked.setText(self._when(now))
        self.check_button.setEnabled(True)
        self.release = release
        if release is None:
            self.latest.setText("—")
            self.status.setText("No release has been published yet.")
            return
        set_update_setting("latest", release.version)
        self.latest.setText(release.version)
        self.notes.setMarkdown(f"## SimpleCAD {release.version}\n\n{release.notes}\n\n"
                               f"[Release page]({release.page})")
        if not updates.is_newer(release.version):
            self.status.setText("You have the latest version.")
            self.install_button.setEnabled(False)
            return
        reason = updates.notify_only_reason(self.kind)
        self.install_button.setEnabled(updates.can_install(self.kind))
        self.status.setText(reason or f"SimpleCAD {release.version} is available.")

    def _failed(self, message: str) -> None:
        from ...core import updates

        self.check_button.setEnabled(True)
        # Only a newer release this copy can install is worth trying again.
        self.install_button.setEnabled(
            self.release is not None and updates.is_newer(self.release.version)
            and updates.can_install(self.kind))
        self.bar.hide()
        self.status.setText(message)

    def install(self) -> None:
        if self.release is None:
            return
        answer = QMessageBox.question(
            self, "Install update",
            f"Download SimpleCAD {self.release.version}, check its signature, and install it? "
            "Your projects and settings are not touched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.install_button.setEnabled(False)
        self.bar.setValue(0)
        self.bar.show()
        self.status.setText("Downloading and verifying…")
        self.worker.download(self.release, self.kind)

    def _downloaded(self, path: str) -> None:
        from ...core import updates

        self.bar.hide()
        try:
            outcome = updates.install(path, self.kind)
        except updates.UpdateError as exc:
            self._failed(str(exc))
            return
        if outcome == "restart":
            self.status.setText(f"SimpleCAD {self.release.version} is installed. The previous "
                                "version is kept beside it in case you need to go back.")
            if QMessageBox.question(self, "Restart", "Restart SimpleCAD now to use the new "
                                    "version? Unsaved work is offered to save first.",
                                    QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes:
                self.window_.restart_application()
        elif outcome == "quit-to-install":
            self.status.setText("The installer is open. SimpleCAD closes so it can finish.")
            self.window_.close()
        else:
            self.status.setText("The verified package was handed to your system's installer.")
