import subprocess
import sys

APP_NAME = "Kut Studio"

subprocess.check_call([
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--windowed",
    "--name",
    APP_NAME,
    "main.py",
])
