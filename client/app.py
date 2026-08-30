import sys
import logging
from pathlib import Path
from PyQt5.QtWidgets import QApplication

logger = logging.getLogger(__name__)

# One cohesive stylesheet for the whole app (Session 9 / plan Q7).
_STYLE_PATH = Path(__file__).resolve().parent / "ui" / "style.qss"


def _apply_stylesheet(app: QApplication) -> None:
    """Load and apply the app-wide QSS. A missing/broken stylesheet must not
    stop the app launching — it just falls back to the native Qt look."""
    try:
        app.setStyleSheet(_STYLE_PATH.read_text(encoding="utf-8"))
        logger.info("Applied Echo stylesheet.")
    except OSError as e:
        logger.warning(f"Could not load stylesheet ({_STYLE_PATH}): {e}. Using native style.")


def main():
    """Initialize the application and start the main event loop."""

    app = QApplication(sys.argv)
    app.setApplicationName("Echo")
    _apply_stylesheet(app)

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
