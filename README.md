# Seerr Hub for Kodi

One "watch this" menu for any movie or show in Kodi. Pick a title and Seerr Hub offers, in order:

- **Play it from your library** (Plex, Jellyfin or Emby) if you already have it
- **Open it in the official streaming app** (Netflix, Disney+, Prime Video, HBO Max, Hulu and more) on Android TV, using JustWatch data for your region
- **Request it** in Seerr / Jellyseerr / Overseerr
- *(optional)* **Choose the release yourself** through Radarr/Sonarr and **start watching while qBittorrent is still downloading**

It works on its own (Search, Trending, My requests, Downloading), as a context-menu item ("Watch or request") on any movie, show, season or episode, and as a [TMDb Helper](https://github.com/jurialmunkey/plugin.video.themoviedb.helper) player.

> Seerr Hub doesn't stream anything itself. It only opens apps you already have, plays files from your own server, or asks your own Seerr to fetch them.

## What you need

- Kodi 20 or newer (tested on Kodi 21)
- A [Seerr](https://github.com/seerr-team/seerr), Jellyseerr or Overseerr server and its API key
- Optional: Plex with [PlexKodiConnect](https://github.com/croneter/PlexKodiConnect), or Jellyfin / Emby
- Optional: Radarr, Sonarr and qBittorrent (already connected to Seerr) for picking releases and watching downloads

Opening streaming apps only works on **Android** (Android TV, Shield, Fire TV, Google TV). On other systems the app options are skipped and the rest works as normal.

## Install

1. Download `plugin.video.seerrhub-<version>.zip` from [Releases](https://github.com/Mooseca/plugin.video.seerrhub/releases).
2. In Kodi: **Settings → System → Add-ons → Unknown sources** on.
3. **Settings → Add-ons → Install from zip file** and pick the zip.
4. Open the add-on settings and fill in at least the Seerr page.

To build the zip yourself: `python3 build.py`.

## Settings

### Seerr
| Setting | What to put |
|---|---|
| Seerr address | e.g. `http://192.168.1.10:5055` |
| Seerr API key | Seerr → Settings → General → API Key |
| Streaming region | Two-letter country code (`US`, `GB`, `AU`...). Blank uses the region set in Seerr. |

### Media server
**Ask Seerr** (the default) uses whichever server Seerr is connected to. You can force Plex, Jellyfin, Emby or None.

- **Plex** needs PlexKodiConnect signed in and synced: Seerr Hub plays titles from the Kodi library that PlexKodiConnect builds, and uses PlexKodiConnect's login. There's nothing to fill in here.
- **Jellyfin / Emby**: titles are played from the Kodi library, so use the Jellyfin for Kodi (or Emby for Kodi) add-on to sync it. If that add-on is signed in, Seerr Hub uses its login and you can leave these fields blank. Otherwise fill in the server address (e.g. `http://192.168.1.10:8096`), an API key (Dashboard → API Keys) and your user name.

If a title is on your server but not in Kodi's library yet, Seerr Hub opens the Plex / Jellyfin / Emby app instead (Android).

Emby goes through the same code as Jellyfin but hasn't been tested on a real Emby server. Reports welcome.

### Downloads (optional)
| Setting | What to put |
|---|---|
| Web UI address | qBittorrent Web UI, e.g. `http://192.168.1.10:8080` |
| User name / Password | Leave blank if qBittorrent skips login for your network |
| Download folder as Kodi sees it | qBittorrent's download folder shared to Kodi, e.g. `smb://nas/downloads/` (read-only is fine) |

With qBittorrent set up you get:
- **Download - choose the release**: Radarr/Sonarr's interactive search, ranked by your quality profile, with sizes, seeders and an estimate of whether it can be watched while downloading
- a **Downloading** list, where you can start watching once enough of the file is in (the add-on turns on sequential download for that torrent)
- watched status and resume points from those downloads are copied to Plex / Jellyfin / Emby once Radarr/Sonarr have imported the file

## TMDb Helper player

Copy [`seerrhub.json`](seerrhub.json) into `userdata/addon_data/plugin.video.themoviedb.helper/players/`. "Seerr Hub" then shows up as a player, and you can make it the default player in TMDb Helper's settings.

## Home-screen widgets

Any Seerr Hub folder works as a skin widget, e.g. `plugin://plugin.video.seerrhub/?action=trending`.

To keep a **Downloading** widget up to date, add this to the end of its path:

```
plugin://plugin.video.seerrhub/?action=downloads&reload=$INFO[Window(Home).Property(seerrhub.downloads)]
```

While something is downloading, the add-on's background service changes that property every 30 seconds, which makes the skin reload the widget.

For a skin menu "Search" button, use `RunPlugin(plugin://plugin.video.seerrhub/?action=ask)`.

## Supported streaming apps

Netflix, Prime Video, Disney+, HBO Max, Hulu, Apple TV+, Paramount+, Peacock, Crunchyroll, MUBI, Tubi, Pluto TV, YouTube, Crave, BBC iPlayer, ITVX, Stan, Binge, Kayo, ABC iview, SBS On Demand, 9Now, 7plus, 10 Play. The list is in `resources/lib/api.py` (`SERVICES`). Adding an app means adding its JustWatch name and Android package name there.

## License

MIT
