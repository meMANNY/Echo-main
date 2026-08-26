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

    def closeEvent(self, event):
        """
        clean shutdown hook:
        """

        logger.info("Shutting down application...")
        if self.peer_listener:
            try:
                self.peer_listener.stop()
            except Exception as e:
                logger.error(f"Error stopping peer listener: {e}")

        if hasattr(self.core, "server") and self.core.server:
            try:
                self.core.server.stop()
            except Exception as e:
                logger.error(f"Error stopping server: {e}")

        event.accept()  # Accept the close event to proceed with closing the window 


def main():
    """
    Initialize the application and start the main event loop.
    """

    app = QApplication(sys.argv)
    app.setApplicationName("Echo")

    from client.core import ClientCore
    core = ClientCore()

    if core.first_run:
        logger.info("First run detected. Performing initial setup...")
        from client.ui.start_window import StartWindow
        main_window = StartWindow(core)
    else:
        logger.info("Returning user detected. Launching main application window...")
        from client.ui.main_window import EchoMainWindow
        main_window = EchoMainWindow(core)

    main_window.center_on_screen()
    main_window.show()

    #start the event loop
    sys.exit(app.exec_())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()