# The "what do you want to do with this title" flow, shared by the plugin, the context menu and TMDb Helper.
import json
import re

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from resources.lib import api

ADDON = xbmcaddon.Addon('plugin.video.seerrhub')
NAME = 'Seerr Hub'
PLAYING_PROP = 'seerrhub.playing'  # set just before playing a download, read by service.py


def log(msg):
    xbmc.log('[seerrhub] %s' % msg, xbmc.LOGINFO)


def notify(msg, icon=xbmcgui.NOTIFICATION_INFO):
    xbmcgui.Dialog().notification(NAME, msg, icon, 4000)


def seerr():
    url, key = ADDON.getSetting('seerr_url'), ADDON.getSetting('api_key')
    if not url or not key:
        xbmcgui.Dialog().ok(NAME, 'Set the Seerr address and API key in the add-on settings first.')
        ADDON.openSettings()
        url, key = ADDON.getSetting('seerr_url'), ADDON.getSetting('api_key')
    return api.Seerr(url, key)


def configured():
    return bool(ADDON.getSetting('seerr_url') and ADDON.getSetting('api_key'))


def client_settings():
    """(client class, address, user, password) of the torrent client, or None if there isn't one."""
    if ADDON.getSetting('dl_url'):
        kind = number(ADDON.getSetting('dl_client')) or 0
        return (api.CLIENTS[kind if kind < len(api.CLIENTS) else 0], ADDON.getSetting('dl_url'),
                ADDON.getSetting('dl_user'), ADDON.getSetting('dl_pass'))
    if ADDON.getSetting('qbit_url'):  # 2.0.x only had qBittorrent
        return api.QBit, ADDON.getSetting('qbit_url'), ADDON.getSetting('qbit_user'), ADDON.getSetting('qbit_pass')
    return None


def has_client():
    """A torrent client Seerr Hub can talk to: needed to watch while it downloads."""
    return client_settings() is not None


def client(s=None):
    """The torrent client, or (any other client, or none set up) what Radarr/Sonarr's queue says."""
    c = client_settings()
    if c is None:
        return api.ArrQueue(s or seerr())
    cls, url, user, password = c
    return cls(url, user, password)


def public_settings(s=None):
    """Seerr's public settings, kept for the Kodi session (they only change when Seerr is reconfigured)."""
    win, url = xbmcgui.Window(10000), ADDON.getSetting('seerr_url')
    try:
        cached = json.loads(win.getProperty('seerrhub.public') or '{}')
    except ValueError:
        cached = {}
    if cached.get('url') == url:
        return cached['settings']
    if not s and not (url and ADDON.getSetting('api_key')):  # not set up yet (and the service must never ask)
        return {}
    try:
        settings = (s or seerr()).public()
    except api.SeerrError as e:
        log('public settings: %s' % e)
        return {}
    keep = {k: settings.get(k) for k in ('mediaServerType', 'streamingRegion', 'discoverRegion', 'applicationTitle')}
    win.setProperty('seerrhub.public', json.dumps({'url': url, 'settings': keep}))
    return keep


def region(s=None):
    pub = {} if ADDON.getSetting('region') else public_settings(s)
    return (ADDON.getSetting('region') or pub.get('streamingRegion') or pub.get('discoverRegion') or 'US').upper()


SERVERS = {1: 'plex', 2: 'jellyfin', 3: 'emby'}  # Seerr's mediaServerType
SERVER_NAMES = {'plex': 'Plex', 'jellyfin': 'Jellyfin', 'emby': 'Emby'}
SERVER_APPS = {'plex': 'com.plexapp.android', 'jellyfin': 'org.jellyfin.androidtv', 'emby': 'tv.emby.embyatv'}


def server_kind(s=None):
    """'plex', 'jellyfin', 'emby' or None: the setting, or whatever Seerr is connected to."""
    choice = ADDON.getSetting('media_server') or '0'
    if choice != '0':
        return {'1': 'plex', '2': 'jellyfin', '3': 'emby'}.get(choice)
    return SERVERS.get(public_settings(s).get('mediaServerType'))


def server_name(s=None):
    return SERVER_NAMES.get(server_kind(s))


def status_label(status, s=None):
    return api.status_label(status, server_name(s))


def is_android():
    return xbmc.getCondVisibility('System.Platform.Android')


