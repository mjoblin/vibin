# `vibin`

`vibin` is the backend for [`vibinui`](https://github.com/mjoblin/vibinui). It:

* Talks to [StreamMagic] audio streamers.
* Manages music from:
  * NAS media servers running [Asset UPnP] (optional).
  * USB connections to the StreamMagic streamer (optional).
* Handles volume and mute controls for:
  * [StreamMagic] streamers in Pre-Amp mode, or via the Control Bus in Amplifier mode (optional).
  * [Hegel] amplifiers (optional).
* Serves the `vibinui` Web UI.
* Exposes a REST API and a WebSocket server for use by clients (such as `vibinui`).
* Comes with CLI tools for starting the server, controlling the streamer transport, installing the
  UI, etc.

> `vibin` can in theory be extended to support other streamers, media servers, and amplifiers.
> Currently it has only been used with Cambridge Audio StreamMagic streamers, USB media
> connected to the streamer, a NAS running Asset UPnP, and a Hegel H120 amplifier.

## Quick start

The following assumes you already have Python 3.10 (or higher) and Git installed.

### Install `vibin`

#### Mac, Linux

```
git clone https://github.com/mjoblin/vibin.git
cd vibin
python -m venv venv-vibin
source venv-vibin/bin/activate
pip install .
```

#### Windows PowerShell

```
git clone https://github.com/mjoblin/vibin.git
cd vibin
python -m venv venv-vibin
.\venv-vibin\Scripts\Activate
pip install .
```

Note: If `.\venv-vibin\Scripts\Activate` results in an error about running scripts being disabled,
you need to enable script execution on your system. To do this, start PowerShell as an administrator
(right-click the PowerShell application icon and choose "Run as administrator", then run the command
`Set-ExecutionPolicy RemoteSigned`. Then try the `.\venv-vibin\Scripts\Activate` step again).

### Use `vibin` to install the Web browser UI

```
vibin installui
```

### Start the `vibin` server

```
vibin serve
```

`vibin` will take a few seconds to start up. When ready, it will display a log line such as
`Uvicorn running on http://0.0.0.0:8080` at which point the Web UI can be loaded into a browser
at `http://localhost:8080`.

Run `vibin --help` to see all commandline options. To see all the `serve` options, run
`vibin serve --help`.

## Overview

`vibin` sits between the Vibin UI (`vibinui`) and the hardware devices (such as the music streamer,
NAS, and amplifier).

![Vibin Overview]

## Developers

See the [Developers README] for more information.

## Installation

`vibin` can be installed on any device with Python available, although for extended use it is
recommended to install on an always-on device which maintains an active network connection at all
times -- such as a Raspberry Pi or a server.

`vibin` has been tested on:

* Ubuntu 22.10 on a Raspberry Pi (Python 3.10)
* MacOS 13.4 (Python 3.11)
* Windows 11 (Python 3.11)

### Installing `vibin`

`vibin` requires [Python 3.10] or higher.

> The following installs `vibin` into a Python virtual environment. A virtual environment is
> recommended for isolation/cleanliness, but is not required.

To install `vibin` inside a Python virtual environment:

```
git clone https://github.com/mjoblin/vibin.git
cd vibin
python3 -m venv venv-vibin
source venv-vibin/bin/activate
pip install -e .
```

> Note: The `-e` ("editable") flag for `pip install` is optional. If used, then the special
> `vibin/_data/` and `vibin/_webui/` directories will be created where the git repository was
> checked out, rather than under the virtual environment's `lib/` directory.

The install can be validated by attempting to run the CLI with `vibin --help`:

```
$ vibin --help
Usage: vibin [OPTIONS] COMMAND [ARGS]...

  A commandline interface to the Vibin server.

  Note that "vibin serve" must be running before any other commands will
  function. See "vibin serve --help" for more information.

Options:
  --version  Show the version and exit.
  --help     Show this message and exit.

Commands:
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

### Using `vibin` to install `vibinui`

The CLI can then be used to install the Vibin Web UI:

```
vibin installui
```

This will retrieve the latest release version of the UI from GitHub, unpack it, and install it
under `vibin/_webui/`. This directory houses the static files (JavaScript, CSS, etc) which the
server will then serve to the browser.

## Running the server

Once installed, run the `vibin` server with:

```
vibin serve
```

`vibin serve` will:

1. Attempt to find a Cambridge Audio StreamMagic audio streamer on the network (using UPnP
   discovery).
1. Attempt to find (via the streamer) any supported local media.
1. Attempt to find a supported amplifier.
1. Serve the Web UI (if installed).

This behavior can be modified using command line options. See `vibin serve --help` for more
information. The supported options include:

```
  -h, --host HOST               Host to listen on.  [default: 0.0.0.0]
  -p, --port PORT               Port to listen on.  [default: 8080]
  -s, --streamer NAME           Streamer (hostname, UPnP friendly name, or UPnP location URL).
  --streamer-type TYPE          Streamer type (e.g. StreamMagic). Usually not required.
  -m, --media-server NAME       Media server (UPnP friendly name, or UPnP location URL).
  --media-server-type TYPE      Media server type (e.g. Asset). Usually not required.
  --no-media-server             Ignore any local media servers.
  -a, --amplifier NAME          Amplifier (UPnP friendly name, or UPnP location URL).
  --amplifier-type TYPE         Amplifier type (e.g. Hegel). Usually not required.
  --no-amplifier                Ignore any amplifiers.
  -t, --discovery-timeout SECS  UPnP discovery timeout (seconds).  [default: 5]
  -u, --vibinui DIR             Path to Web UI static files; use 'auto' to find 'vibin installui'
                                location.  [default: auto]
  --no-vibinui                  Do not serve the Web UI.
  -o, --proxy-media-server      Act as a proxy for the media server.
  --help                        Show this message and exit.
```

Once running, the logging output will show links to the UI and the REST API documentation. For
example, if the server is running on `192.168.1.100` then the following URLs will be available:

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
running `vibin serve` (e.g. on MacOS or Linux):

```
GENIUS_ACCESS_TOKEN=<genius_token> DISCOGS_ACCESS_TOKEN=<discogs_token> vibin serve
```

Replace `<genius_token>` and `<discogs_token>` with the previously-generated tokens.

A similar approach can be taken in PowerShell on Windows:

```
$Env:GENIUS_ACCESS_TOKEN = "<genius_token>"
$Env:DISCOGS_ACCESS_TOKEN = "<discogs_token>"

vibin serve
```

### Waveform generation

`vibin` uses [audiowaveform] for waveform generation, which needs to be installed separately (see
the [audiowaveform installation instructions]). Once installed into the path available to `vibin`,
waveforms will be automatically generated from local media on play.

## Interfaces

`vibin` provides three interfaces for interaction:

1. Command line interface (CLI).
2. REST API (under `/api`).
3. WebSocket server (at `/ws`).

> These interfaces are intended primarily for the Web UI. Most installs won't need to use more than
> `vibin installui` and `vibin serve`.

### Command line interface (CLI)

The `vibin` CLI is command-based. To see all available commands:

```
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

```
vibin <command> --help
```

e.g.:

```
vibin serve --help
```

### REST API

> The REST API can be ignored by most users.

`vibin` exposes a REST API. When `vibin serve` is running, the REST API documentation can be found
at `http://hostname:8080/docs` with the API endpoints being found under `http://hostname:8080/api`.
The API documentation is interactive. 

![Swagger]

### WebSocket server

> The WebSocket server can be ignored by most users.

`vibin` also exposes a WebSocket server at `http://hostname:8080/ws`. Connected clients will receive
messages describing updates to the backend as they happen.

## Database

`vibin` maintains its own database for storing lyrics, favorites, playlists, etc. This is stored in
`vibin/_data/`.


[//]: # "--- Links -------------------------------------------------------------------------------"

[StreamMagic]: https://www.cambridgeaudio.com/row/en/products/streammagic
[Asset UPnP]: https://dbpoweramp.com/asset-upnp-dlna.htm
[Hegel]: https://hegel.com
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
[Swagger]: media/vibin_swagger.png
