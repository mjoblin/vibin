from vibin import Vibin

vibin = Vibin(streamer="streamer", media_source="Asset UPnP: thicc")

name = vibin.streamer.name
albums = vibin.media_server.albums
print()

# vibin.play_id("coE2A8E1A777532255")

for i, album in enumerate(albums):
    print(f"{i} :: {album.artist} :: {album.title}")

print()
user_input = input("Play: ")

try:
    user_index = int(user_input)
    print(f"\nPlaying {albums[user_index].title} ... ", end="")

    vibin.play_id(vibin.media_server.albums[user_index].id)

    # vibin.play_album(vibin.media.albums[user_index])

    print("done.")

    # vibin.pause()

    # vibin.seek(0.5)
    # vibin.seek("0:02:14")
    # browse = vibin.browse_media()
    # browse = vibin.browse_media("coC2C844CDDBB1CFC8")
    browse = vibin.browse_media("co52D4D95CE96F9AAF")
    # browse = vibin.browse_media("coE2A8E1A777532255")
    pass
except ValueError:
    print("Not a number")
except IndexError:
    print("Invalid album index")
