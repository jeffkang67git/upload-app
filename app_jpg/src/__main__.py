import sys
import os
os.environ.setdefault('QT_LOGGING_RULES', '*.debug=false')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import MainWindow
from PyQt5.QtWidgets import QApplication

if __name__ == '__main__':
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec_()
