"""Start isolated from system Python configuration."""
import sys
import os
from pathlib import Path
os.environ.pop('QT_PLUGIN_PATH',None)
os.environ.pop('QT_QPA_PLATFORM_PLUGIN_PATH',None)
sys.path.insert(0,str(Path(__file__).resolve().parent))
try:
    from app import main
    main()
except Exception:
    import traceback
    import ctypes
    ctypes.windll.user32.MessageBoxW(0,traceback.format_exc(),'Luma 실행 오류',16)
