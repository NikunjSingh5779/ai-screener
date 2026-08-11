from pathlib import Path

ROOT = Path(r"C:\Users\ASUS\OneDrive\Desktop\ai screener")
lines = []

req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
init = (ROOT / "ai_trader/__init__.py").read_text(encoding="utf-8")
readme = (ROOT / "README.md").read_text(encoding="utf-8")

lines.append(f"REQ has PyQt6: {'PyQt6>=6.6.0' in req}")
lines.append(f"REQ header 0/2: {'Phase 0/2 dependencies' in req}")
lines.append(f"INIT version 0.2.0: {'0.2.0' in init}")
lines.append(f"INIT docstring 0/2: {'Phase 0/2' in init}")
lines.append(f"README title 0/2: {'(Phase 0/2)' in readme.splitlines()[0]}")
lines.append(f"README current build: {'Phase 2 (floating overlay)' in readme}")
lines.append(f"README overlay cmd: {'python -m ai_trader.cli --overlay' in readme}")
lines.append(f"README age text: {'signal 8s old' in readme or 'stale (>5 min)' in readme}")
lines.append(f"README auto-skip: {'auto-skip when PyQt6' in readme}")

(ROOT / ".claude/scratch-read/meta_verify_report.txt").write_text("\n".join(lines), encoding="utf-8")
print("WROTE_META_REPORT")
