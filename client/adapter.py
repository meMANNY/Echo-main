import logging
from PyQt5.QtCore import QObject, pyqtSignal,QRunnable, QThreadPool,pyqtSlot


logger = logging.getLogger(__name__)

class WorkerSignals(QObject):
    """Signals for asynchronous bg tasks."""
    result = pyqtSignal(object)  # Signal to emit the result of the task
    error = pyqtSignal(str)  # Signal to emit an error (exception type, value, traceback)

class Worker(QRunnable):
    """Generic runnable worker to execute blocking network calls off the GUI thread."""

    def __init__(self,fn,*args,**kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    @pyqtSlot() #required when connecting signals across threads to avoid seg faults
    def run(self):
        """Run the worker function with the provided arguments."""
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.signals.result.emit(result)  # Emit the result signal
        except Exception as e:
            logger.error(f"Error in worker thread: {e}")
            self.signals.error.emit(str(e))
