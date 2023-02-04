from setuptools import setup, find_packages
import pathlib

from vibin import __version__

here = pathlib.Path(__file__).parent.resolve()

long_description = (here / "README.md").read_text(encoding="utf-8")

setup(
    name="vibin",
    version=__version__,
    description="The Vibin music server",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/mjoblin/vibin",
    packages=[
        "vibin",
        "vibin.cli",
        "vibin.mediasources",
        "vibin.streamers",
        "vibin.server",
    ],
    python_requires=">=3.9, <4",
    install_requires=[
        "aiofiles",
        "click",
        "fastapi",
        "httpx",
        "lxml",
        "lyricsgenius",
        "requests",
        "rich",
        "starlette",
        "upnpclient",
        "uvicorn[standard]",
        "wikipedia",
        "xmltodict",
    ],
    extras_require={
        "dev": [
            "black[d]",
        ],
        "test": [
            "coverage",
            "pytest",
        ],
    },
    entry_points={
        'console_scripts': [
            'vibin=vibin.cli:cli',
        ]
    },
)
