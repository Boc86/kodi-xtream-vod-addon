import os
import requests
from datetime import datetime, timedelta
import re
import logging
import time
import sys
import xbmcvfs
import xbmcaddon
import xbmcgui
import xbmc
import json
import threading
import schedule

# Initialize Kodi addon
addon = xbmcaddon.Addon()

# Retrieve settings from settings.xml
WORKING_DIR = addon.getSetting("working_dir")
MOVIES_DIR = addon.getSetting("movies_dir")
TV_SHOWS_DIR = addon.getSetting("tv_shows_dir")
SERVER = addon.getSetting("server")
USERNAME = addon.getSetting("username")
PASSWORD = addon.getSetting("password")
UPDATE_TIME = addon.getSetting("update_time")  # Format: "HH:MM"
UPDATE_INTERVAL = int(addon.getSetting("update_interval"))  # Days interval

# Ensure the server URL starts with "http://"
if not SERVER.startswith("http://"):
    SERVER = f"http://{SERVER}"

# Construct M3U URL
M3U_URL = f"{SERVER}/get.php?username={USERNAME}&password={PASSWORD}&type=m3u_plus&output=ts"

# Construct file paths
M3U_FILE = os.path.join(WORKING_DIR, "playlist.m3u")
LOG_FILE = os.path.join(WORKING_DIR, "m3u_processing.log")

# Add counter class
class Stats:
    def __init__(self):
        self.movies_added = 0
        self.shows_added = set()
        self.episodes_added = 0
        self.movies_deleted = 0
        self.shows_deleted = 0
        self.episodes_deleted = 0

# Setup logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format='%(asctime)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

def should_download_file():
    """Check if the M3U file should be downloaded based on its age."""
    if not xbmcvfs.exists(M3U_FILE):
        return True
    file_time = datetime.fromtimestamp(xbmcvfs.Stat(M3U_FILE).st_mtime())
    return datetime.now() - file_time > timedelta(hours=24)

