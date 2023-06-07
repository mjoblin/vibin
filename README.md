# `vibin`

`vibin` is the back-end for [`vibinui`](https://github.com/mjoblin/vibinui). It:

* Talks to [StreamMagic] audio streamers.
* Talks to NAS media servers running [Asset UPnP].
* Serves the `vibinui` Web UI.
* Exposes a REST API and a WebSocket server for use by clients (such as `vibinui`).
* Comes with CLI tools for starting the server, controlling the streamer transport, installing the
  UI, etc.

> `vibin` can in theory be extended to support other streamers and media servers. Currently it has
> only been used with the Cambridge Audio CXNv2 and a NAS running Asset UPnP.

## Overview

`vibin` sits between the Vibin UI (`vibinui`) and the music streamer, NAS, etc.

## Developers

See the [Developers README] for more information.

![Vibin Overview]

## Installation

It is recommended to install and run `vibin` on an always-on device which maintains an active
network connection at all times, such as a Raspberry Pi. It will still run on other devices (like a
laptop), but may need to be restarted when the device comes out of sleep.

### Installing `vibin`

`vibin` requires [Python 3.10] or higher.

> The following installs `vibin` into a Python virtual environment. A virtual environment is
> recommended for isolation/cleanliness, but is not required.

To install `vibin` inside a Python virtual environment:

```bash
git clone https://github.com/mjoblin/vibin.git
cd vibin
python3 -m venv venv-vibin
source venv-vibin/bin/activate
pip install .
```

The install can be validated by attempting to run the CLI:

```
vibin --help
```

### Using `vibin` to install `vibinui`

The CLI can then be used to install the Vibin Web UI:

```bash
vibin installui
```

This will retrieve the latest release version of the UI from GitHub, unpack it, and install it
under `vibin/_webui/`. This directory houses the static files (JavaScript, CSS, etc) which the
server will then serve to the browser.

## Running the server

Once installed, run the `vibin` server with:

```bash
vibin serve
```

`vibin serve` will:

1. Attempt to find a Cambridge Audio StreamMagic audio streamer on the network (using UPnP
   discovery).
1. Attempt to find (via the streamer) any connected local media.
1. Serve the Web UI (if installed).

This behavior can be modified using command line parameters. See `vibin serve --help` for more
information.

The logging output will show links to the UI and the REST API documentation. For example, if the
server is running on `192.168.1.100` then the following URLs will be available:

* `http://192.168.1.100:8080/ui`: The Web user interface ([vibinui]).
* `http://192.168.1.100:8080/docs`: The REST API documentation.

> `vibin serve` will run until told to stop (e.g. using Ctrl-C). When the server stops running, the
> UI will no longer function.

## Optional extras

Some optional features can be enabled by doing some additional configuration:

* Generating tokens for external services to retrieve **lyrics** and generate **links**.
* Installing an **audio waveform** generation tool.

### Tokens for external services

`vibin` relies on external services for some of the information it makes available. These services
include:

* [Discogs], [Rate Your Music], and [Wikipedia], for links.
* [Genius] for lyrics and links.

Some of these services require special tokens to function properly. `vibin` does not come with
these tokens pre-installed. Any installation of `vibin` which wants these features enabled will
need its own tokens.

> Generating and using tokens is not required. A missing token will not prevent `vibin` from
> otherwise functioning as normal; it just means some links or lyrics may not be available.

#### Genius token

The Genius token is used for lyrics retrieval.

To generate a Genius token:

1. Create an account if you don't already have one.
1. Go to [https://docs.genius.com](https://docs.genius.com) and follow the instructions for
   generating an API client (which will result in a `client_id` and `client_secret`).
1. Once you have an API client created, generate a client token (for _application_ use not user
   use).
1. Provide the client token to `vibin` using the `GENIUS_ACCESS_TOKEN` environment variable.

#### Discogs token

The Discogs token is used to provide links to Discogs for the currently-playing media.

To generate a Discogs token:

1. Create an account if you don't already have one.
1. Go to [https://www.discogs.com/settings/developers](https://www.discogs.com/settings/developers)
   and click "Generate token". (`vibin` uses a _user token_ not a _Consumer Key and Secret_).
1. Provide the token to `vibin` using the `DISCOGS_ACCESS_TOKEN` environment variable.

#### Passing tokens to `vibin`

`vibin` uses environment variables to find tokens. One way to do this is to specify them when
running `vibin serve`:

```bash
GENIUS_ACCESS_TOKEN=<genius_token> DISCOGS_ACCESS_TOKEN=<discogs_token> vibin serve
```

Replace `<genius_token>` and `<discogs_token>` with the previously-generated tokens.

### Waveform generation

`vibin` uses [audiowaveform] for waveform generation, which needs to be installed separately (see
the [audiowaveform installation instructions]). Once installed into the path available to `vibin`,
waveforms will be automatically generated from local media on play.

## Interfaces

`vibin` provides three interfaces for interaction:

1. Command line interface (CLI).
2. REST API.
3. WebSocket server.

> These interfaces are intended primarily for the Web UI. Most installs won't need to use more than
> `vibin installui` and `vibin serve`.

### Command line interface (CLI)

The `vibin` CLI is command-based. To see all available commands:

```bash
vibin --help
```

Available commands include:

```
albums     Retrieve a list of all albums.
browse     Browse the children of the given media id.
installui  Install the Vibin Web UI.
next       Skip to the next track.
pause      Pause playback.
play       Resume playback, or play the specified media ID.
previous   Skip to the previous track.
seek       Seek into the current track.
serve      Start the Vibin server.
```

To get help on a single command:

```bash
vibin <command> --help
```

e.g.:

```bash
vibin serve --help
```

### REST API

`vibin` exposes a REST API. When `vibin serve` is running, the REST API documentation can be found
at `http://hostname:8080/docs`. The API documentation is interactive.

### WebSocket server

`vibin` also exposes a WebSocket server. Connected clients will receive messages describing updates
to the back-end as they happen.

#### Message types

The following message types are sent by `vibin`:

> These message types have evolved significantly over time, and would benefit from a cleanup pass.

* `ActiveTransportControls`: Which transport controls are currently available (e.g. play, pause,
  next track, etc). These will vary based on the current media source, and current player state.
* `DeviceDisplay`: What is currently being displayed on the streamer's display.
* `Favorites`: Information on Favorite Albums and Tracks.
* `PlayState`: Information about the current player state (playing, paused, etc), and the currently-
  playing media (including Album and Track IDs).
* `Position`: Playhead position.
* `Presets`: Information on Presets (e.g. Internet Radio stations).
* `StateVars`: A general kitchen-sink message. Mostly used for extracting audio source information,
  and some details on the current audio (including stream details like codec). **This message
  type's usefulness has largely been replaced by other message types and should be deprecated (once
  its remaining usefulness has been extracted)**.
* `StoredPlaylists`: Information on Stored Playlists.
* `System`: Information about the hardware devices (streamer name and power status; media server
  name).
* `VibinStatus`: Information about the Vibin back-end (start time, system information, connected
  clients, etc).

## Database

`vibin` maintains its own database for storing lyrics, favorites, playlists, etc.


[//]: # "--- Links -------------------------------------------------------------------------------"

[StreamMagic]: https://www.cambridgeaudio.com/row/en/products/streammagic
[Asset UPnP]: https://dbpoweramp.com/asset-upnp-dlna.htm
[Python 3.10]: https://www.python.org/downloads
[Discogs]: https://www.discogs.com
[Genius]: https://genius.com
[Rate Your Music]: https://rateyourmusic.com
[Wikipedia]: https://www.wikipedia.org
[audiowaveform]: https://github.com/bbc/audiowaveform#installation
[audiowaveform installation instructions]: https://github.com/bbc/audiowaveform#installation
[vibinui]: https://github.com/mjoblin/vibinui
[Developers README]: README_DEV.md

[//]: # "--- Images ------------------------------------------------------------------------------"

[Vibin Overview]: media/vibin_overview.svg
