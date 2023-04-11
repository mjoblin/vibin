import os.path
from pathlib import Path


VIBIN_PORT = 7669

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_ROOT = Path(APP_ROOT, "_data")
UI_ROOT = Path(APP_ROOT, "_webui")

UI_REPOSITORY = "mjoblin/vibinui"
UI_BUILD_DIR = "/build/"  # Directory in the repo which holds the UI build
UI_APPNAME = UI_REPOSITORY.split("/")[1]