def curl_request(url, binary=False):
    """
    Make a request with custom headers
    binary: If True, return raw bytes instead of text (for images)
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
        }

        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.content if binary else response.text

    except requests.RequestException as e:
        logging.error(f"RequestException: {e}")
        return None

def download_m3u():
    """Download the M3U file if it is outdated."""
    if should_download_file():
        xbmcgui.Dialog().notification("M3U Processing", "Downloading M3U file...", xbmcgui.NOTIFICATION_INFO, 5000)
        response = curl_request(M3U_URL, binary=True)
        if response:
            file_handle = xbmcvfs.File(M3U_FILE, 'wb')
            file_handle.write(response)
            file_handle.close()
            xbmcgui.Dialog().notification("M3U Processing", "Download complete", xbmcgui.NOTIFICATION_INFO, 5000)
        else:
            xbmcgui.Dialog().notification("M3U Processing", "Failed to download M3U file", xbmcgui.NOTIFICATION_ERROR, 5000)
            sys.exit(1)
    else:
        xbmcgui.Dialog().notification("M3U Processing", "Using existing M3U file (less than 24 hours old)", xbmcgui.NOTIFICATION_INFO, 5000)

def extract_info(extinf_line):
    # Extract show name from tvg-name
    name_match = re.search(r'tvg-name="([^"]+)"', extinf_line)
    tv_show_name = None
    if name_match:
        full_name = name_match.group(1)
        # Split on " - " and take everything after it
        if " - " in full_name:
            full_name = full_name.split(" - ", 1)[1]
           
        # Extract everything up to the year if present
        year_match = re.search(r'(.+?)(?:\s*\(\d{4}\))', full_name)
        if year_match:
            tv_show_name = year_match.group(1).strip()
        else:
            tv_show_name = full_name.strip()
    
    # Fallback: Extract name from the full line if tvg-name is not found
    if not tv_show_name:
        # Try to extract any text between quotes
        alt_name_match = re.search(r'"([^"]+)"', extinf_line)
        if alt_name_match:
            tv_show_name = alt_name_match.group(1).strip()
        else:
            # Last resort: Use "Unknown Title" with timestamp
            tv_show_name = f"Unknown Title {int(time.time())}"

    # Extract year with fallback
    year_match = re.search(r'\((\d{4})\)', extinf_line)
    tv_show_year = year_match.group(1) if year_match else "0000"

    # Extract season and episode numbers with fallbacks
    season_match = re.search(r'S(\d{2})', extinf_line)
    tv_show_season = season_match.group(1) if season_match else "01"

    episode_match = re.search(r'E(\d{2})', extinf_line)
    tv_show_episode = episode_match.group(1) if episode_match else "01"
   
    return {
        'name': tv_show_name,
        'year': tv_show_year,
        'season': tv_show_season,
        'episode': tv_show_episode
    }

def create_strm(path, url, stats, create_dirs=True):
    try:
        # Use xbmcvfs for file operations
        if not create_dirs:
            xbmcvfs.mkdirs(os.path.dirname(path) or '.')
        else:
            xbmcvfs.mkdirs(os.path.dirname(path))
           
        if not xbmcvfs.exists(path):
            file_handle = xbmcvfs.File(path, 'w')
            file_handle.write(url)
            file_handle.close()
            return True
        return False
    except Exception as e:
        logging.error(f"Error creating file {path}: {str(e)}")
        return False

def update_progress(current, total, movies, episodes):
    progress = (current / total) * 100
    xbmcgui.Dialog().notification(
        "M3U Processing",
        f"Progress: {progress:.1f}% | Movies: {movies} | Episodes: {episodes}",
        xbmcgui.NOTIFICATION_INFO,
        1000
    )
    sys.stdout.flush()

def sanitize_filename(filename):
    # Replace slashes, backslashes and other problematic characters
    invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
    for char in invalid_chars:
        filename = filename.replace(char, '-')
    return filename.strip()

def cleanup_stale_files(valid_files, stats):
    # Replace all file operations with xbmcvfs equivalents
    dialog = xbmcgui.Dialog()
    
    # Check Movies directory
    movies_to_delete = []
    dirs, files = xbmcvfs.listdir(MOVIES_DIR)
    for filename in files:
        if filename.endswith('.strm'):
            filepath = os.path.join(MOVIES_DIR, filename)
            if filepath not in valid_files:
                movies_to_delete.append(filepath)

    if movies_to_delete:
        if dialog.yesno("Delete Movies", f"Found {len(movies_to_delete)} movies to delete. Proceed?"):
            for filepath in movies_to_delete:
                xbmcvfs.delete(filepath)
                stats.movies_deleted += 1

    # Check TVShows directory
    show_folders = set()
    episodes_to_delete = []
    empty_seasons = []
    empty_shows = []

    dirs, show_folders = xbmcvfs.listdir(TV_SHOWS_DIR)
    for show_folder in show_folders:
        show_path = os.path.join(TV_SHOWS_DIR, show_folder)
        if xbmcvfs.isdir(show_path):
            empty_show = True
            dirs, season_folders = xbmcvfs.listdir(show_path)
            for season_folder in season_folders:
                season_path = os.path.join(show_path, season_folder)
                if xbmcvfs.isdir(season_path):
                    files_deleted = False
                    dirs, episodes = xbmcvfs.listdir(season_path)
                    for episode in episodes:
                        if episode.endswith('.strm'):
                            episode_path = os.path.join(season_path, episode)
                            if episode_path not in valid_files:
                                episodes_to_delete.append(episode_path)
                                files_deleted = True

                    # Track empty season folders
                    if not xbmcvfs.listdir(season_path)[1]:
                        empty_seasons.append(season_path)
                    elif not files_deleted:
                        empty_show = False

            # Track empty show folders
            if empty_show:
                empty_shows.append(show_path)

    if episodes_to_delete:
        if dialog.yesno("Delete Episodes", f"Found {len(episodes_to_delete)} episodes to delete. Proceed?"):
            for episode_path in episodes_to_delete:
                xbmcvfs.delete(episode_path)
                stats.episodes_deleted += 1

            # After deleting episodes, handle empty folders
            if empty_seasons:
                if dialog.yesno("Delete Empty Seasons", f"Found {len(empty_seasons)} empty season folders. Proceed?"):
                    for season_path in empty_seasons:
                        try:
                            xbmcvfs.rmdir(season_path)
                        except Exception:
                            pass

            if empty_shows:
                if dialog.yesno("Delete Empty Shows", f"Found {len(empty_shows)} empty show folders. Proceed?"):
                    for show_path in empty_shows:
                        try:
                            xbmcvfs.rmdir(show_path)
                            stats.shows_deleted += 1
                        except Exception:
                            pass

def get_server_domain():
    """Extract domain from server URL."""
    return SERVER.split('://')[1] if '://' in SERVER else SERVER

def process_m3u():
    stats = Stats()
    xbmcvfs.mkdirs(MOVIES_DIR)
    xbmcvfs.mkdirs(TV_SHOWS_DIR)
    valid_files = set()

    # Check if M3U file is empty
    if xbmcvfs.Stat(M3U_FILE).st_size() == 0:
        xbmcgui.Dialog().notification("M3U Processing", "Error: Empty playlist file. Exiting.", xbmcgui.NOTIFICATION_ERROR, 5000)
        sys.exit(1)

    xbmcgui.Dialog().notification("M3U Processing", "Processing M3U file...", xbmcgui.NOTIFICATION_INFO, 5000)
    file_handle = xbmcvfs.File(M3U_FILE, 'r')
    lines = file_handle.read().splitlines()
    file_handle.close()

    total_lines = len(lines)
    i = 0
    while i < total_lines:
        line = lines[i].strip()
        if line.startswith('#EXTINF'):
            info = extract_info(line)
            url = lines[i + 1].strip()
            server_domain = get_server_domain()
           
            # Always process if we have a URL, regardless of info completeness
            if url:
                # Movie processing
                if url.startswith(f'http://{server_domain}:80/movie/') or url.startswith(f'https://{server_domain}:80/movie/'):
                    safe_name = sanitize_filename(info['name'])
                    strm_path = os.path.join(MOVIES_DIR, f"{safe_name} {info['year']}.strm")
                    valid_files.add(strm_path)
                    if create_strm(strm_path, url, stats, create_dirs=False):
                        stats.movies_added += 1
               
                # TV Show processing
                elif url.startswith(f'http://{server_domain}:80/series/') or url.startswith(f'https://{server_domain}:80/series/'):
                    # Get the variables from info
                    tv_show_name = sanitize_filename(info['name'])
                    tv_show_year = info['year']
                    tv_show_season = info['season']
                    tv_show_episode = info['episode']
                    
                    # Create paths and STRM file
                    parent_folder = os.path.join(TV_SHOWS_DIR, f"{tv_show_name} {tv_show_year}")
                    season_folder = os.path.join(parent_folder, f"Season {tv_show_season}")
                    strm_filename = f"{tv_show_name} S{tv_show_season}E{tv_show_episode}.strm"
                    strm_path = os.path.join(season_folder, strm_filename)
                    valid_files.add(strm_path)
                    
                    # Ensure folders exist and create STRM file
                    xbmcvfs.mkdirs(parent_folder)
                    xbmcvfs.mkdirs(season_folder)
                    
                    if not xbmcvfs.exists(strm_path):
                        file_handle = xbmcvfs.File(strm_path, 'w')
                        file_handle.write(url)
                        file_handle.close()
                        stats.shows_added.add(f"{tv_show_name} {tv_show_year}")
                        stats.episodes_added += 1
            i += 2
        else:
            i += 1
       
        update_progress(i, total_lines, stats.movies_added, stats.episodes_added)

    # Cleanup stale files after processing
    cleanup_stale_files(valid_files, stats)
    xbmcgui.Dialog().notification("M3U Processing", "Processing complete!", xbmcgui.NOTIFICATION_INFO, 5000)
    return stats

def schedule_task():
    """Schedule the script to run at the specified time and interval."""
    def run_scheduled_task():
        logging.info("Scheduled task started.")
        main()

    # Schedule the task
    schedule.every(UPDATE_INTERVAL).days.at(UPDATE_TIME).do(run_scheduled_task)

    # Run the scheduler in a separate thread
    def scheduler_thread():
        while True:
            schedule.run_pending()
            time.sleep(1)

    threading.Thread(target=scheduler_thread, daemon=True).start()

def main():
    start_time = time.time()
    xbmcgui.Dialog().notification("M3U Processing", "Starting M3U processing script...", xbmcgui.NOTIFICATION_INFO, 5000)
    logging.info("Script started")
   
    download_m3u()
    stats = process_m3u()
   
    end_time = time.time()
    run_time = end_time - start_time
   
    xbmcgui.Dialog().notification(
        "M3U Processing",
        f"Summary:\nRun time: {run_time:.2f}s\nMovies added: {stats.movies_added}\nTV Shows added: {len(stats.shows_added)}\nEpisodes added: {stats.episodes_added}\nMovies deleted: {stats.movies_deleted}\nTV Shows deleted: {stats.shows_deleted}\nEpisodes deleted: {stats.episodes_deleted}",
        xbmcgui.NOTIFICATION_INFO,
        10000
    )
    logging.info(f"Script finished")
    logging.info(f"Total run time: {run_time:.2f} seconds")
    logging.info(f"Movies added: {stats.movies_added}")
    logging.info(f"TV Shows added: {len(stats.shows_added)}")
    logging.info(f"Episodes added: {stats.episodes_added}")
    logging.info(f"Movies deleted: {stats.movies_deleted}")
    logging.info(f"TV Shows deleted: {stats.shows_deleted}")
    logging.info(f"Episodes deleted: {stats.episodes_deleted}")

if __name__ == "__main__":
    # Start the scheduler
    schedule_task()

    # Run the script manually if needed
    main()
