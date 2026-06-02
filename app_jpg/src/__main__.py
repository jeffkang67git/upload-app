import sys
import os
import traceback

os.environ.setdefault('QT_LOGGING_RULES', '*.debug=false')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 启动错误日志
_log_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'startup_error.log')

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFontDatabase

    # 注册思源黑体（支持中文）
    _font_base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    _font_path = os.path.join(_font_base, 'fonts', 'NotoSansSC-Regular.otf')
    _font_id = QFontDatabase.addApplicationFont(_font_path)

    from src.app import MainWindow

    with open(_log_path, 'w', encoding='utf-8') as f:
        f.write(f"Font ID: {_font_id}\n")
        f.write(f"Font path: {_font_path}\n")
        f.write(f"Font base: {_font_base}\n")
        f.write("Startup OK\n")

    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec_()

except Exception:
    with open(_log_path, 'w', encoding='utf-8') as f:
        f.write(traceback.format_exc())
    sys.exit(1)
