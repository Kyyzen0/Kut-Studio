import subprocess
import sys


subprocess.check_call([
    sys.executable,
    "-m",
    "PyInstaller",
    "--noconfirm",
    "--windowed",
    "--name",
    "PremiereSimple",
    "main.py",
])