def installed_apps():
    """Package names of the installed Android apps; None anywhere apps can't be opened from Kodi."""
    if not is_android():
        return None
    try:
        dirs, files = xbmcvfs.listdir('androidapp://sources/apps/')
        return set(d.rstrip('/') for d in dirs + files)
    except Exception:
        return set()


def number(v):
    """int from a URL argument, None when missing or not a number."""
    return int(v) if v is not None and str(v).isdigit() else None


def rpc(method, params=None):
    r = json.loads(xbmc.executeJSONRPC(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}})))
    return r.get('result') or {}


def find_in_library(media_type, tmdb_id, title):
    """Kodi library id of this title (filled by PlexKodiConnect, Jellyfin for Kodi...), matched on the TMDb id."""
    if media_type == 'movie':
        method, key, idkey = 'VideoLibrary.GetMovies', 'movies', 'movieid'
    else:
        method, key, idkey = 'VideoLibrary.GetTVShows', 'tvshows', 'tvshowid'
    res = rpc(method, {'properties': ['uniqueid', 'title'],
                       'filter': {'field': 'title', 'operator': 'contains', 'value': title[:40]}})
    for item in res.get(key, []):
        if str(item.get('uniqueid', {}).get('tmdb')) == str(tmdb_id):
            return item[idkey]
    for item in res.get(key, []):  # some scrapers only give imdb/tvdb ids: fall back to an exact title match
        if item['title'].lower() == title.lower():
            return item[idkey]
    return None


def find_episode(tvshowid, season, episode):
    """Kodi library id of one episode of a synced show, or None."""
    res = rpc('VideoLibrary.GetEpisodes', {'tvshowid': tvshowid, 'season': season, 'properties': ['episode']})
    return next((e['episodeid'] for e in res.get('episodes', []) if e.get('episode') == episode), None)


def launch_app(package, uri):
    log('launch %s %s' % (package, uri))
    if uri:
        xbmc.executebuiltin('StartAndroidActivity("%s","android.intent.action.VIEW","","%s")' % (package, uri.replace('"', '%22')))
    else:
        xbmc.executebuiltin('StartAndroidActivity("%s")' % package)


def request(s, media_type, tmdb_id, title, details):
    if media_type == 'movie':
        if not xbmcgui.Dialog().yesno(NAME, 'Request "%s" in Seerr?' % title):
            return
        s.request('movie', tmdb_id)
    else:
        have = {x['seasonNumber']: x['status'] for x in (details.get('mediaInfo') or {}).get('seasons', [])}
        for r in (details.get('mediaInfo') or {}).get('requests', []):
            for x in r.get('seasons', []):
                have.setdefault(x['seasonNumber'], api.PENDING)
        seasons = [x for x in details.get('seasons', []) if x['seasonNumber'] > 0]
        missing = [x for x in seasons if have.get(x['seasonNumber'], api.UNKNOWN) == api.UNKNOWN]
        if not missing:
            notify('Every season is already available or requested')
            return
        labels = ['Season %d  (%d episodes)' % (x['seasonNumber'], x.get('episodeCount') or 0) for x in missing]
        picked = xbmcgui.Dialog().multiselect('Request which seasons of %s?' % title, labels,
                                              preselect=list(range(len(missing))))
        if not picked:
            return
        s.request('tv', tmdb_id, [missing[i]['seasonNumber'] for i in picked])
    notify('Requested %s' % title)


