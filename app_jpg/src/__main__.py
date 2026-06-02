import sys
import os
import traceback

os.environ.setdefault('QT_LOGGING_RULES', '*.debug=false')

# 计算 app 根目录（兼容 PyInstaller 打包和源码运行）
if getattr(sys, 'frozen', False):
    # 打包后：sys.executable 同目录的 _internal 里
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
else:
    # 源码运行：src/ 的父目录
    _app_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, _app_root)

_log_path = os.path.join(_app_root, 'startup_error.log')

try:
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QFontDatabase

    # 注册思源黑体
    _font_path = os.path.join(_app_root, 'fonts', 'NotoSansSC-Regular.otf')
    _font_id = QFontDatabase.addApplicationFont(_font_path)

    from src.app import MainWindow

    with open(_log_path, 'w', encoding='utf-8') as f:
        f.write(f"Font ID: {_font_id}\n")
        f.write(f"Font path: {_font_path}\n")
        f.write(f"App root: {_app_root}\n")
        f.write("Startup OK\n")

    app = QApplication([])
    w = MainWindow()
    w.show()
    app.exec_()

except Exception:
    try:
        with open(_log_path, 'w', encoding='utf-8') as f:
            f.write(f"App root: {_app_root}\n")
            f.write(traceback.format_exc())
    except Exception:
        pass
    sys.exit(1)
