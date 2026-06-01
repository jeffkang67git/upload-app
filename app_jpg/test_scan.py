#!/usr/bin/env python3
"""Test scan + UI with verbose debugging."""
import os
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt5.QtWidgets import QApplication, QListWidgetItem
from PyQt5.QtCore import Qt, QTimer
from src.app import MainWindow, DEVICE_LIST
import time

app = QApplication([])

w = MainWindow()
w._destroyed = False  # ensure not destroyed

# Monkey-patch to debug
original_finished = w._on_scan_finished
def debug_finished(patients):
    print(f"[DEBUG] _on_scan_finished called with {len(patients)} patients: {[p.mrn for p in patients]}")
    original_finished(patients)
    print(f"[DEBUG] After handler: patients={len(w.patients)}, list={w.patient_list.count()}")
w._on_scan_finished = debug_finished

original_show = w._show_patient
def debug_show(idx):
    print(f"[DEBUG] _show_patient({idx}) called")
    original_show(idx)
w._show_patient = debug_show

w.show()
app.processEvents()

# Wait for scan
print("[DEBUG] Waiting for scan...")
for i in range(10):
    time.sleep(0.5)
    app.processEvents()
    if w.patient_list.count() > 0:
        print(f"[DEBUG] At {i*0.5}s: patients={len(w.patients)}, list={w.patient_list.count()}")
        break
    print(f"[DEBUG] At {i*0.5}s: patients={len(w.patients)}, list={w.patient_list.count()}")

print(f"\nFinal: patients={len(w.patients)}, list={w.patient_list.count()}")
