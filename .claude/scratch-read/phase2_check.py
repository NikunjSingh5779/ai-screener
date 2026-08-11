"""One-shot Phase 2 verification (v2): file presence, content markers, and a
pure-model pytest run. Root is fixed to the real project dir."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(r"C:\Users\ASUS\OneDrive\Desktop\ai screener")
REPORT = ROOT / ".claude/scratch-read/phase2_report.txt"

lines = []
for rel in ["ai_trader/overlay.py", "tests/test_overlay.py", "ai_trader/overlay_ui.py"]:
    p = ROOT / rel
    lines.append(f"{rel}: EXISTS bytes={p.stat().st_size}" if p.exists() else f"{rel}: MISSING")

cmd = [sys.executable, "-m", "pytest", "tests/test_overlay.py", "-q"]
proc = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
lines.append("PYTEST_STDOUT:")
lines.append(proc.stdout.strip())
lines.append("PYTEST_RC=" + str(proc.returncode))
REPORT.write_text("\n".join(lines), encoding="utf-8")
print("REPORT_WRITTEN_V2")
