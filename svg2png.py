#!/usr/bin/env python3
"""svg2png: 把导图 SVG 用 Chrome headless 转 PNG（自动读 SVG 尺寸，1.6x）。
用法: python svg2png.py <in.svg> [out.png]
"""
import os
import re
import subprocess
import sys
from pathlib import Path

svg = Path(sys.argv[1]).resolve()
png = (Path(sys.argv[2]) if len(sys.argv) > 2 else svg.with_suffix(".png")).resolve()
head = svg.read_text(encoding="utf-8")[:400]
m = re.search(r'width="(\d+)" height="(\d+)"', head)
w, h = (int(m.group(1)), int(m.group(2))) if m else (1400, 2000)
chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                    "--force-device-scale-factor=1.6",
                    f"--user-data-dir={Path(os.environ.get('TEMP', '.')) / 'chrome-notes'}",
                    f"--window-size={w},{h}", f"--screenshot={png}",
                    f"file:///{svg.as_posix()}"], capture_output=True, text=True)
print(f"{svg.name}  {w}x{h} -> {png.name}  {png.stat().st_size:,} bytes")
