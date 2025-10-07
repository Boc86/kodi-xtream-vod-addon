**This repo has now been archived, henryjfry has updated the code on his repo but there is likely to be further development**

***Link to henryjfry repo for the latest version [kodi-xtream-vod-addon](https://github.com/henryjfry/kodi-xtream-vod-addon)***

***henryjfry also hosts a more complete vod addon that i suggest checking out at [Xtreme Vod](https://github.com/henryjfry/repository.thenewdiamond/tree/main/script.xtreme_vod)***

***You can also check out Fynix Library Builder which is an evolution of the addon [Fynix Library Builder](https://github.com/Boc86/Fynix-Library-Builder)***





**Code generated using Github Autopilot**

Thanks to henryjfry for taking the time to fix my shoddy attempt at vibe coding this addon. Hopefully with his effort and fixes it can be of use for some people

# Kodi Xtream VOD Addon: IPTV VOD to Library

This Kodi addon allows you to seamlessly add IPTV Video-on-Demand (VOD) content to your Kodi library using Xtream API logins. It parses your IPTV provider's json responses and automatically generates `.strm` and `.nfo` files for movies and TV shows, enabling full Kodi library integration with metadata, artwork, and Trakt support.

> **This addon does not provide any content.** You must have a valid, legal IPTV subscription that allows Xtream API access.

## Version 2.6
- Fixed by henryjfry, all credit for taking my shoddy vibe coding attempt and turning it into something useful

---

## Version 2.5
- Retired m3u support as this is now used less and less and causing authentication errors with some IPTV suppliers
- Switched to full Xtream API json responses for parsing
- Change name addon name to reflect the changes
- Bundled all python dependancies with the addon to avoid the need for pip installations, this should now work on Android but has not been tested (feedback appreciated)
## Version 2.1
- Added sports category and folder creation to seperate them from movies
- Improved movie matching between m3u and json now 99% accurate
- Improved tv show matching between m3u and imtb now 90% accurate this comes at the expense of a slower run time
## Version 2
- Significant code overhaul
- Added TMDb metadata creation (credit to [henryjfry](https://github.com/henryjfry))
- Improved Title detection
- Improved library scanning time, 20k plus files reduced from 48+ hours to 4 hours due to the implementation of TMDb nfo metadata
- **Becuause this is effectively a new code base it is recommended to delete all files created with any versions prior to 2.0. and clean your kodi library**
- Version 1 will still work if you want to continue to use it but will no longer be maintained 
---

## Features

- **Automatic VOD Library Integration:**
  - Parses IPTV json responses and creates `.strm` and `.nfo` files for movies and TV shows.
  - Organises content into your chosen Movies and TV Shows directories for easy Kodi library scanning.
  - Fetches rich metadata and artwork from TMDb for a complete Kodi experience.
- **Configurable:**
  - Set working, Movies, and TV Shows directories.
  - Supports Xtream API only (if you need M3U playlist support use version 2.1, note that you will need to install the required python denpencies as they are not bundled prior to 2.5).
  - Adjustable update interval and time.
- **Efficient & Safe:**
  - 24-hour caching to avoid unnecessary downloads and reduce risk of provider bans.
  - Confirmation dialog before deleting files/folders.
  - Cleans all metadata and filenames to be ASCII-only for maximum compatibility.
- **No Live TV:**
  - This addon is for VOD only. For live TV channels, use [NextPVR](https://www.nextpvr.com/) or similar solutions.
  - Live TV json is saved in .kodi/userdata/addon_data/plugin.video.m3utostrm/cache/ipvos-all_stream_Live.json but not integrated
---

## How It Works

1. **Configuration:**
   - Set your working, movies, and TV shows directories in the Kodi addon settings.
   - Enter your IPTV provider's server address, username, and password (Xtream API details).
   - Enter your TMDb API key, these can be created for free from [TMDb](https://developer.themoviedb.org/docs/getting-started)
2. **Fetching & Caching:**
   - The addon downloads json responses and caches them for 24 hours to minimise server load.
   - Series and VOD metadata are also cached for 24 hours.
3. **Parsing & File Generation:**
   - The json files are parsed, and entries are matched to your provider's VOD/series catalog.
   - For each movie or TV episode, a `.strm` file (with the stream URL) and a Kodi-compatible `.nfo` file (with full metadata) are created in the appropriate directory.
   - TV shows are organised into folders by show and season.
4. **Kodi Library Integration:**
   - Add your Movies and TV Shows directories to Kodi as sources and scan them into your library.
   - Enjoy full metadata, artwork, and Trakt integration for your IPTV VOD content.

---

## Installation Instructions
### 2.5 Onwards
1. **Install the Addon**
   - Download the latest addon ZIP from the [Releases](https://github.com/Boc86/kodi-xtream-vod-addon/releases) page.
   - In Kodi, enable installation from unknown sources.
   - Go to **Add-ons > Install from zip file** and select the downloaded ZIP.
2. **Configure the Addon**
   - Open the addon configuration and set your directories, IPTV credentials, and TMDb API Key.
3. **Add Sources to Kodi Library**
   - Add your Movies and TV Shows directories as sources in Kodi and scan them into your library.

### Pre 2.5
1. **Install Python Requirements**
   - Download the [requirements.txt](https://github.com/Boc86/kodi-xtream-vod-addon/blob/main/requirements.txt) file.
   - Open a terminal in the folder where you downloaded `requirements.txt` and run:
     ```bash
     pip install -r requirements.txt
     ```
2. **Install the Addon**
   - Download the latest addon ZIP from the [Releases](https://github.com/Boc86/kodi-xtream-vod-addon/releases) page.
   - In Kodi, enable installation from unknown sources.
   - Go to **Add-ons > Install from zip file** and select the downloaded ZIP.
3. **Configure the Addon**
   - Open the addon settings and set your directories, IPTV credentials, and TMDb API Key.
   - Adjust update interval and time as desired.
4. **Add Sources to Kodi Library**
   - Add your Movies and TV Shows directories as sources in Kodi and scan them into your library.

---

## Important Notes

- **Security:**
  - The generated `.strm` files will contain your IPTV username and password in the stream URL. Make sure you understand the implications before using this addon and keep your library folders secure.
- **Caching:**
  - To avoid excessive polling and potential bans from your IPTV provider, the addon caches playlist and metadata for 24 hours. Do not reduce this interval.
- **No Content Provided:**
  - This addon does not provide any content. You must have a valid, legal IPTV subscription that Xtream API access.
- **Compatibility:**
  - All metadata and filenames are cleaned to be ASCII-only for maximum compatibility with Kodi and various filesystems.
- **Support:**
  - This addon is provided as-is. Please do not request support or fixes. Feel free to fork or modify the code for your own use.

---

## Tested On
- Ubuntu 24.04.2 LTS
- Fedora 41 & 42
- Kodi 21.2 Omega
- Python 3.x


Enjoy your IPTV VOD content fully integrated into your Kodi library!

Credit to [henryjfry](https://github.com/henryjfry) for pointing me in the direction of his repo and existing VOD work that inspired the V2 re-write






