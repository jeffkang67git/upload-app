#!/usr/bin/env python3
"""Minimal debug: build UI step by step and check sizes."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'

from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLabel
from PyQt5.QtCore import Qt

app = QApplication([])
app.setStyle('Fusion')

# Simulate the layout structure
central = QWidget()
root = QVBoxLayout(central)
root.setContentsMargins(0, 0, 0, 0)
root.setSpacing(0)

body = QWidget()
body.setMinimumHeight(500)
body.setStyleSheet("background: #F5F5F7;")
bl = QVBoxLayout(body)
bl.setContentsMargins(16, 12, 16, 12)

top = QWidget()
top.setStyleSheet("background: transparent;")
tl = QHBoxLayout(top)
tl.setContentsMargins(0, 0, 0, 0)
tl.setSpacing(12)

left = QWidget()
left.setFixedWidth(190)
left.setStyleSheet("background: white; border-radius: 12px;")
ll = QVBoxLayout(left)
ll.setContentsMargins(0, 8, 0, 8)

lt = QLabel("患者列表")
lt.setStyleSheet("font-size: 11px; font-weight: 600; color: #86868B; padding: 8px 16px 6px;")
ll.addWidget(lt)

patient_list = QListWidget()
patient_list.setStyleSheet("""
    QListWidget { background: transparent; border: none; outline: none; }
    QListWidget::item { padding: 0; }
