from .albums import albums_router
from .artists import artists_router
from .browse import browse_router
from .favorites import favorites_router
from .media_server_proxy import media_server_proxy_router
from .playlist import playlist_router
from .presets import presets_router
from .system import system_router
from .stored_playlists import stored_playlists_router
from .tracks import tracks_router
from .transport import transport_router
from .ui_static import ui_static_router
from .vibin import vibin_router
from .websocket_server import websocket_connection_manager, websocket_server_router
