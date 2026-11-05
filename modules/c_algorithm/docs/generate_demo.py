"""Compatibility CLI entry for C's relocated synthetic M1 demonstration."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[3] / "C_cy071210_识别算法" / "generate_demo.py"
    runpy.run_path(str(target), run_name="__main__")
