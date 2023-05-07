# `vibin` development

`vibin` is a Python 3.10 or higher application. It relies primarily on the following packages:

* [Click] (for the command line interface)
* [FastAPI] (for the REST API, WebSocket server, and proxies)
* [Pydantic] (for data models)
* [TinyDB] (for local persistence)
* [uPnPclient] (for communicating with UPnP devices)
* [untangle] (for XML parsing)

And [Black] for code formatting.

## Architecture

The main responsibilities of `vibin` are:

1. **Interact with a network music streamer**, implementing the `Streamer` interface.
   * The only current implementation is `CXNv2` (a Cambridge Audio streamer using [StreamMagic]).
1. **Interact with a local media server**, implementing the `MediaServer` interface.
   * The only current implementation is `Asset` (using the [Asset UPnP] server).
1. **Retrieve information from external sources** (Wikipedia, Genius, Rate Your Music, Discogs,
   etc).
1. **Persist information** such as user-defined Playlists, Favorites, lyrics, etc.
1. Expose:
   * **A REST API**.
     * To retrieve media metadata.
     * To perform actions on the streamer.
     * To receive UPnP events from the streamer.
   * **A WebSocket server** (to send live updates to any connected clients).
   * **The UI's static files** (see [vibinui]).
   * **A proxy for the media server** (mostly for album art).
     * This is only required when one or more clients will be accessing `vibin` from a different
       network which otherwise can't access art on the media server.
   * **A command line interface (CLI)** (to start the server, interact with the streamer from the
     command line, etc).

The various components and how they broadly interact is shown below:

![Architecture]

## Project structure

The project structure is broadly laid out as follows:

```
.
├── _data/                                Persisted data (TinyDB)
├── _webui/                               The web UI's static files (once installed)
├── base.py                               The main Vibin class
├── cli/                                  The command line interface
├── constants.py                          Application constants
├── device_resolution.py                  UPnP device discovery
├── exceptions.py                         Application exceptions
├── external_services/                    ExternalService and its implentations
│   ├── discogs.py
│   ├── external_service.py
│   ├── genius.py
│   ├── rateyourmusic.py
│   └── wikipedia.py
├── logger.py                             Application logger
├── mediasources/                         MediaSource and its implementations (Asset)
│   ├── asset.py
│   └── mediasource.py
├── models/                               Application models
│   └── models.py
├── server/                               The REST API, WebSocket server, and proxies (FastAPI)
│   ├── dependencies.py                   Dependencies relied on by various routers
│   ├── routers/                          API routers
│   │   ├── albums.py
│   │   ├── artists.py
│   │   ├── browse.py
│   │   ├── favorites.py
│   │   ├── media_server_proxy.py
│   │   ├── playlist.py
│   │   ├── playlists.py
│   │   ├── presets.py
│   │   ├── stored_playlists.py
│   │   ├── system.py
│   │   ├── tracks.py
│   │   ├── transport.py
│   │   ├── ui_static.py
│   │   ├── upnp_events.py
│   │   ├── vibin.py
│   │   └── websocket_server.py
│   └── server.py
├── streamers/                         Streamer and its implementations (CXNv2)
│   ├── cxnv2.py
│   └── streamer.py
└── utils.py                           General utilities
```

### The `Vibin` class

The main hub of `vibin` is the `Vibin` class, which:

* Instantiates and manages a `Streamer` instance and (optionally) a `MediaServer` instance.
* Instantiates and manages any `ExternalService` implementations (such as Wikipedia, etc).
* Exposes all capabilities of the streamer and media server, such as transport controls, retrieving
  media metadata, etc.
* Provides access to the information received from external services (such as lyrics, links, etc).
* Persists information to TinyDB.
* Announces any updates (such as playhead position updates, playlist updates, etc) to any interested
  subscribers.

The REST API is mostly a thin API layer that sits in front of `Vibin`. The WebSocket server
subscribes to any `Vibin` updates, which it then passes on to any connected clients.

### Tests

`vibin`, regrettably, does not currently have any test coverage -- although [pytest] is an
aspirational dev dependency.

[//]: # "--- Links -------------------------------------------------------------------------------"

[StreamMagic]: https://www.cambridgeaudio.com/row/en/products/streammagic
[Asset UPnP]: https://dbpoweramp.com/asset-upnp-dlna.htm
[vibinui]: https://github.com/mjoblin/vibinui
[Click]: https://click.palletsprojects.com
[FastAPI]: https://fastapi.tiangolo.com/
[Pydantic]: https://pydantic.dev
[TinyDB]: https://github.com/msiemens/tinydb
[uPnPclient]: https://github.com/flyte/upnpclient
[untangle]: https://untangle.readthedocs.io/
[websockets]: https://github.com/python-websockets/websockets
[Black]: https://github.com/psf/black
[pytest]: https://pytest.org

[//]: # "--- Images ------------------------------------------------------------------------------"

[Architecture]: media/vibin_architecture.svg