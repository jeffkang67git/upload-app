import sys
import os
os.environ.setdefault('QT_LOGGING_RULES', '*.debug=false')

# 禁用硬件加速，防止显卡驱动崩溃（PyInstaller 打包常见问题）
os.environ.setdefault('QT_OPENGL', 'software')

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFontDatabase

# 注册思源黑体（支持中文）
if getattr(sys, 'frozen', False):
    _base = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_font_path = os.path.join(_base, 'fonts', 'NotoSansSC-Regular.otf')
QFontDatabase.addApplicationFont(_font_path)

from src.app import MainWindow

if __name__ == '__main__':
    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec_()