def open_title(media_type, tmdb_id, title=None, season=None, episode=None):
    """Show where the title can be watched and what can be done with it, then do it.
    For shows, season/episode (when the item was an episode) go straight to that episode."""
    media_type = 'tv' if media_type in ('tv', 'tvshow', 'show', 'season', 'episode') else 'movie'
    if media_type == 'movie' or not season:  # season 0 (specials) isn't handled: treat it as the whole show
        season = episode = None
    ep_tag = (' S%02d' % season + ('E%02d' % episode if episode else '')) if season else ''
    s = seerr()
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        d = s.details(media_type, tmdb_id)
    except api.SeerrError as e:
        xbmcgui.Dialog().ok(NAME, str(e))
        return
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    title = d.get('title') or d.get('name') or title or ''
    status = (d.get('mediaInfo') or {}).get('status') or api.UNKNOWN

    actions = []
    apps = installed_apps()
    server = server_kind(s)
    where = SERVER_NAMES.get(server) or 'your library'
    if status in (api.PARTIAL, api.AVAILABLE):
        dbid = find_in_library(media_type, tmdb_id, title)
        epid = find_episode(dbid, season, episode) if dbid and episode else None
        if epid:
            actions.append(('Play%s from %s' % (ep_tag, where), ('library_ep', epid)))
        if dbid:
            actions.append(('Play from %s' % where if media_type == 'movie' else 'Open in %s' % where, ('library', dbid)))
        elif apps and SERVER_APPS.get(server) in apps:
            actions.append(('In %s - not in Kodi\'s library yet (open the %s app)' % (where, where),
                            ('app', (SERVER_APPS[server], None))))
        else:
            actions.append(('In %s - not in Kodi\'s library yet' % where, ('none', None)))

    if status in (api.PROCESSING, api.PARTIAL):
        try:
            cl = client(s)
            rows = active_downloads(s, cl, media_type, tmdb_id)
            if episode:  # this episode's download first
                rows.sort(key=lambda row: (row[1].get('season'), row[1].get('episode')) != (season, episode))
            for h, q, t in rows[:4]:
                actions.append((download_label(cl, q, t), ('download', (h, q))))
        except api.SeerrError as e:
            log('downloads: %s' % e)

    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        offers = api.where_to_watch(title, tmdb_id, media_type, region(s))
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')
    openable = lambda p: p and apps is not None and (not apps or p in apps)  # empty set: the app list couldn't be read
    for name, package, uri in offers:
        if openable(package):
            actions.append(('Open in %s' % name, ('app', (package, uri))))

    if status in (api.PENDING, api.PROCESSING):
        actions.append(('%s in Seerr (already requested)' % api.STATUS_LABEL[status], ('none', None)))
    if status != api.AVAILABLE or media_type == 'tv':
        actions.append(('Download%s - choose the release...' % ep_tag, ('pick', None)))
    if (media_type == 'tv' and status != api.AVAILABLE) or (media_type == 'movie' and status == api.UNKNOWN):
        actions.append(('Request in Seerr (it picks the release)' if status == api.UNKNOWN else 'Request more seasons in Seerr',
                        ('request', None)))

    elsewhere = [n for n, p, u in offers if not openable(p)]
    heading = title + ep_tag + (' (%s)' % (d.get('releaseDate') or d.get('firstAirDate') or '')[:4] if (d.get('releaseDate') or d.get('firstAirDate')) else '')
    if elsewhere:
        heading += ' - also on ' + ', '.join(elsewhere)
    if not actions:
        xbmcgui.Dialog().ok(NAME, 'Nothing to do for "%s".' % title)
        return
    if not offers and status == api.UNKNOWN:
        heading += ' - not streaming in %s' % region(s)
    choice = xbmcgui.Dialog().select(heading, [a[0] for a in actions])
    if choice < 0:
        return
    kind, arg = actions[choice][1]
    if kind == 'library_ep':
        rpc('Player.Open', {'item': {'episodeid': arg}})
    elif kind == 'library':
        if media_type == 'movie':
            rpc('Player.Open', {'item': {'movieid': arg}})
        else:
            xbmc.executebuiltin('ActivateWindow(Videos,videodb://tvshows/titles/%d/,return)' % arg)
    elif kind == 'app':
        launch_app(*arg)
    elif kind == 'download':
        watch_download(*arg)
    elif kind == 'pick':
        try:
            choose_release(s, media_type, tmdb_id, title, d, season, episode)
        except api.SeerrError as e:
            xbmcgui.Dialog().ok(NAME, str(e))
    elif kind == 'request':
        try:
            request(s, media_type, tmdb_id, title, d)
        except api.SeerrError as e:
            xbmcgui.Dialog().ok(NAME, str(e))


def open_by_title(media_type, title, year=None):
    """For items without a TMDb id: look the title up in Seerr first."""
    want = 'tv' if media_type in ('tv', 'tvshow', 'season', 'episode') else 'movie'
    try:
        results = seerr().search(title).get('results', [])
    except api.SeerrError as e:
        xbmcgui.Dialog().ok(NAME, str(e))
        return
    results = [r for r in results if r.get('mediaType') == want]
    if year:
        dated = [r for r in results if str(r.get('releaseDate') or r.get('firstAirDate') or '').startswith(str(year))]
        results = dated or results
    if not results:
        notify('"%s" not found in Seerr' % title)
        return
    open_title(want, results[0]['id'], title)


