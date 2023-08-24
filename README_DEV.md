# `vibin` development

`vibin` is a Python 3.10 or higher application. It relies primarily on the following packages:

* [Click] (for the command line interface)
* [FastAPI] (for the REST API, WebSocket server, and proxies)
* [Pydantic] (for data models)
* [TinyDB] (for local persistence)
* [uPnPclient] (for communicating with UPnP devices)
* [untangle] (for XML parsing)
* [websockets] (for communicating with the StreamMagic streamer)

And [Black] for code formatting.

## Architecture

The main responsibilities of `vibin` are:

1. **Interact with a network music streamer**, implementing the `Streamer` interface.
   * The only current implementation is `StreamMagic` (for Cambridge Audio streamers using
     [StreamMagic]).
1. **Interact with a local media server** (optional), implementing the `MediaServer` interface.
   * The only current implementation is `Asset` (for the [Asset UPnP] server).
1. **Interact with an amplifier** (optional), implementing the `Amplifier` interface.
    * The only current implementation is `Hegel` (for [Hegel] amplifiers).
1. **Retrieve information from external sources** (Wikipedia, Genius, Rate Your Music, Discogs,
   etc).
1. **Persist information** such as user-defined Playlists, Favorites, lyrics, etc.
1. Expose:
   * **A REST API**.
     * To retrieve media metadata.
     * To perform actions on the streamer, media server, and amplifier.
     * To receive UPnP events from the streamer, media server, or amplifier; and to then forward the
       events on to the target interface implementation.
   * **A WebSocket server** (to send live updates to any connected clients).
   * **The UI's static files** (see [vibinui]).
   * **A proxy for the media server** (mostly for album art).
     * This is only required when one or more clients will be accessing `vibin` from a different
       network which otherwise can't access art on the media server.
   * **A command line interface (CLI)** (to start the server, interact with the streamer from the
     command line, etc).

The various components and how they broadly interact is shown below:

![Architecture]

### Device responsibility assumptions

Vibin assumes the following device responsibilities:

* `Streamer`: Initiates playback (of local media, internet radio, etc); details on what's currently
  playing (track/stream details); transport controls (play/pause, playhead position, etc).
* `MediaServer`: Local media browsing (Artists, Albums, Tracks, etc).
* `Amplifier`: Volume (including mute).

It's possible for one physical device (e.g. a streaming amplifier) to own two or all three of these
responsibilities, although Vibin's current implementation was built for three distinct physical
devices.

## Installation

To install vibin for development:

```bash
git clone https://github.com/mjoblin/vibin.git
cd vibin
python3 -m venv venv-vibin
source venv-vibin/bin/activate
pip install -e .
pip install -e '.[dev]'
```

## Project structure

The project structure is broadly laid out as follows:

```
.
├── _data/                             Persisted data (TinyDB)
├── _webui/                            The web UI's static files (once installed)
├── amplifiers                         Amplifier ABC and its implementations
│   ├── amplifier.py
│   └── hegel.py
├── base.py                            The main Vibin class
├── cli/                               The command line interface
├── constants.py                       Application constants
├── device_resolution.py               UPnP device discovery
├── exceptions.py                      Application exceptions
├── external_services/                 ExternalService and its implentations
│   ├── discogs.py
│   ├── external_service.py
│   ├── genius.py
│   ├── rateyourmusic.py
│   └── wikipedia.py
├── logger.py                          Application logger
├── managers                           Feature managers (used by the main Vibin class)
│   ├── favorites_manager.py
│   ├── links_manager.py
│   ├── lyrics_manager.py
│   ├── playlists_manager.py
│   └── waveform_manager.py
├── mediasources/                      MediaSource ABC and its implementations (Asset)
│   ├── asset.py
│   └── mediasource.py
├── server/                            The REST API, WebSocket server, and proxies (FastAPI)
│   ├── dependencies.py                Dependencies relied on by various routers
│   ├── routers/                       REST API routers
│   │   ├── active_playlist.py
│   │   ├── albums.py
│   │   ├── artists.py
│   │   ├── browse.py
│   │   ├── favorites.py
│   │   ├── media_server_proxy.py
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
├── streamers/                         Streamer ABC and its implementations (StreamMagic)
│   ├── streammagic.py
│   └── streamer.py
├── models.py                          Application models
├── types.py                           Application types
└── utils.py                           General utilities
```

### The `Vibin` class

The main hub of `vibin` is the `Vibin` class, which:

* Instantiates and manages a `Streamer` instance and (optionally) a `MediaServer` and `Amplifier`
  instance.
* Instantiates and manages any `ExternalService` implementations (such as Wikipedia, etc).
* Exposes all capabilities of the streamer, media server, and amplifier, such as transport controls,
  retrieving media metadata, etc.
* Acts as a hub for all the feature managers (Favorites, Links, Lyrics, etc).
* Announces any updates as messages over a WebSocket connection (such as playhead position updates,
  playlist updates, etc) to any interested subscribers.

The REST API is mostly a thin API layer that sits in front of `Vibin`. The WebSocket server
subscribes to any `Vibin` updates, which it then passes on to any connected clients.

#### WebSocket message types

The following message types are published:

