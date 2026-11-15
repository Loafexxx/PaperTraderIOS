"""Render papertrader.svg to PNGs (used by install.sh)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

src, out_dir = sys.argv[1], sys.argv[2]
app = QGuiApplication(sys.argv)
r = QSvgRenderer(src)
assert r.isValid(), "invalid SVG"
for size in (16, 24, 32, 48, 64, 128, 256, 512):
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    r.render(p)
    p.end()
    os.makedirs(f"{out_dir}/{size}", exist_ok=True)
    img.save(f"{out_dir}/{size}/papertrader.png")
print("rendered")