# ---- choosing the release yourself ----

ANIME_KEYWORD = 210024  # TMDb "anime" keyword: Seerr sends these to the anime profile and folder


def busy(fn, *a, **kw):
    xbmc.executebuiltin('ActivateWindow(busydialognocancel)')
    try:
        return fn(*a, **kw)
    finally:
        xbmc.executebuiltin('Dialog.Close(busydialognocancel)')


def pick_season(title, d):
    seasons = [x for x in d.get('seasons', []) if x['seasonNumber'] > 0]
    if not seasons:
        raise api.SeerrError('No seasons listed for %s' % title)
    have = {x['seasonNumber']: x['status'] for x in (d.get('mediaInfo') or {}).get('seasons', [])}
    labels = ['Season %d  (%d episodes)%s' % (x['seasonNumber'], x.get('episodeCount') or 0,
                                              ' - %s' % status_label(have[x['seasonNumber']])
                                              if have.get(x['seasonNumber']) in api.STATUS_LABEL else '') for x in seasons]
    first_missing = next((i for i, x in enumerate(seasons) if have.get(x['seasonNumber']) != api.AVAILABLE), 0)
    i = xbmcgui.Dialog().select('Download which season of %s?' % title, labels, preselect=first_missing)
    return seasons[i] if i >= 0 else None


def release_label(r, star, what):
    """One line: Arctic Fuse's two-line select dialog doesn't redraw, so everything has to fit here."""
    seeds = r.get('seeders') or 0
    parts = ([what] if what else []) + [r['quality']['quality']['name'], '%.1f GB' % (r['size'] / 2.0 ** 30),
                                        '%d seeds' % seeds if seeds else '[COLOR red]no seeds[/COLOR]']
    label = ' - '.join(parts)
    if not r['ok']:
        label = 'x ' + label
    if star:
        return '[B][COLOR gold]*[/COLOR] %s[/B]' % label
    return label if r['ok'] else '[COLOR grey]%s[/COLOR]' % label


def release_summary(arr, r):
    lines = [r['title'], '%.1f GB  -  %d seeds, %d leechers  -  %s' % (
        r['size'] / 2.0 ** 30, r.get('seeders') or 0, r.get('leechers') or 0, r['indexer'].replace(' (Prowlarr)', '')),
        'Size: ' + r['why'][0], 'Bitrate: ' + r['why'][1]]
    if r.get('customFormats'):
        lines.append('Formats: %s (+%d)' % (', '.join(f['name'] for f in r['customFormats']), r.get('customFormatScore') or 0))
    if not r['ok']:
        lines.append('[COLOR orange]%s won\'t take it by itself: %s[/COLOR]' % (arr.kind.capitalize(), '; '.join(r['rejections'][:2])))
    return '[CR]'.join(lines)