""")
ll.addWidget(patient_list)

tl.addWidget(left)

right = QWidget()
right.setStyleSheet("background: transparent;")
rl = QVBoxLayout(right)
rl.setContentsMargins(0, 0, 0, 0)
rl.setSpacing(8)

cards_wrap = QWidget()
cards_wrap.setStyleSheet("background: white; border-radius: 12px; padding: 12px;")
cards_wrap.setMinimumHeight(400)
cw = QVBoxLayout(cards_wrap)
cw.setContentsMargins(10, 10, 10, 10)
cw.setSpacing(8)

from PyQt5.QtWidgets import QGridLayout, QFrame, QHBoxLayout as QHB, QVBoxLayout as QVB, QPushButton, QLabel, QProgressBar
from PyQt5.QtCore import QTimer

cards_grid = QGridLayout()
cards_grid.setSpacing(8)
cw.addLayout(cards_grid)

patient_hdr = QLabel("")
patient_hdr.setStyleSheet("font-size: 14px; font-weight: 600; color: #1D1D1F; padding: 4px 4px 8px; background: transparent;")
rl.addWidget(patient_hdr)
rl.addWidget(cards_wrap, 1)

tl.addWidget(right, 1)
bl.addWidget(top, 1)
root.addWidget(body)

btm = QWidget()
btm.setFixedHeight(50)
btm.setStyleSheet("background: white; border-top: 1px solid #F0F0F0;")
btml = QHBoxLayout(btm)
btml.setContentsMargins(16, 10, 16, 10)
btml.setSpacing(12)

btn_settings = QPushButton("设置")
btn_settings.setStyleSheet("padding: 8px 16px; border: 1px solid #D1D1D6; border-radius: 8px; background: white; color: #333; font-size: 12px;")
btml.addWidget(btn_settings)

lbl_last = QLabel("")
lbl_last.setStyleSheet("font-size: 11px; color: #86868B;")
btml.addWidget(lbl_last)

# pagination nav
page_nav = QWidget()
page_nav.setStyleSheet("background: transparent;")
pnl = QHBoxLayout(page_nav)
pnl.setContentsMargins(0, 0, 0, 0)
pnl.setSpacing(4)
btn_prev = QPushButton("‹")
btn_prev.setFixedWidth(28)
btn_prev.setStyleSheet("border: 1px solid #D1D1D6; border-radius: 4px; font-size: 16px; color: #333; padding: 2px 6px; background: white;")
pnl.addWidget(btn_prev)
lbl_page = QLabel("1 / 3")
lbl_page.setStyleSheet("font-size: 12px; color: #86868B; min-width: 60px; text-align: center;")
lbl_page.setAlignment(Qt.AlignCenter)
pnl.addWidget(lbl_page)
btn_next = QPushButton("›")
btn_next.setFixedWidth(28)
btn_next.setStyleSheet("border: 1px solid #D1D1D6; border-radius: 4px; font-size: 16px; color: #333; padding: 2px 6px; background: white;")
pnl.addWidget(btn_next)
btml.addWidget(page_nav)
btml.addStretch()

btn_upload = QPushButton("上传到东华 + 合并PDF")
btn_upload.setStyleSheet("background: #0066CC; color: white; border: none; border-radius: 8px; padding: 9px 20px; font-size: 13px; font-weight: 600;")
btml.addWidget(btn_upload)

root.addWidget(btm)

ver = QLabel("V1.0 - Proudly developed by Jeffrey Kang")
ver.setStyleSheet("background: #FAFAFA; text-align: center; font-size: 10px; color: #C7C7CC; padding: 4px;")
ver.setAlignment(Qt.AlignCenter)
root.addWidget(ver)

# Add test patient items
class FakePatient:
    def __init__(self, mrn):
        self.mrn = mrn
        self.reports = []
        self.aggregate_status = 'pending'

for i in range(3):
    patient_list.addItem(f"Test {i}")

# Create a main window to test
from PyQt5.QtWidgets import QMainWindow, QListWidgetItem

class TestWin(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Test")
        self.setMinimumWidth(900)
        self.setMinimumHeight(700)
        self.setCentralWidget(central)
        
        # Add items
        for i in range(3):
            item = QListWidgetItem(f"MRN-2026-{1000+i}")
            item.setData(Qt.UserRole, i)
            patient_list.addItem(item)

win = TestWin()
win.show()
win.resize(900, 700)

app.processEvents()

# Check sizes
print(f"central geometry: {central.geometry()}")
print(f"body geometry: {body.geometry()}")
print(f"top geometry: {top.geometry()}")
print(f"left geometry: {left.geometry()}")
print(f"patient_list geometry: {patient_list.geometry()}")
print(f"right geometry: {right.geometry()}")
print(f"cards_wrap geometry: {cards_wrap.geometry()}")
print(f"btm geometry: {btm.geometry()}")
print(f"ver geometry: {ver.geometry()}")
print(f"window geometry: {win.geometry()}")

print(f"\npatient_list.count: {patient_list.count()}")
for i in range(patient_list.count()):
    item = patient_list.item(i)
    print(f"  item {i}: text='{item.text()}' data={item.data(Qt.UserRole)}")

# Add card widget
for i in range(4):
    card = QFrame()
    card.setFixedHeight(88)
    card.setStyleSheet("background: white; border: 1px solid #E8E8ED; border-radius: 10px;")
    cl = QHBoxLayout(card)
    cl.setContentsMargins(10, 8, 10, 8)
    cl.setSpacing(8)
    
    thumb = QLabel("📄")
    thumb.setFixedSize(40, 54)
    thumb.setStyleSheet("background: #F5F5F7; border: 1px solid #E0E0E4; border-radius: 5px; font-size: 20px;")
    thumb.setAlignment(Qt.AlignCenter)
    cl.addWidget(thumb)
    
    info = QVBoxLayout()
    info.setSpacing(1)
    dv = QLabel(f"人体成分分析4楼 - {i}")
    dv.setStyleSheet("font-size: 13px; font-weight: 600; color: #1D1D1F;")
    info.addWidget(dv)
    ol = QLabel(f"医嘱号: ORD-{i}")
    ol.setStyleSheet("font-size: 11px; color: #555;")
    info.addWidget(ol)
    info.addStretch()
    cl.addLayout(info, 1)
    
    right_actions = QVBoxLayout()
    right_actions.setSpacing(3)
    right_actions.setContentsMargins(0, 2, 0, 2)
    btn = QPushButton("预览")
    btn.setStyleSheet("padding: 4px 12px; border-radius: 6px; border: 1px solid #0066CC; color: #0066CC; background: white; font-size: 11px; font-weight: 500;")
    right_actions.addWidget(btn)
    st = QLabel("待上传")
    st.setStyleSheet("font-size: 11px; color: #86868B;")
    right_actions.addWidget(st)
    bar = QProgressBar()
    bar.setFixedHeight(3)
    bar.setRange(0, 100)
    bar.setValue(0)
    right_actions.addWidget(bar)
    cl.addLayout(right_actions)
    
    cards_grid.addWidget(card, i // 2, i % 2)

app.processEvents()
print(f"\nAfter cards_grid populated:")
print(f"cards_wrap geometry: {cards_wrap.geometry()}")
print(f"cards_grid count: {cards_grid.count()}")
for i in range(cards_grid.count()):
    w = cards_grid.itemAt(i).widget()
    if w:
        print(f"  card {i}: {w.geometry()}")
