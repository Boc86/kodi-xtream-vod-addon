import os
import re
import json
import asyncio
import aiohttp
import aiofiles
from multiprocessing import cpu_count
from asyncio import Semaphore
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
from unidecode import unidecode
from requests_cache import CachedSession
from rapidfuzz import fuzz, process
import shutil
import time

# Kodi settings
ADDON = xbmcaddon.Addon()
SERVER_ADD = ADDON.getSetting('server_address')
USERNAME = ADDON.getSetting('username')
PASSWORD = ADDON.getSetting('password')
MOVIES_DIR = ADDON.getSetting('movies_dir')
TVSHOWS_DIR = ADDON.getSetting('tvshows_dir')
TMDB_API_KEY = ADDON.getSetting('tmdb_api_key')

if not SERVER_ADD.startswith("http://"):
    SERVER_ADD = f"http://{SERVER_ADD}"

M3U_URL = f'{SERVER_ADD}/get.php?username={USERNAME}&password={PASSWORD}&type=m3u_plus&output=mpegts'
SERIES_API_URL = f'{SERVER_ADD}/player_api.php?username={USERNAME}&password={PASSWORD}&action=get_series'
VOD_API_URL = f'{SERVER_ADD}/player_api.php?username={USERNAME}&password={PASSWORD}&action=get_vod_streams'

# Define paths for JSON cache files
CACHE_DIR = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo('profile')), 'cache')
SERIES_JSON_PATH = os.path.join(CACHE_DIR, 'ipvos-all_stream_Series.json')
VOD_JSON_PATH = os.path.join(CACHE_DIR, 'ipvos-all_stream_VOD.json')

session = CachedSession(cache_name='xtream_cache', backend='sqlite', expire_after=86400)

TCP_LIMIT = 500
SEM_LIMIT = 200
CHUNK_SIZE = 500
FILE_SEM_LIMIT = 200

def log_to_kodi(msg):
    xbmc.log(f"[m3utostrm] {msg}", xbmc.LOGINFO)
    print(f"[m3utostrm] {msg}")

def ensure_dir(dir_path):
    if not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path)
            log_to_kodi(f"Created directory {dir_path}")
        except Exception as e:
            log_to_kodi(f"Could not create directory {dir_path}: {e}")

def confirm_and_delete(paths):
    if not paths:
        return
    summary = '\n'.join(paths)
    dialog = xbmcgui.Dialog()
    msg = f"The following files/folders will be deleted:\n\n{summary}\n\nDo you want to proceed?"
    ret = dialog.yesno('Confirm Deletion', msg)
    if ret:
        for p in paths:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.isfile(p):
                    os.remove(p)
            except Exception as e:
                log_to_kodi(f"Failed to delete {p}: {e}")
        log_to_kodi(f"Deleted {len(paths)} files/folders.")
    else:
        log_to_kodi("User cancelled deletion.")

def fetch_json_data_sync():
    ensure_dir(CACHE_DIR)
    series_data = []
    vod_data = []
    try:
        log_to_kodi(f"Fetching Series data from {SERIES_API_URL}")
        series_resp = session.get(SERIES_API_URL)
        series_resp.raise_for_status()
        series_data = series_resp.json()
        original_series_count = len(series_data)
        series_data = [item for item in series_data if '#####' not in item.get('name', '')]
        log_to_kodi(f"Filtered out {original_series_count - len(series_data)} series entries with ##### in the title")
        with open(SERIES_JSON_PATH, 'w') as f:
            json.dump(series_data, f)
        log_to_kodi(f"Saved {len(series_data)} Series entries to {SERIES_JSON_PATH}")
        log_to_kodi(f"Fetching VOD data from {VOD_API_URL}")
        vod_resp = session.get(VOD_API_URL)
        vod_resp.raise_for_status()
        vod_data = vod_resp.json()
        original_vod_count = len(vod_data)
        vod_data = [item for item in vod_data if '#####' not in item.get('name', '')]
        log_to_kodi(f"Filtered out {original_vod_count - len(vod_data)} VOD entries with ##### in the title")
        with open(VOD_JSON_PATH, 'w') as f:
            json.dump(vod_data, f)
        log_to_kodi(f"Saved {len(vod_data)} VOD entries to {VOD_JSON_PATH}")
    except Exception as e:
        log_to_kodi(f"Error fetching JSON data: {e}")
    return series_data, vod_data

def filter_live_content(data_list):
    filtered_data = []
    for item in data_list:
        if item.get('stream_type', '').lower() != 'live':
            filtered_data.append(item)
    log_to_kodi(f"Filtered out {len(data_list) - len(filtered_data)} live stream entries from {len(data_list)} total")
    return filtered_data

def load_json_data():
    series_data = []
    vod_data = []
    try:
        if os.path.exists(SERIES_JSON_PATH):
            with open(SERIES_JSON_PATH, 'r') as f:
                series_data = json.load(f)
            log_to_kodi(f"Loaded {len(series_data)} Series entries from cache")
            original_series_count = len(series_data)
            series_data = [item for item in series_data if '#####' not in item.get('name', '')]
            log_to_kodi(f"Filtered out {original_series_count - len(series_data)} series entries with ##### in the title")
        if os.path.exists(VOD_JSON_PATH):
            with open(VOD_JSON_PATH, 'r') as f:
                vod_data = json.load(f)
            log_to_kodi(f"Loaded {len(vod_data)} VOD entries from cache")
            original_vod_count = len(vod_data)
            vod_data = [item for item in vod_data if '#####' not in item.get('name', '')]
            log_to_kodi(f"Filtered out {original_vod_count - len(vod_data)} VOD entries with ##### in the title")
    except Exception as e:
        log_to_kodi(f"Error loading cached JSON data: {e}")
    if not series_data or not vod_data:
        series_data, vod_data = fetch_json_data_sync()
    series_data = filter_live_content(series_data)
    vod_data = filter_live_content(vod_data)
    log_to_kodi(f"After filtering out live streams: {len(series_data)} series and {len(vod_data)} VOD entries remain")
    return series_data, vod_data

