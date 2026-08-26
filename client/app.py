import sys
import logging
from PyQt5.QtWidgets import QApplication, QMainWindow, QDesktopWidget

logger = logging.getLogger(__name__)


class MainWindowShell(QMainWindow):

    """
    A thin top-level shell that manages screen centering and guarantees clean 
    resource teardown on application exit."""

    def __init__(self, core):
        super().__init__()
        self.core = core
        self.peer_listener = None

    def center_on_screen(self):
        """
        Center the window on the active screen.
        """

        frame_geometry = self.frameGeometry()
        center_point = QDesktopWidget().availableGeometry().center()
        frame_geometry.moveCenter(center_point)
        self.move(frame_geometry.topLeft())