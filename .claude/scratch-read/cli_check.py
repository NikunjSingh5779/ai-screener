"""Verify the rewritten cli.py: compiles, exports expected symbols, and the
--overlay path reports PyQt6 missing (PyQt6 is not installed in this env)."""
import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(r"C:\Users\ASUS\OneDrive\Desktop\ai screener")
sys.path.insert(0, str(ROOT))

REPORT = ROOT / ".claude/scratch-read/cli_check_report.txt"
lines = []

# 1. Compile check
src = Path(ROOT / "ai_trader/cli.py").read_text(encoding="utf-8")
try:
    compile(src, "ai_trader/cli.py", "exec")
    lines.append("COMPILE: ok")
except SyntaxError as exc:
    lines.append(f"COMPILE: FAIL {exc}")

# 2. Import + symbol check (imports ai_trader.cli; PyQt6 not needed)
try:
    import ai_trader.cli as cli
    lines.append("IMPORT: ok")
    lines.append(f"has ReadResult: {hasattr(cli, 'ReadResult')}")
    lines.append(f"has read_signal: {hasattr(cli, 'read_signal')}")
    lines.append(f"has run_overlay lazy: {'--overlay' in src}")
    rr = cli.ReadResult()
    lines.append(f"ReadResult defaults ctx={rr.ctx} err={rr.error} flip={rr.flip_suppressed}")
    lines.append(f"ReadResult.to_view() -> {rr.to_view()}")
except Exception as exc:  # noqa: BLE001
    lines.append(f"IMPORT: FAIL {type(exc).__name__}: {exc}")

# 3. main(["--overlay"]) without PyQt6 -> returncode 1 + error text
try:
    buf = io.StringIO()
    with redirect_stderr(buf):
        rc = cli.main(["--overlay"])
    lines.append(f"overlay_without_pyqt6: rc={rc} stderr_has_pyqt6={'PyQt6' in buf.getvalue()}")
except SystemExit as exc:  # main may raise SystemExit? it returns int, but be safe
    lines.append(f"overlay_without_pyqt6: SystemExit {exc.code}")
except Exception as exc:  # noqa: BLE001
    lines.append(f"overlay_without_pyqt6: EXC {type(exc).__name__}: {exc}")

REPORT.write_text("\n".join(lines), encoding="utf-8")
print("DONE")
