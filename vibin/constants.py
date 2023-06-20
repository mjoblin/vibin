import importlib.metadata
import os.path
from pathlib import Path

VIBIN_VER = importlib.metadata.version("vibin")
VIBIN_PORT = 8080

# These media path defaults come from Asset UPnP DLNA. They can be overridden
# using the API (or via the Web UI).
DEFAULT_ALL_ALBUMS_PATH = "Album/[All Albums]"
DEFAULT_NEW_ALBUMS_PATH = "New Albums"
DEFAULT_ALL_ARTISTS_PATH = "Artist/[All Artists]"

APP_ROOT = os.path.dirname(os.path.abspath(__file__))
DB_ROOT = Path(APP_ROOT, "_data")
UI_ROOT = Path(APP_ROOT, "_webui")

UI_REPOSITORY = "mjoblin/vibinui"
UI_BUILD_DIR = "/build/"  # Directory in the repo which holds the UI build
UI_APPNAME = UI_REPOSITORY.split("/")[1]
