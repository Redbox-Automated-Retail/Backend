"""Start unredbox/HW-Emu-Python v2 (hal_hw_emu) from C:\\rb\\tools\\hw-emu with the embeddable Python, which ignores
PYTHONPATH (its ._pth file fixes sys.path)."""
import runpy
import sys

sys.path.insert(0, r"C:\rb\tools\hw-emu")
sys.dont_write_bytecode = True  # the tools folder is mapped read-only
runpy.run_module("hal_hw_emu", run_name="__main__")