def choose_release(s, media_type, tmdb_id, title, d, season=None, episode=None):
    """Search every indexer through Radarr/Sonarr, rank the results against the quality profile, size settings,
    seeders and the download cap, and grab the one picked. Adds the title to Radarr/Sonarr first if needed,
    without letting it search by itself."""
    if media_type == 'movie':
        arr, season = busy(api.arr_server, s, 'radarr'), None
        item = busy(arr.movie, tmdb_id)
        runtime = d.get('runtime') or item.get('runtime') or 110
        scope = 'Searching for %s...' % title
        search = {'movieId': item['id']}
        eps = []
    else:
        tvdb = (d.get('externalIds') or {}).get('tvdbId')
        if not tvdb:
            raise api.SeerrError('%s has no TheTVDB id, so Sonarr can\'t add it' % title)
        if not season:  # not started from an episode: ask
            picked = pick_season(title, d)
            if picked is None:
                return
            season = picked['seasonNumber']
        anime = any(k.get('id') == ANIME_KEYWORD for k in d.get('keywords') or [])
        arr = busy(api.arr_server, s, 'sonarr')
        item = busy(arr.series, tvdb, season, anime)
        runtime = (d.get('episodeRunTime') or [None])[0] or item.get('runtime') or 45
        eps = busy(arr.episodes, item['id'], season)
        scope = 'Searching for %s season %d%s...' % (title, season, ' (episode %d first)' % episode if episode else '')
        search = {'seriesId': item['id'], 'seasonNumber': season}

    notify(scope + ' (can take a minute)')
    rels = busy(arr.releases, **search)
    if has_client():  # watching while it downloads needs a torrent
        rels = [r for r in rels if r.get('protocol') == 'torrent']
    if media_type == 'tv':
        rels = [r for r in rels if r.get('seasonNumber') == season]
    if not rels:
        xbmcgui.Dialog().ok(NAME, 'No releases found for %s%s.' % (title, ' season %d' % season if season else ''))
        return
    limits = busy(arr.size_limits, item.get('qualityProfileId'))
    fallback = {}
    if not any(v[1] for v in limits.values()):  # no sizes set: borrow the other server's per-resolution ones
        try:
            fallback = api.arr_server(s, 'radarr' if arr.kind == 'sonarr' else 'sonarr').size_limits()
        except api.SeerrError:
            pass
    try:
        cap = client(s).rate_limit()
    except api.SeerrError:
        cap = 0
    n_eps = max(1, len(eps))

    def minutes(r):
        if media_type == 'movie':
            return runtime
        return runtime * (n_eps if r.get('fullSeason') else max(1, len(r.get('episodeNumbers') or [])))

    ranked = api.score_releases(rels, api.size_reference(limits, fallback), minutes, cap)
    if media_type == 'tv':
        ranked, stars = by_episode(ranked, episode)
    else:
        stars = {0}
    ranked = ranked[:100]

    def what(r):
        if media_type == 'movie':
            return ''
        if r.get('fullSeason'):
            return 'Pack'
        e = r.get('episodeNumbers') or []
        return ''.join('E%02d' % x for x in e[:3]) or 'S%02d' % season
    heading = '%s: %d releases' % (title + (' S%02d' % season if season else ''), len(ranked))
    best = ('best for each episode' if media_type == 'tv' and len(stars) > 1 else 'best pick')
    heading += (' (* %s, x outside profile)' if not any(r['ok'] for r in ranked) else ' (* = %s)') % best
    while True:
        i = xbmcgui.Dialog().select(heading, [release_label(r, n in stars, what(r)) for n, r in enumerate(ranked)])
        if i < 0:
            return
        r = ranked[i]
        if xbmcgui.Dialog().yesno(NAME, release_summary(arr, r), nolabel='Back', yeslabel='Download'):
            break
    override = None
    if not r.get('downloadAllowed', True) or (media_type == 'tv' and not r.get('mappedSeriesId')):
        if media_type == 'movie':
            override = {'movieId': item['id']}
        else:
            nums = set(r.get('episodeNumbers') or [])
            ids = [e['id'] for e in eps if r.get('fullSeason') or e['episodeNumber'] in nums]
            override = {'seriesId': item['id'], 'episodeIds': ids or [e['id'] for e in eps]}
    busy(arr.grab, r, override)
    notify('Sent to %s: %s' % (client(s).name if has_client() else arr.kind.capitalize(), r['title'][:60]))
    if not has_client():
        return
    after_grab(arr, r, {'title': title, 'type': media_type, 'tmdb_id': tmdb_id, 'runtime': runtime,
                        'season': season, 'episode': (r.get('episodeNumbers') or [None])[0]})


def by_episode(ranked, episode=None):
    """Season search results in watching order: season packs, then each episode's releases best first
    (the asked-for episode at the very top). Returns (releases, positions of the best one in each group)."""
    def group(r):
        if r.get('fullSeason'):  # packs lead, unless one episode was asked for: then that episode alone comes first
            return (2, 0) if episode else (0, 0)
        eps = r.get('episodeNumbers') or [999]
        return (1, min(eps))
    first = lambda r: not (episode and (r.get('fullSeason') or episode in (r.get('episodeNumbers') or [])))
    order = sorted(ranked, key=lambda r: (first(r), group(r), not r['ok'], -r['score']))
    stars, seen = set(), set()
    for n, r in enumerate(order):
        g = (first(r), group(r))
        if g not in seen:
            seen.add(g)
            stars.add(n)
    return order, stars


