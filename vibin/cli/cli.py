import json
import tempfile

import click
import requests
from rich.console import Console
from rich.table import Table

from vibin import VibinError
from vibin.server import server_start
from vibin.constants import VIBIN_PORT


CONTEXT_SETTINGS = {
    "max_content_width": 100,
    "help_option_names": ["--help"],
}

SERVER_FILE = f"{tempfile.gettempdir()}/vibinserver"


@click.group()
def cli():
    """
    A commandline interface to the Vibin server.

    Note that "vibin serve" must be running before any other commands will
    function. See "vibin serve --help" for more information.
    """
    pass


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--host", "-h",
    help="Host to listen on.",
    metavar="HOST",
    type=click.STRING,
    default="0.0.0.0",
    show_default=True,
)
@click.option(
    "--port", "-p",
    help="Port to listen on.",
    metavar="PORT",
    type=click.INT,
    default=VIBIN_PORT,
    show_default=True,
)
@click.option(
    "--streamer", "-s",
    help="Streamer (hostname, UPnP friendly name, or UPnP location URL).",
    metavar="NAME",
    type=click.STRING,
    default=None,
    show_default=True,
)
@click.option(
    "--media", "-m",
    help="Media server (UPnP friendly name, or UPnP location URL).",
    metavar="NAME",
    type=click.STRING,
    default=None,
    show_default=True,
)
@click.option(
    "--no-media", "-n",
    help="Ignore any local media servers.",
    is_flag=True,
    default=False,
)
@click.option(
    "--discovery-timeout", "-t",
    help="UPnP discovery timeout (seconds).",
    metavar="SECS",
    type=click.INT,
    default=5,
    show_default=True,
)
@click.option(
    "--vibinui", "-u",
    help="Path to vibinui static files.",
    metavar="DIR",
    type=click.STRING,
    default=None,
    show_default=True,
)
@click.option(
    "--proxy-media-server", "-o",
    help="Act as a proxy for the media server.",
    is_flag=True,
    default=False,
)
def serve(
        host,
        port,
        streamer,
        media,
        no_media,
        discovery_timeout,
        vibinui,
        proxy_media_server,
):
    """
    Start the Vibin server.

    VIBIN API

    The Vibin server exposes a REST API for interacting with the music streamer
    and (when available) the local media server. This API is required for the
    other Vibin CLI commands to work, as well as for use by the Web interface.

    STREAMER AND MUSIC SERVER

    The Vibin server needs to know which music streamer on the network to
    interact with. By default, it will attempt to auto-find a Cambridge Audio
    streamer using UPnP discovery. Alternatively, the --streamer flag can be
    used to specify a streamer hostname (e.g. 192.168.1.100), UPnP friendly
    name, or UPnP location URL.

    Vibin currently expects the streamer to be a Cambridge Audio device
    supporting StreamMagic.

    If a local media server is also available on the network then it will be
    auto-detected from the Cambridge Audio streamer settings. Alternatively,
    the --media flag can be used to specify a media server UPnP friendly name,
    or UPnP location URL.

    WEB INTERFACE

    The Vibin server can also serve the Web interface to browsers on the
    network. The path to the Web interface application files can be specified
    with the --vibinui flag.

    Once the Vibin server has started, the Web interface will be available at
    http://<host>:<port>/ui (where <host> is the --hostname, and <port> is the
    --port).

    EXAMPLES

    To auto-discover the streamer and any local media server:

     $ vibin serve

    To specify a streamer hostname:

     $ vibin serve --streamer 192.168.1.100

    To specify a streamer and media server by UPnP friendly name:

     $ vibin serve --streamer MyStreamer --media MyMediaServer
    """
    if proxy_media_server and no_media:
        raise click.ClickException(
            f"Cannot specify both --proxy-media-server and --no-media"
        )

    with open(SERVER_FILE, "w") as server_file:
        server_file.write(f"http://{host}:{port}")

    try:
        server_start(
            host=host,
            port=port,
            streamer=streamer,
            media=False if no_media else media,
            discovery_timeout=discovery_timeout,
            vibinui=vibinui,
            proxy_media_server=proxy_media_server,
        )
    except VibinError as e:
        raise click.ClickException(f"Could not start Vibin server: {e}")


def get_server_info():
    with open(SERVER_FILE, "r") as server_file:
        server_info = server_file.readline()

    return server_info.strip()


def call_vibin(endpoint, method="POST", payload=None):
    vibin_server = None

    try:
        vibin_server = get_server_info()
    except IOError:
        click.echo(
            f"Unable to locate the Vibin server.\n\n" +
            f"When 'vibin serve' is run, the server details are stored in\n" +
            f"{SERVER_FILE}.\n\n" +
            f"Either the server has not been started, or the server details " +
            f"could not be stored.\n"
        )

        raise click.ClickException(f"Could not determine Vibin server details.")

    try:
        response = requests.request(
            method=method,
            url=f"{vibin_server}{endpoint}",
            json=payload,
        )

        if response.status_code >= 400:
            try:
                raise click.ClickException(response.json()["detail"])
            except (json.decoder.JSONDecodeError, KeyError):
                raise click.ClickException(response.text)

        return response.json()
    except requests.exceptions.ConnectionError:
        raise click.ClickException(
            f"Unable to connect to the Vibin server at {vibin_server}. Is " +
            f"'vibin serve' running?"
        )


@cli.command(context_settings=CONTEXT_SETTINGS)
def pause():
    """
    Pause playback.
    """
    call_vibin("/transport/pause")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--id",
    help="ID of the media to play (album or track).",
    metavar="ID",
    type=click.STRING,
    default=None,
)
def play(id):
    """
    Resume playback, or play the specified media ID.
    """
    if id is None:
        call_vibin("/transport/play")
    else:
        call_vibin(f"/transport/play/{id}")


@cli.command(context_settings=CONTEXT_SETTINGS)
def next():
    """
    Skip to the next track.
    """
    call_vibin("/transport/next")


@cli.command(context_settings=CONTEXT_SETTINGS)
def previous():
    """
    Skip to the previous track.
    """
    call_vibin("/transport/previous")


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--target",
    help="Seek target: h:mm:ss, seconds, or percentage (0.0 to 1.0).",
    metavar="TARGET",
    type=click.STRING,
    default=None,
)
def seek(target):
    """
    Seek into the current track.
    """
    call_vibin(f"/transport/seek?target={target}")


@cli.command(context_settings=CONTEXT_SETTINGS)
def albums():
    """
    Retrieve a list of all albums.
    """
    album_results = call_vibin("/albums", method="GET")

    console = Console()

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Artist")
    table.add_column("Title")

    for album in album_results:
        table.add_row(album["id"], album["artist"], album["title"])

    console.print(table)


@cli.command(context_settings=CONTEXT_SETTINGS)
@click.option(
    "--id",
    help="ID of the parent media to browse ('0' is the top-level container).",
    metavar="ID",
    type=click.STRING,
    default="0",
)
def browse(id):
    """
    Browse the children of the given media id.
    """
    browse_results = call_vibin(f"/browse/{id}", method="GET")

    console = Console()

    table = Table(show_header=True, header_style="bold")
    table.add_column("ID", style="dim", justify="right")
    table.add_column("Playable", justify="right")
    table.add_column("Title")

    for child in browse_results["children"]:
        table.add_row(
            child["id"],
            "True" if child["vibin_playable"] else "False",
            child["title"],
        )

    console.print(table)


if __name__ == "__main__":
    cli()
