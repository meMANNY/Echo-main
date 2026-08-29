import sys
import logging
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)


def main():
    """Initialize the application and start the main event loop."""

    app = QApplication(sys.argv)
    app.setApplicationName("Echo")

    from client.core import ClientCore
    from client.adapter import CoreAdapter

    core = ClientCore()
    # The ONE Qt<->core seam. Every window is handed this adapter (never the raw
    # core), so widgets talk to the adapter and the adapter talks to the core.
    adapter = CoreAdapter(core)

    if core.first_run:
        logger.info("First run detected. Starting the setup chain...")
        from client.ui.start_window import StartWindow
        window = StartWindow(adapter)
    else:
        logger.info("Returning user detected. Launching main window...")
        from client.ui.echo_main_window import EchoMainWindow
        window = EchoMainWindow(adapter)

    window.center_on_screen()
    window.show()

    # start the event loop
    sys.exit(app.exec_())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    main()