def after_grab(arr, r, q):
    """Find the torrent the grab created (many indexers don't give its hash up front) and offer to watch it."""
    cl = client()
    pd = xbmcgui.DialogProgress()
    pd.create(NAME, 'Waiting for %s to pick it up...' % cl.name)
    h = None
    try:
        for n in range(24):
            if pd.iscanceled():
                return
            try:
                h = (arr.grabbed_hash(r['title'])
                     or next((x['hash'] for x in arr.queue() if api.same_release(x.get('release'), r['title'])), None)
                     or next((t['hash'] for t in cl.torrents() if api.same_release(t['name'], r['title'])), None))
                if h and cl.torrents(h):  # wait until the client actually has it
                    break
            except api.SeerrError as e:
                log('after grab: %s' % e)
            h = None
            pd.update(int(n * 100 / 24))
            if xbmc.Monitor().waitForAbort(2.5):
                return
    finally:
        pd.close()
    if not h:
        notify('Grabbed - it will show under Downloading shortly')
        return
    q = {k: v for k, v in q.items() if v is not None}
    try:
        cl.make_watchable(cl.torrent(h))
    except api.SeerrError:
        pass
    if xbmcgui.Dialog().yesno(NAME, 'Downloading in order now.[CR]Watch it while it downloads?', nolabel='Later', yeslabel='Watch'):
        watch_download(h, q)


# ---- watching while it downloads ----

def active_downloads(s, cl, media_type=None, tmdb_id=None):
    """[(hash, queue entry, torrent)] for unfinished downloads, newest first; optionally only one title's."""
    queue = cl.entries() if isinstance(cl, api.ArrQueue) else api.arr_queue(s)
    out = []
    for t in sorted(cl.incomplete(), key=lambda t: t['added_on'], reverse=True):
        q = dict(queue.get(t['hash'].lower()) or {'title': t['name'], 'type': None, 'tmdb_id': None})
        q.setdefault('torrent', t['name'])
        if tmdb_id and (str(q.get('tmdb_id')) != str(tmdb_id) or q.get('type') != media_type):
            continue
        out.append((t['hash'], q, t))
    return out


def episode_tag(q):
    if q.get('season') is not None and q.get('episode') is not None:
        return ' S%02dE%02d' % (q['season'], q['episode'])
    return ''


def pick_file(files, q, ask=True):
    """The episode's file in a season pack, the only video otherwise; ask (or take the biggest) when unclear."""
    vids = api.video_files(files)
    if not vids:
        return None
    tag = episode_tag(q).strip().lower()
    if tag:
        match = [f for f in vids if tag in re.sub(r'[ ._-]', '', f['name'].lower())]
        if match:
            return match[0]
    if len(vids) == 1 or not ask:
        return max(vids, key=lambda f: f['size'])
    i = xbmcgui.Dialog().select('Which file?', ['%s  (%d%%)' % (f['name'].split('/')[-1], f['progress'] * 100) for f in vids])
    return vids[i] if i >= 0 else None


def runtime_s(q):
    mins = q.get('runtime') or (110 if q.get('type') == 'movie' else 45)
    return mins * 60


def check(qb, h, q, f=None, speed=None):
    """(torrent, file, estimate) right now."""
    t = qb.torrent(h)
    if f is None:
        f = pick_file(qb.files(h), q, ask=False)
    else:
        f = next((x for x in qb.files(h) if x['index'] == f['index']), f)
    if f is None:
        raise api.SeerrError('No video file in this download')
    sp = speed if speed is not None else t['dlspeed']
    return t, f, api.estimate(f, qb.pieces(h), qb.piece_size(h), sp, runtime_s(q))


def verdict(e, t):
    if e['ready']:
        return 'Watch now'
    if t['state'] in ('queued', 'paused', 'checking'):
        return t['state'].capitalize()
    if e['wait'] is None:
        return 'Stalled'
    return 'Ready in ~%s' % api.mins(e['wait'])


def queue_verdict(t):
    """For downloads only known from Radarr/Sonarr's queue: no idea what's playable, just when it's done."""
    if t['state'] in ('queued', 'paused'):
        return t['state'].capitalize()
    left = api.timeleft_s(t.get('timeleft'))
    return 'done in ~%s' % api.mins(left) if left else 'Downloading'


def download_label(cl, q, t):
    if not cl.can_stream:
        return 'Downloading%s: %d%% - %s' % (episode_tag(q), t['progress'] * 100, queue_verdict(t))
    try:
        t, f, e = check(cl, t['hash'], q)
        return 'Downloading%s: %d%% - %s' % (episode_tag(q), t['progress'] * 100, verdict(e, t))
    except api.SeerrError:
        return 'Downloading%s' % episode_tag(q)