* `CurrentlyPlaying`: Information about what's currently playing (current track, current playlist,
   format details, stream details, etc).
* `Favorites`: Information on Favorite Albums and Tracks.
* `Position`: Playhead position.
* `Presets`: Information on Presets (e.g. Internet Radio stations).
* `StoredPlaylists`: Information on Stored Playlists.
* `System`: Information about the hardware devices (streamer, media server, and amplifier) such as
  device names, power status, audio sources, etc.
* `TransportState`: Current state of the streamer transport (play state, active transport controls,
  shuffle and repeat state, etc).
* `UPnPProperties`: A general kitchen-sink message containing all the UPnP property values received
* by the streamer and media server.
* `VibinStatus`: Information about the Vibin back-end (start time, system information, connected
  clients, etc).

WebSocket messages can be viewed in the browser's Network pane. An example `TransportState` message
is shown below:

```json
{
    "id": "267c3e25-1f82-47ed-b711-3146511ad6d9",
    "client_id": "51e668ad-bf18-44ba-a19d-5e67779be4e9",
    "time": 1685764530636,
    "type": "TransportState",
    "payload": {
        "play_state": "play",
        "active_controls": [
            "pause",
            "stop",
            "shuffle",
            "repeat",
            "next",
            "previous",
            "seek"
        ],
        "repeat": "all",
        "shuffle": "off"
    }
}
```

#### REST API

The REST API's interactive swagger is available at `http://hostname:8080/docs`.

![Swagger]

The top-level REST routes include:

| Route               | Description                                                                                   |
|---------------------|-----------------------------------------------------------------------------------------------|
| `/vibin`            | Interact with the Vibin Server's top-level capabilities (settings, data cache, etc)           |
| `/system`           | Interact with the system's Streamer, Media Server, and Amplifier devices (power, source, etc) |
| `/artists`          | Interact with the Media Server's **Artists**                                                  |
| `/albums`           | Interact with the Media Server's **Albums**                                                   |
| `/tracks`           | Interact with the Media Server's **Tracks**                                                   |
| `/browse`           | **Browse media** on the Media Server                                                          |
| `/transport`        | Interact with the Streamer's **Transport** (pause, play, etc)                                 |
| `/presets`          | Interact with the Streamer's **Presets** (internet radio, etc)                                |
| `/active_playlist`  | Interact with the Streamer's **Active Playlist**                                              |
| `/stored_playlists` | Interact with Vibin's **Stored Playlists**                                                    |
| `/favorites`        | Interact with Vibin's **Favorites** (favorited Albums and Tracks)                             |

### A note on concurrent database access

TinyDB explicitly [does not support concurrency and/or HTTP server environments]. As a result, Vibin
uses a thread lock whenever reading from or writing to the database -- and assumes that all
DB-related FastAPI endpoints will be run in a thread pool not as a coroutine (i.e. `def` not
`async def` endpoint handlers). This approach seems to fend off concurrent DB access issues, but an
alternative persistence solution should probably be found.

### Supporting other hardware devices

The intent behind the [`Streamer()`](vibin/streamers/streamer.py),
[`MediaServer()`](vibin/mediaservers/mediaserver.py), and
[`Amplifier()`](vibin/amplifiers/amplifier.py) interfaces is that they would be general enough to
support a variety of implementations for different hardware devices. The reality is that they're
_heavily_ influenced by three specific products: [StreamMagic] network streamers from Cambridge
Audio, and the [Asset UPnP] media server software, and the [Hegel] amplifier control protocol
(implemented in [`streammagic.py`](vibin/streamers/streammagic.py),
[`asset.py`](vibin/mediaservers/asset.py), and [`hegel.py`](vibin/amplifiers/hegel.py)
respectively).

The same issue applies to many of the models (`models.py`) and types (`types.py`).

If additional devices were to be supported then it's likely that the interfaces, models, and types,
would need to be adjusted appropriately. It would be a learning adventure.

Supporting additional amplifiers would require implementing the `Amplifier` interface. The amplifier
would need to support software controls for power, volume, etc.

Supporting additional media server devices would require implementing the `MediaServer` interface.

Supporting additional streamer devices would require implementing the `Streamer` interface. The
implementation would also need to be sure to invoke the `on_update()` method (as passed in by
`Vibin` when instantiating the implementation) for the following message types: `CurrentlyPlaying`,
`Position`, and `TransportState`.

Any implementation would need to ensure that the data owned by the amplifier, media server, or
streamer, is munged into the shape expected by the types and models specified by the interfaces.

### Tests

`vibin`, regrettably, does not currently have any test coverage -- although [pytest] is an
aspirational dev dependency.

[//]: # "--- Links -------------------------------------------------------------------------------"

[StreamMagic]: https://www.cambridgeaudio.com/row/en/products/streammagic
[Asset UPnP]: https://dbpoweramp.com/asset-upnp-dlna.htm
[Hegel]: https://hegel.com
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
[does not support concurrency and/or HTTP server environments]: https://tinydb.readthedocs.io/en/latest/intro.html#why-not-use-tinydb

[//]: # "--- Images ------------------------------------------------------------------------------"

[Architecture]: media/vibin_architecture.svg
[Swagger]: media/vibin_swagger.png
