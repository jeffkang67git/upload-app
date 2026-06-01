"""
DHCPE批量体检报告上传 - PyQt5 应用
入口文件
"""
import sys
import os

# macOS Qt plugin fix
if sys.platform == 'darwin':
    venv_site = [p for p in sys.path if 'site-packages' in p]
    if venv_site:
        plugin_path = os.path.join(venv_site[0], 'PyQt5', 'Qt5', 'plugins', 'platforms')
        if os.path.exists(plugin_path):
            os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = plugin_path

from PyQt5.QtWidgets import QApplication
from src.app import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("SZU体检报告上传")
    app.setOrganizationName("JeffreyKang")

    w = MainWindow()
    w.show()

    sys.exit(app.exec_())