def play_path(qb, t, f):
    base = ADDON.getSetting('downloads_path')
    if not base:
        xbmcgui.Dialog().ok(NAME, 'Kodi can\'t see the downloads folder yet.[CR]Share %s\'s download folder ' % qb.name +
                                  '(read-only SMB or NFS is fine) and put its address in Seerr Hub settings, '
                                  'e.g. smb://nas/downloads/')
        return None
    root, save = qb.save_root(), t['save_path'].rstrip('/')
    rel = save[len(root):] if save.startswith(root) else ''
    return base.rstrip('/') + rel + '/' + f['name']


def play(qb, t, f, q):
    path = play_path(qb, t, f)
    if not path:
        return
    q = dict(q)
    m = re.search(r'S(\d{1,2})[ ._-]?E(\d{1,3})', f['name'].split('/')[-1], re.I)
    if q.get('type') == 'tv' and m:  # a file picked out of a season pack: use its own episode number
        q['season'], q['episode'] = int(m.group(1)), int(m.group(2))
    li = xbmcgui.ListItem(q.get('title', '') + episode_tag(q), path=path)
    log('play %s' % path)
    # the service picks this up and later copies watched / resume point to the media server
    xbmcgui.Window(10000).setProperty(PLAYING_PROP, json.dumps({'path': path, 'q': q}))
    xbmc.Player().play(path, li)


def wait_then_play(qb, h, q, f):
    """Poll until the download is safely ahead of playback, then start it."""
    pd = xbmcgui.DialogProgress()
    pd.create(NAME, 'Checking...')
    speed = None
    try:
        while not pd.iscanceled():
            t, f, e = check(qb, h, q, f, None)
            speed = t['dlspeed'] if speed is None else 0.7 * speed + 0.3 * t['dlspeed']  # smooth the speed
            e = api.estimate(f, qb.pieces(h), qb.piece_size(h), speed, e['runtime'])
            if e['ready']:
                pd.close()
                return play(qb, t, f, q)
            pd.update(int(100 * e['have'] / max(1, e['size'])),
                      '%s%s[CR]%s on disk, downloading at %s (needs %s)[CR]%s' % (
                          q.get('title', ''), episode_tag(q), api.mins(e['buffer_s']), api.mbps(speed),
                          api.mbps(e['bitrate'] * api.SAFETY), verdict(e, t) + ' - starts by itself'))
            xbmc.Monitor().waitForAbort(5)
    finally:
        pd.close()


def watch_download(h, q):
    try:
        qb = client()
        t = qb.torrent(h)
        if not qb.can_stream:
            xbmcgui.Dialog().ok(NAME, '%s%s: %d%% - %s.[CR]Watching while it downloads needs qBittorrent, Transmission '
                                      'or Deluge set up in Seerr Hub settings.' % (
                                          q.get('title', ''), episode_tag(q), t['progress'] * 100, queue_verdict(t)))
            return
        changed = qb.make_watchable(t)
        if changed:
            notify('Download set to ' + ' and '.join(changed))
        f = pick_file(qb.files(h), q)
        if f is None:
            return
        while True:
            t, f, e = check(qb, h, q, f)
            title = q.get('title', '') + episode_tag(q)
            heading = '%s - %d%% done' % (title, f['progress'] * 100)
            info = '%s of %s ready to play. Downloading at %s, the file needs %s.' % (
                api.mins(e['buffer_s']), api.mins(e['runtime']), api.mbps(e['speed']), api.mbps(e['bitrate']))
            if e['ready']:
                opts = [('Watch now', 'play'), ('Refresh', 'refresh')]
            else:
                opts = [('Start by itself when it\'s safe (%s)' % verdict(e, t).lower(), 'wait'),
                        ('Watch now anyway (it will pause to buffer)', 'play'), ('Refresh', 'refresh')]
            i = xbmcgui.Dialog().select(heading + ' - ' + info, [o[0] for o in opts])
            if i < 0:
                return
            if opts[i][1] == 'play':
                return play(qb, t, f, q)
            if opts[i][1] == 'wait':
                return wait_then_play(qb, h, q, f)
    except api.SeerrError as e:
        xbmcgui.Dialog().ok(NAME, str(e))
