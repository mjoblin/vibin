from .albums import albums_router
from .artists import artists_router
from .browse import browse_router
from .favorites import favorites_router
from .media_server_proxy import media_server_proxy_router
from .active_playlist import playlist_router
from .presets import presets_router
from .queue import queue_router
from .stored_playlists import stored_playlists_router
from .system import system_router
from .tracks import tracks_router
from .transport import transport_router
from .ui_static import ui_static_router
from .upnp_events import upnp_events_router
from .vibin import vibin_router
from .websocket_server import ws_connection_manager, websocket_server_router
