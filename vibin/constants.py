import os.path
from pathlib import Path


VIBIN_PORT = 7669

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_ROOT = Path(APP_ROOT, "_data")
UI_ROOT = Path(APP_ROOT, "_webui")

UI_REPOSITORY = "mjoblin/vibinui"