async def fetch_m3u():
    import time
    m3u_cache_path = os.path.join(CACHE_DIR, 'playlist.m3u')
    ensure_dir(CACHE_DIR)
    # Check if cache exists and is fresh (24h)
    if os.path.exists(m3u_cache_path):
        mtime = os.path.getmtime(m3u_cache_path)
        age = time.time() - mtime
        if age < 86400:
            log_to_kodi(f"Loading M3U playlist from cache: {m3u_cache_path}")
            with open(m3u_cache_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.read().splitlines()
            entries = parse_m3u(lines)
            log_to_kodi(f"Parsed {len(entries)} entries from cached M3U playlist")
            return entries
        else:
            log_to_kodi(f"M3U cache is older than 24h, re-downloading.")
    # Download and cache
    log_to_kodi(f"Fetching M3U playlist from {M3U_URL}")
    resp = session.get(M3U_URL, auth=(USERNAME, PASSWORD))
    resp.raise_for_status()
    with open(m3u_cache_path, 'w', encoding='utf-8') as f:
        f.write(resp.text)
    lines = resp.text.splitlines()
    entries = parse_m3u(lines)
    log_to_kodi(f"Parsed {len(entries)} entries from downloaded M3U playlist")
    return entries

def is_title_a_year(title):
    clean_title = title.strip()
    clean_title = re.sub(r'^\((\d{4})\)$', r'\1', clean_title)
    return re.match(r'^\d{4}$', clean_title) is not None

def extract_title_and_year(title):
    original_title = title
    prefixes = [
        r'^[A-Z]{2,3}\s*[|]\s*', r'^[A-Z]{2,3}\s*[-]\s*', r'^VOD\s*[|]\s*', r'^TV\s*[|]\s*',
        r'^\d+\.\s*', r'^[A-Z0-9_]+:\s*', r'^[0-9]+\s*[|]\s*', r'^num":\s*\d+,\s*"name":\s*"',
        r'^[A-Z]{2,3}\s*:\s*'
    ]
    clean_title = title
    for prefix_pattern in prefixes:
        clean_title = re.sub(prefix_pattern, '', clean_title)
    clean_title = re.sub(r'^\((\d{4})\)$', r'\1', clean_title.strip())
    if re.match(r'^\d{4}$', clean_title):
        log_to_kodi(f"Detected title as a year: {clean_title}")
        return clean_title, ""
    year_match = re.search(r'\((\d{4})\)|(?<!\()\b(\d{4})\b', title)
    year = ""
    if year_match:
        year = year_match.group(1) if year_match.group(1) else year_match.group(2)
    clean_title = title
    for prefix_pattern in prefixes:
        clean_title = re.sub(prefix_pattern, '', clean_title)
    if year:
        year_pattern_1 = r'\(' + year + r'\).*$'
        year_pattern_2 = r'\b' + year + r'\b.*$'
        clean_title = re.sub(year_pattern_1, '', clean_title)
        if year in clean_title:
            clean_title = re.sub(year_pattern_2, '', clean_title)
    suffixes = [r'\s*\|.*$', r'\s*-\s*.*$', r'\s+S\d+E\d+.*$', r'\s*"$']
    for suffix_pattern in suffixes:
        clean_title = re.sub(suffix_pattern, '', clean_title)
    clean_title = re.sub(r'\s+', ' ', clean_title).strip()
    return clean_title, year

def sanitize(name):
    name = unidecode(name)
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    # Remove any remaining non-ASCII characters
    name = ''.join(c for c in name if ord(c) < 128)
    return name

def remove_non_ascii(obj):
    """Recursively remove non-ASCII characters from all strings in a dict/list/str structure."""
    if isinstance(obj, dict):
        return {remove_non_ascii(k): remove_non_ascii(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [remove_non_ascii(i) for i in obj]
    elif isinstance(obj, str):
        return unidecode(obj)
    else:
        return obj

def create_filename(title, content_type, release_year=None):
    clean_title, year_from_title = extract_title_and_year(title)
    year = release_year if release_year else year_from_title
    # Always strip parentheses from clean_title if it's just a year
    if is_title_a_year(clean_title):
        clean_title = clean_title.strip('()')
        if year and clean_title != year:
            filename = f"{clean_title} {year}"
        else:
            filename = clean_title
    else:
        if year:
            filename = f"{clean_title} ({year})"
        else:
            filename = clean_title
    filename = sanitize(filename)
    # Prevent filenames like (2012) or empty
    if filename.startswith('(') and filename.endswith(')') and len(filename) == 6:
        filename = filename.strip('()')
    if not filename:
        filename = 'Unknown'
    return filename

def extract_series_name_from_url(url):
    """Extract the series name from a /series/ URL segment."""
    match = re.search(r'/series/([^/]+)/', url)
    if match:
        # Replace dots/underscores with spaces, remove extra symbols
        name = match.group(1)
        name = re.sub(r'[._]+', ' ', name)
        name = re.sub(r'[^\w\s-]', '', name)
        return name.strip()
    return None

def parse_m3u(lines):
    entries = []
    excluded_hashtag_count = 0
    extinf_pat = re.compile(r'#EXTINF:-1\s+(?P<attrs>.*?),(?P<title>.*)')
    tvg_group_pat = re.compile(r'tvg-group="([^"]*)"')
    group_title_pat = re.compile(r'group-title="([^"]*)"')
    stream_id_pat = re.compile(r'stream-id="([^"]*)"')
    tvg_id_pat = re.compile(r'tvg-id="([^"]*)"')
    for i, line in enumerate(lines):
        if line.startswith('#EXTINF'):
            try:
                m = extinf_pat.search(line)
                if not m:
                    continue
                title = unidecode(m.group('title')).strip() if m else 'Unknown'
                attrs = m.group('attrs') if m else ''
                if '#####' in title:
                    excluded_hashtag_count += 1
                    continue
                if i + 1 >= len(lines) or lines[i+1].startswith('#'):
                    continue
                url = lines[i+1].strip()
                if not url or url.startswith('#'):
                    continue
                stream_id_match = stream_id_pat.search(attrs)
                tvg_id_match = tvg_id_pat.search(attrs)
                stream_id = None
                if stream_id_match:
                    stream_id = stream_id_match.group(1)
                elif tvg_id_match:
                    stream_id = tvg_id_match.group(1)
                if not stream_id and 'id=' in url:
                    id_match = re.search(r'id=(\d+)', url)
                    if id_match:
                        stream_id = id_match.group(1)
                group_title_match = group_title_pat.search(attrs)
                tvg_group_match = tvg_group_pat.search(attrs)
                group = ""
                if group_title_match:
                    group = group_title_match.group(1)
                elif tvg_group_match:
                    group = tvg_group_match.group(1)
                content_type = "unknown"
                if 'movie' in group.lower() or 'vod' in url.lower() or '/movie/' in url.lower():
                    content_type = "movie"
                elif 'series' in group.lower() or 'show' in group.lower() or '/series/' in url.lower():
                    content_type = "tv"
                elif 'series' in url.lower():
                    content_type = "tv"
                elif 'movie' in url.lower():
                    content_type = "movie"
                # --- Improved TV show detection fallback ---
                if content_type == "tv":
                    # If title is empty, generic, or looks like a filename, fallback to URL extraction
                    fallback_needed = not title or title.lower() in ("unknown", "series", "tv show") or re.match(r'^s\d+e\d+', title, re.I)
                    if fallback_needed:
                        extracted = extract_series_name_from_url(url)
                        if extracted:
                            title = extracted
                clean_filename = create_filename(title, content_type)
                entries.append({
                    'title': title,
                    'url': url,
                    'safe': clean_filename,
                    'type': content_type,
                    'group': group,
                    'stream_id': stream_id,
                    'original_title': title
                })
            except Exception as e:
                log_to_kodi(f"Error parsing entry at line {i}: {e}")
    log_to_kodi(f"Excluded {excluded_hashtag_count} entries with ##### in the title")            
    return entries

def filter_entries_by_json(entries, series_data, vod_data):
    filtered_entries = []
    excluded_hashtag_count = 0
    series_ids = set(str(item.get('series_id')) for item in series_data if item.get('series_id'))
    vod_ids = set(str(item.get('stream_id')) for item in vod_data if item.get('stream_id'))
    log_to_kodi(f"Found {len(series_ids)} unique series IDs and {len(vod_ids)} unique VOD IDs in JSON files")
    series_titles = set(item.get('name', '').lower() for item in series_data if item.get('name'))
    vod_titles = set(item.get('name', '').lower() for item in vod_data if item.get('name'))
    id_matches = 0
    title_matches = 0
    for entry in entries:
        if '#####' in entry['title']:
            excluded_hashtag_count += 1
            continue
        matched = False
        stream_id = entry.get('stream_id')
        if stream_id:
            if (entry['type'] == 'tv' and stream_id in series_ids) or \
               (entry['type'] == 'movie' and stream_id in vod_ids):
                filtered_entries.append(entry)
                id_matches += 1
                matched = True
        if not matched:
            title_lower = entry['title'].lower()
            if entry['type'] == 'tv':
                for series_title in series_titles:
                    if (series_title and title_lower and 
                        (series_title in title_lower or title_lower in series_title)):
                        filtered_entries.append(entry)
                        title_matches += 1
                        matched = True
                        break
            elif entry['type'] == 'movie':
                for vod_title in vod_titles:
                    if (vod_title and title_lower and 
                        (vod_title in title_lower or title_lower in vod_title)):
                        filtered_entries.append(entry)
                        title_matches += 1
                        matched = True
                        break
    if excluded_hashtag_count > 0:
        log_to_kodi(f"Excluded an additional {excluded_hashtag_count} entries with ##### in the title during JSON matching")
    log_to_kodi(f"Matched {id_matches} entries by ID and {title_matches} by title")
    log_to_kodi(f"Filtered to {len(filtered_entries)} total entries that match JSON data")
    return filtered_entries

# ==== BEGIN: FILTER ENTRIES ALREADY EXISTING IN FILESYSTEM ====

def get_existing_movie_filenames(movies_dir):
    existing = set()
    if not os.path.exists(movies_dir):
        return existing
    for fname in os.listdir(movies_dir):
        if fname.lower().endswith('.strm'):
            base = os.path.splitext(fname)[0]
            existing.add(base.lower())
    return existing

def get_existing_tv_filenames(tvshows_dir):
    existing = set()
    if not os.path.exists(tvshows_dir):
        return existing
    for root, dirs, files in os.walk(tvshows_dir):
        for fname in files:
            if fname.lower().endswith('.strm'):
                base = os.path.splitext(fname)[0]
                existing.add(base.lower())
    return existing

def filter_entries_that_exist(entries, movies_dir, tvshows_dir):
    movie_existing = get_existing_movie_filenames(movies_dir)
    tv_existing = get_existing_tv_filenames(tvshows_dir)
    filtered = []
    skipped = 0
    for entry in entries:
        if entry['type'] == 'movie':
            fn = create_filename(entry['title'], entry['type'])
            if fn.lower() in movie_existing:
                skipped += 1
                continue
        elif entry['type'] == 'tv':
            show_name, year = extract_title_and_year(entry['title'])
            show_name = sanitize(show_name)
            season, episode = extract_season_episode(entry['original_title'])
            if not (season and episode):
                season, episode = extract_season_episode(entry['safe'])
            if season and episode:
                folder_name = f"{show_name}{f' ({year})' if year else ''}"
                tvfn = kodi_tv_episode_filename(show_name, year, season, episode, "strm")
                tvfn_base = os.path.splitext(tvfn)[0].lower()
                if tvfn_base in tv_existing:
                    skipped += 1
                    continue
            else:
                folder_name = f"{show_name}{f' ({year})' if year else ''}"
                fallback_fn = sanitize(folder_name)
                if fallback_fn.lower() in tv_existing:
                    skipped += 1
                    continue
        filtered.append(entry)
    log_to_kodi(f"Filtered out {skipped} entries that already exist in library folders.")
    return filtered

# ==== END: FILTER ENTRIES ALREADY EXISTING IN FILESYSTEM ====

async def fetch_movie_metadata(title, session, sem: Semaphore):
    clean_title, year = extract_title_and_year(title)
    search_query = clean_title
    is_year_title = is_title_a_year(clean_title)
    url = 'https://api.themoviedb.org/3/search/movie'
    params = {'api_key': TMDB_API_KEY, 'query': search_query}
    if year and not is_year_title:
        params['year'] = year
    async with sem:
        async with session.get(url, params=params, timeout=30) as r:
            if r.status == 200:
                data = await r.json()
                results = data.get('results', [])
                if results:
                    movie_id = results[0].get('id')
                else:
                    # Fuzzy match fallback
                    # Try again without year restriction
                    params.pop('year', None)
                    async with session.get(url, params=params, timeout=30) as r2:
                        if r2.status == 200:
                            data2 = await r2.json()
                            results2 = data2.get('results', [])
                            if results2:
                                # Fuzzy match
                                choices = {m['title']: m for m in results2 if 'title' in m}
                                match, score, _ = process.extractOne(clean_title, list(choices.keys()), scorer=fuzz.token_sort_ratio)
                                if score >= 85:
                                    movie_id = choices[match]['id']
                                else:
                                    return {}
                            else:
                                return {}
                        else:
                            return {}
            else:
                return {}
    if not movie_id:
        return {}
    details_url = f'https://api.themoviedb.org/3/movie/{movie_id}'
    details_params = {'api_key': TMDB_API_KEY, 'append_to_response': 'credits,external_ids,release_dates'}
    async with sem:
        async with session.get(details_url, params=details_params, timeout=30) as r:
            if r.status == 200:
                return await r.json()
            else:
                return {}

async def fetch_tv_metadata(title, session, sem: Semaphore):
    from urllib.parse import quote

    clean_title, year = extract_title_and_year(title)
    search_query = clean_title
    is_year_title = is_title_a_year(clean_title)

    api_key = TMDB_API_KEY
    tmdb_bearer = ADDON.getSetting('tmdb_bearer_token') if hasattr(ADDON, 'getSetting') else None

    search_url = 'https://api.themoviedb.org/3/search/tv'
    search_params = {'query': search_query}
    if api_key:
        search_params['api_key'] = api_key
    if year and not is_year_title:
        search_params['first_air_date_year'] = year

    headers = {}
    if tmdb_bearer:
        headers['Authorization'] = f'Bearer {tmdb_bearer}'
        search_params.pop('api_key', None)

    async with sem:
        async with session.get(search_url, params=search_params, headers=headers, timeout=30) as r:
            if r.status != 200:
                return {}
            data = await r.json()
            results = data.get('results', [])
            if not results:
                # Fuzzy match fallback
                # Try again without year restriction
                search_params.pop('first_air_date_year', None)
                async with session.get(search_url, params=search_params, headers=headers, timeout=30) as r2:
                    if r2.status == 200:
                        data2 = await r2.json()
                        results2 = data2.get('results', [])
                        if results2:
                            choices = {m['name']: m for m in results2 if 'name' in m}
                            match, score, _ = process.extractOne(clean_title, list(choices.keys()), scorer=fuzz.token_sort_ratio)
                            if score >= 85:
                                tmdb_id = choices[match]['id']
                            else:
                                return {}
                        else:
                            return {}
                    else:
                        return {}
            else:
                tmdb_id = results[0].get('id')

    if not tmdb_id:
        return {}

    details_url = f'https://api.themoviedb.org/3/tv/{tmdb_id}'
    details_params = {'append_to_response': 'credits,external_ids,content_ratings'}
    if api_key and not tmdb_bearer:
        details_params['api_key'] = api_key

    headers = {}
    if tmdb_bearer:
        headers['Authorization'] = f'Bearer {tmdb_bearer}'
        details_params.pop('api_key', None)

    async with sem:
        async with session.get(details_url, params=details_params, headers=headers, timeout=30) as r:
            if r.status != 200:
                return {}
            meta = await r.json()
            return meta

async def fetch_episode_metadata(tv_id, season, episode, session, sem):
    api_key = TMDB_API_KEY
    tmdb_bearer = ADDON.getSetting('tmdb_bearer_token') if hasattr(ADDON, 'getSetting') else None

    url = f'https://api.themoviedb.org/3/tv/{tv_id}/season/{season}/episode/{episode}'
    # Add all possible metadata fields for episode NFO
    params = {'append_to_response': 'credits,external_ids,images,content_ratings'}
    if api_key and not tmdb_bearer:
        params['api_key'] = api_key

    headers = {}
    if tmdb_bearer:
        headers['Authorization'] = f'Bearer {tmdb_bearer}'
        params.pop('api_key', None)

    async with sem:
        async with session.get(url, params=params, headers=headers, timeout=30) as r:
            if r.status != 200:
                return {}
            return await r.json()

def meta_to_nfo(meta, entry_type):
    from xml.etree.ElementTree import Element, SubElement, tostring

    meta = remove_non_ascii(meta)
    if entry_type == 'movie':
        root = Element('movie')
        SubElement(root, 'title').text = meta.get('title', '')
        SubElement(root, 'originaltitle').text = meta.get('original_title', meta.get('title', ''))
        sorttitle = meta.get('title', '')
        if meta.get('belongs_to_collection'):
            sorttitle = meta['belongs_to_collection'].get('name', sorttitle)
        SubElement(root, 'sorttitle').text = sorttitle
        ratings = SubElement(root, 'ratings')
        imdb_id = meta.get('external_ids', {}).get('imdb_id')
        if imdb_id:
            rating = SubElement(ratings, 'rating', name='imdb', max='10', default='true')
            SubElement(rating, 'value').text = str(meta.get('vote_average', ''))
            SubElement(rating, 'votes').text = str(meta.get('vote_count', ''))
        rating_tmdb = SubElement(ratings, 'rating', name='themoviedb', max='10')
        SubElement(rating_tmdb, 'value').text = str(meta.get('vote_average', ''))
        SubElement(rating_tmdb, 'votes').text = str(meta.get('vote_count', ''))
        rating_trakt = SubElement(ratings, 'rating', name='trakt', max='10')
        SubElement(rating_trakt, 'value').text = ''
        SubElement(rating_trakt, 'votes').text = ''
        SubElement(root, 'userrating').text = str(meta.get('vote_average', ''))
        SubElement(root, 'top250').text = '0'
        SubElement(root, 'outline').text = ''
        SubElement(root, 'plot').text = meta.get('overview', '')
        SubElement(root, 'tagline').text = meta.get('tagline', '')
        SubElement(root, 'runtime').text = str(meta.get('runtime', ''))
        poster_path = meta.get('poster_path')
        backdrop_path = meta.get('backdrop_path')
        if poster_path:
            SubElement(root, 'thumb', spoof='', cache='', aspect='poster', preview=f"https://image.tmdb.org/t/p/original{poster_path}").text = f"https://image.tmdb.org/t/p/original{poster_path}"
        if backdrop_path:
            SubElement(root, 'thumb', spoof='', cache='', aspect='landscape', preview=f"https://image.tmdb.org/t/p/original{backdrop_path}").text = f"https://image.tmdb.org/t/p/original{backdrop_path}"
        if backdrop_path:
            fanart = SubElement(root, 'fanart')
            SubElement(fanart, 'thumb', colors='', preview=f"https://image.tmdb.org/t/p/w780{backdrop_path}").text = f"https://image.tmdb.org/t/p/original{backdrop_path}"
        mpaa = ''
        for rel in meta.get('release_dates', {}).get('results', []):
            if rel.get('iso_3166_1') == 'US':
                for r in rel.get('release_dates', []):
                    if r.get('certification'):
                        mpaa = r.get('certification')
                        break
        SubElement(root, 'mpaa').text = mpaa
        SubElement(root, 'playcount').text = '0'
        SubElement(root, 'lastplayed').text = ''
        SubElement(root, 'id').text = str(meta.get('id', ''))
        if imdb_id:
            SubElement(root, 'uniqueid', type='imdb').text = imdb_id
        SubElement(root, 'uniqueid', type='tmdb', default='true').text = str(meta.get('id', ''))
        genres = meta.get('genres', [])
        if genres:
            for g in genres:
                SubElement(root, 'genre').text = g.get('name', '')
        countries = meta.get('production_countries', [])
        for c in countries:
            SubElement(root, 'country').text = c.get('name', '')
        if meta.get('belongs_to_collection'):
            set_el = SubElement(root, 'set')
            SubElement(set_el, 'name').text = meta['belongs_to_collection'].get('name', '')
            SubElement(set_el, 'overview').text = ''
        crew = meta.get('credits', {}).get('crew', [])
        writers = [w.get('name') for w in crew if w.get('job', '').lower() == 'writer']
        for w in writers:
            SubElement(root, 'credits').text = w
        directors = [d.get('name') for d in crew if d.get('job', '').lower() == 'director']
        for d in directors:
            SubElement(root, 'director').text = d
        SubElement(root, 'premiered').text = meta.get('release_date', '')
        SubElement(root, 'year').text = (meta.get('release_date') or '')[:4]
        SubElement(root, 'status').text = ''
        SubElement(root, 'code').text = ''
        SubElement(root, 'aired').text = ''
        studios = meta.get('production_companies', [])
        for s in studios:
            SubElement(root, 'studio').text = s.get('name', '')
        SubElement(root, 'trailer').text = ''
        cast = meta.get('credits', {}).get('cast', [])
        for idx, actor in enumerate(cast):
            actor_el = SubElement(root, 'actor')
            SubElement(actor_el, 'name').text = actor.get('name', '')
            SubElement(actor_el, 'role').text = actor.get('character', '')
            SubElement(actor_el, 'order').text = str(idx)
            if actor.get('profile_path'):
                SubElement(actor_el, 'thumb').text = f"https://image.tmdb.org/t/p/original{actor['profile_path']}"
    else:
        root = Element('tvshow')
        SubElement(root, 'title').text = meta.get('name', '')
        SubElement(root, 'originaltitle').text = meta.get('original_name', meta.get('name', ''))
        SubElement(root, 'showtitle').text = meta.get('name', '')
        # Ratings
        ratings = SubElement(root, 'ratings')
        imdb_id = meta.get('external_ids', {}).get('imdb_id')
        tvdb_id = meta.get('external_ids', {}).get('tvdb_id')
        tmdb_id = meta.get('id')
        # IMDB rating (if available)
        rating_imdb = SubElement(ratings, 'rating', name='imdb', max='10', default='true')
        SubElement(rating_imdb, 'value').text = ''
        SubElement(rating_imdb, 'votes').text = ''
        # TMDB rating
        rating_tmdb = SubElement(ratings, 'rating', name='tmdb', max='10')
        SubElement(rating_tmdb, 'value').text = str(meta.get('vote_average', ''))
        SubElement(rating_tmdb, 'votes').text = str(meta.get('vote_count', ''))
        # Trakt rating (placeholder)
        rating_trakt = SubElement(ratings, 'rating', name='trakt', max='10')
        SubElement(rating_trakt, 'value').text = ''
        SubElement(rating_trakt, 'votes').text = ''
        SubElement(root, 'userrating').text = str(meta.get('vote_average', ''))
        SubElement(root, 'top250').text = '0'
        # Season/Episode counts
        SubElement(root, 'season').text = str(meta.get('number_of_seasons', ''))
        SubElement(root, 'episode').text = str(meta.get('number_of_episodes', ''))
        SubElement(root, 'displayseason').text = '-1'
        SubElement(root, 'displayepisode').text = '-1'
        SubElement(root, 'outline').text = ''
        SubElement(root, 'plot').text = meta.get('overview', '')
        SubElement(root, 'tagline').text = meta.get('tagline', '')
        SubElement(root, 'runtime').text = str(meta.get('episode_run_time', ['0'])[0] if meta.get('episode_run_time') else '0')
        # Thumbs (main images)
        poster_path = meta.get('poster_path')
        backdrop_path = meta.get('backdrop_path')
        logo_path = ''
        if meta.get('images'):
            logos = meta['images'].get('logos', [])
            if logos:
                logo_path = logos[0].get('file_path', '')
        if backdrop_path:
            SubElement(root, 'thumb', spoof='', cache='', aspect='landscape', preview=f"https://image.tmdb.org/t/p/w780{backdrop_path}").text = f"https://image.tmdb.org/t/p/original{backdrop_path}"
        if logo_path:
            SubElement(root, 'thumb', spoof='', cache='', aspect='logos', preview=f"https://image.tmdb.org/t/p/w780{logo_path}").text = f"https://image.tmdb.org/t/p/original{logo_path}"
        if poster_path:
            SubElement(root, 'thumb', spoof='', cache='', aspect='poster', preview=f"https://image.tmdb.org/t/p/w780{poster_path}").text = f"https://image.tmdb.org/t/p/original{poster_path}"
        # Season posters
        for season in meta.get('seasons', []):
            s_poster = season.get('poster_path')
            s_num = season.get('season_number')
            if s_poster and s_num is not None:
                SubElement(root, 'thumb', spoof='', cache='', season=str(s_num), type='season', aspect='poster', preview=f"https://image.tmdb.org/t/p/w780{s_poster}").text = f"https://image.tmdb.org/t/p/original{s_poster}"
        # Fanart (multiple images)
        fanart = SubElement(root, 'fanart')
        fanart_paths = []
        if meta.get('images'):
            fanart_paths = [img['file_path'] for img in meta['images'].get('backdrops', [])[:2] if img.get('file_path')]
        elif backdrop_path:
            fanart_paths = [backdrop_path]
        for fpath in fanart_paths:
            SubElement(fanart, 'thumb', colors='', preview=f"https://image.tmdb.org/t/p/original{fpath}").text = f"https://image.tmdb.org/t/p/original{fpath}"
        # MPAA/content rating
        mpaa = ''
        for rel in meta.get('content_ratings', {}).get('results', []):
            if rel.get('iso_3166_1') == 'US' and rel.get('rating'):
                mpaa = f"US:{rel['rating']}"
                break
            elif rel.get('iso_3166_1') and rel.get('rating'):
                mpaa = f"{rel['iso_3166_1']}:{rel['rating']}"
        SubElement(root, 'mpaa').text = mpaa
        SubElement(root, 'playcount').text = '0'
        SubElement(root, 'lastplayed').text = ''
        SubElement(root, 'id').text = str(tmdb_id or '')
        # Unique IDs
        if imdb_id:
            SubElement(root, 'uniqueid', type='imdb').text = imdb_id
        if tmdb_id:
            SubElement(root, 'uniqueid', type='tmdb', default='true').text = str(tmdb_id)
        if tvdb_id:
            SubElement(root, 'uniqueid', type='tvdb').text = str(tvdb_id)
        # Genres
        genres = meta.get('genres', [])
        if genres:
            for g in genres:
                SubElement(root, 'genre').text = g.get('name', '')
        # Premiered/year
        premiered = meta.get('first_air_date', '')
        SubElement(root, 'premiered').text = premiered
        SubElement(root, 'year').text = premiered[:4] if premiered else ''
        SubElement(root, 'status').text = meta.get('status', '')
        SubElement(root, 'code').text = ''
        SubElement(root, 'aired').text = ''
        # Studios
        studios = meta.get('networks', [])
        for s in studios:
            SubElement(root, 'studio').text = s.get('name', '')
        # Trailer (placeholder)
        SubElement(root, 'trailer').text = ''
        # Actors
        cast = meta.get('credits', {}).get('cast', [])
        for idx, actor in enumerate(cast):
            actor_el = SubElement(root, 'actor')
            SubElement(actor_el, 'name').text = actor.get('name', '')
            SubElement(actor_el, 'role').text = actor.get('character', '')
            SubElement(actor_el, 'order').text = str(idx)
            if actor.get('profile_path'):
                SubElement(actor_el, 'thumb').text = f"https://image.tmdb.org/t/p/original{actor['profile_path']}"
        # Named seasons
        for season in meta.get('seasons', []):
            if season.get('season_number') and season.get('name'):
                namedseason = SubElement(root, 'namedseason')
                namedseason.set('number', str(season['season_number']))
                namedseason.text = season['name']
        # Resume
        resume = SubElement(root, 'resume')
        SubElement(resume, 'position').text = '0.000000'
        SubElement(resume, 'total').text = '0.000000'
        # Date added (placeholder: current date)
        from datetime import datetime
        SubElement(root, 'dateadded').text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' + tostring(root, encoding='utf-8').decode()

def episode_meta_to_nfo(meta, season=None, episode=None, show_meta=None):
    from xml.etree.ElementTree import Element, SubElement, tostring
    from datetime import datetime

    meta = remove_non_ascii(meta)
    if show_meta:
        show_meta = remove_non_ascii(show_meta)
    root = Element('episodedetails')
    SubElement(root, 'title').text = meta.get('name', '') or meta.get('title', '')
    # Show title from show_meta if available
    showtitle = ''
    if show_meta and show_meta.get('name'):
        showtitle = show_meta.get('name')
    elif meta.get('show', {}).get('name'):
        showtitle = meta['show']['name']
    SubElement(root, 'showtitle').text = showtitle
    # Ratings
    ratings = SubElement(root, 'ratings')
    imdb_id = meta.get('external_ids', {}).get('imdb_id')
    tmdb_id = meta.get('id')
    tvdb_id = meta.get('external_ids', {}).get('tvdb_id')
    # IMDB rating (if available)
    rating_imdb = SubElement(ratings, 'rating', name='imdb', max='10', default='true')
    SubElement(rating_imdb, 'value').text = str(meta.get('vote_average', ''))
    SubElement(rating_imdb, 'votes').text = str(meta.get('vote_count', ''))
    # TMDB rating
    rating_tmdb = SubElement(ratings, 'rating', name='tmdb', max='10')
    SubElement(rating_tmdb, 'value').text = str(meta.get('vote_average', ''))
    SubElement(rating_tmdb, 'votes').text = str(meta.get('vote_count', ''))
    # Trakt rating (placeholder)
    rating_trakt = SubElement(ratings, 'rating', name='trakt', max='10')
    SubElement(rating_trakt, 'value').text = ''
    SubElement(rating_trakt, 'votes').text = ''
    SubElement(root, 'userrating').text = '0'
    SubElement(root, 'top250').text = '0'
    if season is not None:
        SubElement(root, 'season').text = str(season)
    if episode is not None:
        SubElement(root, 'episode').text = str(episode)
    SubElement(root, 'displayseason').text = '-1'
    SubElement(root, 'displayepisode').text = '-1'
    SubElement(root, 'outline').text = ''
    SubElement(root, 'plot').text = meta.get('overview', '')
    SubElement(root, 'tagline').text = ''
    # Runtime (in minutes)
    runtime = meta.get('runtime') or meta.get('episode_run_time') or 0
    if isinstance(runtime, list):
        runtime = runtime[0] if runtime else 0
    SubElement(root, 'runtime').text = str(runtime)
    # Thumbs (main images)
    thumbs = []
    if meta.get('still_path'):
        thumbs.append(meta['still_path'])
    if meta.get('images'):
        thumbs += [img['file_path'] for img in meta['images'].get('stills', []) if img.get('file_path')]
    for t in thumbs[:2]:
        SubElement(root, 'thumb', spoof='', cache='', aspect='thumb', preview=f"https://image.tmdb.org/t/p/w780{t}").text = f"https://image.tmdb.org/t/p/original{t}"
    # MPAA/content rating
    mpaa = ''
    for rel in meta.get('content_ratings', {}).get('results', []):
        if rel.get('iso_3166_1') == 'US' and rel.get('rating'):
            mpaa = f"US:{rel['rating']}"
            break
        elif rel.get('iso_3166_1') and rel.get('rating'):
            mpaa = f"{rel['iso_3166_1']}:{rel['rating']}"
    SubElement(root, 'mpaa').text = mpaa
    SubElement(root, 'playcount').text = '0'
    SubElement(root, 'lastplayed').text = ''
    SubElement(root, 'id').text = str(tmdb_id or '')
    # Unique IDs
    if imdb_id:
        SubElement(root, 'uniqueid', type='imdb').text = imdb_id
    if tmdb_id:
        SubElement(root, 'uniqueid', type='tmdb', default='true').text = str(tmdb_id)
    if tvdb_id:
        SubElement(root, 'uniqueid', type='tvdb').text = str(tvdb_id)
    # Genres (from show_meta if available)
    genres = []
    if show_meta and show_meta.get('genres'):
        genres = [g.get('name', '') for g in show_meta['genres']]
    for g in genres:
        SubElement(root, 'genre').text = g
    # Credits (writers)
    writers = meta.get('crew', [])
    credits = [w.get('name') for w in writers if w.get('job', '').lower() == 'writer']
    for w in credits:
        SubElement(root, 'credits').text = w
    # Directors
    directors = [d.get('name') for d in writers if d.get('job', '').lower() == 'director']
    for d in directors:
        SubElement(root, 'director').text = d
    # Premiered/year
    premiered = meta.get('air_date', '') or meta.get('first_air_date', '')
    SubElement(root, 'premiered').text = premiered
    SubElement(root, 'year').text = premiered[:4] if premiered else ''
    SubElement(root, 'status').text = ''
    SubElement(root, 'code').text = ''
    SubElement(root, 'aired').text = premiered
    # Studio (from show_meta if available)
    studios = []
    if show_meta and show_meta.get('networks'):
        studios = [s.get('name', '') for s in show_meta['networks']]
    for s in studios:
        SubElement(root, 'studio').text = s
    # Trailer (placeholder)
    SubElement(root, 'trailer').text = ''
    # Fileinfo (placeholder, no streamdetails)
    fileinfo = SubElement(root, 'fileinfo')
    streamdetails = SubElement(fileinfo, 'streamdetails')
    video = SubElement(streamdetails, 'video')
    SubElement(video, 'codec').text = ''
    SubElement(video, 'aspect').text = ''
    SubElement(video, 'width').text = ''
    SubElement(video, 'height').text = ''
    SubElement(video, 'durationinseconds').text = ''
    SubElement(video, 'stereomode').text = ''
    audio = SubElement(streamdetails, 'audio')
    SubElement(audio, 'codec').text = ''
    SubElement(audio, 'language').text = ''
    SubElement(audio, 'channels').text = ''
    # Actors
    cast = meta.get('guest_stars', []) or meta.get('credits', {}).get('cast', [])
    for idx, actor in enumerate(cast):
        actor_el = SubElement(root, 'actor')
        SubElement(actor_el, 'name').text = actor.get('name', '')
        SubElement(actor_el, 'role').text = actor.get('character', '')
        SubElement(actor_el, 'order').text = str(idx)
        if actor.get('profile_path'):
            SubElement(actor_el, 'thumb').text = f"https://image.tmdb.org/t/p/original{actor['profile_path']}"
    # Resume
    resume = SubElement(root, 'resume')
    SubElement(resume, 'position').text = '0.000000'
    SubElement(resume, 'total').text = '0.000000'
    # Date added (placeholder: current date)
    SubElement(root, 'dateadded').text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n' + tostring(root, encoding='utf-8').decode()

def get_metadata_filename(meta, entry_type, original_title):
    if entry_type == 'movie':
        title = meta.get('title', '')
        release_year = meta.get('release_date', '')[:4] if meta.get('release_date') else ''
    else:
        title = meta.get('name', '')
        release_year = meta.get('first_air_date', '')[:4] if meta.get('first_air_date') else ''
    original_clean_title, _ = extract_title_and_year(original_title)
    is_year_title = is_title_a_year(original_clean_title)
    if is_year_title and title and release_year:
        if title.isdigit() and len(title) == 4:
            if title != release_year:
                return sanitize(f"{title} {release_year}")
            else:
                return sanitize(title)
        else:
            if original_clean_title != release_year:
                return sanitize(f"{original_clean_title} {release_year}")
            else:
                return sanitize(original_clean_title)
    if title and release_year:
        return sanitize(f"{title} ({release_year})")
    elif title:
        return sanitize(title)
    return None

def extract_season_episode(title):
    patterns = [
        r"[Ss](\d{1,2})[\. _-]?[Ee](\d{1,2})",
        r"(\d{1,2})x(\d{2})",
        r"[Ss](\d{1,2})[ _.-]?Ep?\.?(\d{1,2})",
        r"Season[ _]?(\d{1,2})[ _\-]+Ep(isode)?[ _]?(\d{1,2})"
    ]
    for pat in patterns:
        m = re.search(pat, title)
        if m:
            try:
                if len(m.groups()) == 2:
                    return int(m.group(1)), int(m.group(2))
                if len(m.groups()) == 3:
                    return int(m.group(1)), int(m.group(3))
            except Exception:
                continue
    return None, None

def format_season_folder(season_num):
    return f"Season {season_num:02d}"

def kodi_tv_episode_filename(showname, year, season, episode, ext, tmdb_id=''):
    base = f"{showname}"
    if year:
        base += f" ({year})"
    if tmdb_id:
        base += f" {{tmdb={tmdb_id}}}"
    base += f" S{season:02d}E{episode:02d}.{ext}"
    return base

async def process_batch(batch, directory, aio_sess, sem, file_sem):
    ensure_dir(directory)
    tasks = [handle_entry(e, directory, aio_sess, sem, file_sem) for e in batch]
    await asyncio.gather(*tasks)

async def handle_entry(entry, directory, aio_sess, sem, file_sem):
    if not entry['title'].strip() or entry['safe'] == 'Unknown':
        log_to_kodi(f"Skipping entry with invalid title: {entry}")
        return

    if entry['type'] != 'tv':
        ensure_dir(directory)
        meta = await fetch_movie_metadata(entry['title'], aio_sess, sem)
        if meta and (meta.get('title') or meta.get('name')):
            filename = get_metadata_filename(meta, entry['type'], entry['original_title'])
            if not filename:
                filename = entry['safe']
            log_to_kodi(f"Using metadata-based filename: {filename}")
        else:
            filename = entry['safe']
            log_to_kodi(f"Using fallback filename: {filename}")
        strm_path = os.path.join(directory, f"{filename}.strm")
        nfo_path = os.path.join(directory, f"{filename}.nfo")
        # Only create files if they do not exist
        if not os.path.exists(strm_path):
            try:
                async with file_sem:
                    async with aiofiles.open(strm_path, 'w') as f:
                        await f.write(entry['url'])
                        log_to_kodi(f"Wrote STRM file: {strm_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write STRM file at {strm_path}: {e}")
                return
        else:
            log_to_kodi(f"STRM file already exists, skipping: {strm_path}")
        if meta and not os.path.exists(nfo_path):
            try:
                async with file_sem:
                    async with aiofiles.open(nfo_path, 'w') as f:
                        await f.write(meta_to_nfo(meta, entry['type']))
                        log_to_kodi(f"Wrote NFO file: {nfo_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write NFO file at {nfo_path}: {e}")
        elif meta:
            log_to_kodi(f"NFO file already exists, skipping: {nfo_path}")
        return

    meta = await fetch_tv_metadata(entry['title'], aio_sess, sem)
    if meta and meta.get('name'):
        show_name = sanitize(meta['name'])
        year = (meta.get('first_air_date', '')[:4] if meta.get('first_air_date') else '')
        tmdb_id = str(meta.get('id', ''))
        log_to_kodi(f"Using TMDB metadata show_name/year: {show_name}/{year}")
    else:
        # Fallback: extract show name and year from the title, but remove season/episode info
        show_name, year = extract_title_and_year(entry['title'])
        show_name = sanitize(show_name)
        # Remove trailing Sxx Exx or similar patterns from show_name
        show_name = re.sub(r'[ \-]*[Ss]\d{1,2}[ \-]*[Ee]\d{1,2}.*$', '', show_name).strip()
        tmdb_id = ''
        log_to_kodi(f"TMDB metadata not found. Using fallback show_name/year: {show_name}/{year}")

    season, episode = extract_season_episode(entry['original_title'])
    if not (season and episode):
        season, episode = extract_season_episode(entry['safe'])

    if year:
        show_folder = os.path.join(TVSHOWS_DIR, f"{show_name} ({year})")
    else:
        show_folder = os.path.join(TVSHOWS_DIR, show_name)

    # Write tvshow.nfo at the show folder level if meta is available
    if meta and not os.path.exists(os.path.join(show_folder, 'tvshow.nfo')):
        try:
            async with file_sem:
                async with aiofiles.open(os.path.join(show_folder, 'tvshow.nfo'), 'w') as f:
                    await f.write(meta_to_nfo(meta, 'tv'))
                    log_to_kodi(f"Wrote TV show NFO file: {os.path.join(show_folder, 'tvshow.nfo')}")
        except Exception as e:
            log_to_kodi(f"Failed to write TV show NFO file at {os.path.join(show_folder, 'tvshow.nfo')}: {e}")
    elif meta:
        log_to_kodi(f"TV show NFO file already exists, skipping: {os.path.join(show_folder, 'tvshow.nfo')}")

    if season and episode:
        season_folder = os.path.join(show_folder, f"Season {season:02d}")
        os.makedirs(season_folder, exist_ok=True)
        episode_filename = f"{show_name} S{season:02d}E{episode:02d}.strm"
        episode_filename = sanitize(episode_filename)
        strm_path = os.path.join(season_folder, episode_filename)
        nfo_filename = os.path.splitext(episode_filename)[0] + ".nfo"
        nfo_path = os.path.join(season_folder, nfo_filename)
        # Only create files if they do not exist
        if not os.path.exists(strm_path):
            try:
                async with file_sem:
                    async with aiofiles.open(strm_path, 'w') as f:
                        await f.write(entry['url'])
                    log_to_kodi(f"Wrote STRM file: {strm_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write STRM file at {strm_path}: {e}")
        else:
            log_to_kodi(f"STRM file already exists, skipping: {strm_path}")
        if meta and not os.path.exists(nfo_path):
            try:
                async with file_sem:
                    async with aiofiles.open(nfo_path, 'w') as f:
                        await f.write(episode_meta_to_nfo(meta, season, episode, meta))
                    log_to_kodi(f"Wrote NFO file: {nfo_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write NFO file at {nfo_path}: {e}")
        elif meta:
            log_to_kodi(f"NFO file already exists, skipping: {nfo_path}")
        if season and episode and meta and meta.get('id'):
            episode_meta = await fetch_episode_metadata(meta['id'], season, episode, aio_sess, sem)
            episode_nfo_path = os.path.join(season_folder, f"{os.path.splitext(episode_filename)[0]}.nfo")
            if episode_meta and not os.path.exists(episode_nfo_path):
                try:
                    async with file_sem:
                        async with aiofiles.open(episode_nfo_path, 'w') as f:
                            await f.write(episode_meta_to_nfo(episode_meta, season, episode, meta))
                        log_to_kodi(f"Wrote episode NFO file: {episode_nfo_path}")
                except Exception as e:
                    log_to_kodi(f"Failed to write episode NFO file at {episode_nfo_path}: {e}")
            elif episode_meta:
                log_to_kodi(f"Episode NFO file already exists, skipping: {episode_nfo_path}")
    else:
        os.makedirs(show_folder, exist_ok=True)
        fallback_fn = sanitize(show_folder)
        strm_path = os.path.join(show_folder, f"{fallback_fn}.strm")
        nfo_path = os.path.join(show_folder, f"{fallback_fn}.nfo")
        if not os.path.exists(strm_path):
            try:
                async with file_sem:
                    async with aiofiles.open(strm_path, 'w') as f:
                        await f.write(entry['url'])
                    log_to_kodi(f"Wrote fallback STRM file: {strm_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write fallback STRM file at {strm_path}: {e}")
        else:
            log_to_kodi(f"Fallback STRM file already exists, skipping: {strm_path}")
        if meta and not os.path.exists(nfo_path):
            try:
                async with file_sem:
                    async with aiofiles.open(nfo_path, 'w') as f:
                        await f.write(meta_to_nfo(meta, "tv"))
                    log_to_kodi(f"Wrote fallback NFO file: {nfo_path}")
            except Exception as e:
                log_to_kodi(f"Failed to write fallback NFO file at {nfo_path}: {e}")
        elif meta:
            log_to_kodi(f"Fallback NFO file already exists, skipping: {nfo_path}")

async def update_library():
    xbmc.executebuiltin('UpdateLibrary(video)')

async def main():
    start_time = time.time()
    xbmcgui.Dialog().notification('m3utostrm', 'Script started', xbmcgui.NOTIFICATION_INFO)
    log_to_kodi("=== m3utostrm script started ===")
    series_data, vod_data = load_json_data()
    if not series_data and not vod_data:
        log_to_kodi("Failed to load or fetch Series and VOD data. Aborting.")
        xbmcgui.Dialog().notification('Error', 'Failed to load Series and VOD data', xbmcgui.NOTIFICATION_ERROR)
        return
    xbmcgui.Dialog().notification('m3utostrm', 'Loading and parsing M3U playlist...', xbmcgui.NOTIFICATION_INFO)
    entries = await fetch_m3u()
    xbmcgui.Dialog().notification('m3utostrm', 'Filtering entries by JSON data...', xbmcgui.NOTIFICATION_INFO)
    filtered_entries = filter_entries_by_json(entries, series_data, vod_data)
    xbmcgui.Dialog().notification('m3utostrm', 'Filtering out existing files...', xbmcgui.NOTIFICATION_INFO)
    filtered_entries = filter_entries_that_exist(filtered_entries, MOVIES_DIR, TVSHOWS_DIR)
    log_to_kodi(f"After filtering with JSON data & file existence, {len(filtered_entries)} entries will be processed.")
    if not filtered_entries:
        log_to_kodi("No valid entries found that match Series and VOD JSON data and aren't already present.")
        xbmcgui.Dialog().notification('M3U Info', 'No new entries found to add.', xbmcgui.NOTIFICATION_INFO)
        return
    movies = [e for e in filtered_entries if e['type'] == 'movie']
    tvs = [e for e in filtered_entries if e['type'] == 'tv']
    log_to_kodi(f"Found {len(movies)} movies and {len(tvs)} TV shows to process")
    xbmcgui.Dialog().notification('m3utostrm', f'Processing {len(movies)} movies and {len(tvs)} TV shows...', xbmcgui.NOTIFICATION_INFO)
    connector = aiohttp.TCPConnector(limit=TCP_LIMIT)
    sem = Semaphore(SEM_LIMIT)
    file_sem = Semaphore(FILE_SEM_LIMIT)
    added_files = 0
    removed_files = 0
    # Track added files before and after
    before_movie_files = set(get_existing_movie_filenames(MOVIES_DIR))
    before_tv_files = set(get_existing_tv_filenames(TVSHOWS_DIR))
    async with aiohttp.ClientSession(connector=connector) as aio_sess:
        for batch in [movies[i:i+CHUNK_SIZE] for i in range(0, len(movies), CHUNK_SIZE)]:
            await process_batch(batch, MOVIES_DIR, aio_sess, sem, file_sem)
        for batch in [tvs[i:i+CHUNK_SIZE] for i in range(0, len(tvs), CHUNK_SIZE)]:
            await process_batch(batch, TVSHOWS_DIR, aio_sess, sem, file_sem)
    await update_library()
    # Count added files
    after_movie_files = set(get_existing_movie_filenames(MOVIES_DIR))
    after_tv_files = set(get_existing_tv_filenames(TVSHOWS_DIR))
    added_movies = len(after_movie_files - before_movie_files)
    added_tvs = len(after_tv_files - before_tv_files)
    added_files = added_movies + added_tvs
    # For deletions, you may want to track files before and after deletion logic if implemented
    # For now, assume removed_files = 0 unless you add deletion logic
    end_time = time.time()
    runtime = end_time - start_time
    xbmcgui.Dialog().notification(
        'm3utostrm',
        f'Added {added_movies} movies, {added_tvs} TV shows. See log for details.',
        xbmcgui.NOTIFICATION_INFO
    )
    log_to_kodi(f"Summary: Added {added_movies} new movies, {added_tvs} new TV shows.")
    log_to_kodi(f"Total new files added: {added_files}")
    log_to_kodi(f"Total files removed: {removed_files}")
    log_to_kodi(f"Total runtime: {runtime:.2f} seconds")

asyncio.run(main())
