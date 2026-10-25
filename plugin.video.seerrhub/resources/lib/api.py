# Seerr and JustWatch clients. Standard library only, so the add-on has no dependencies.
import base64
import http.cookiejar
import json
import math
import re
import urllib.error
import urllib.parse
import urllib.request

TMDB_IMG = 'https://image.tmdb.org/t/p/'

# Seerr media status codes
UNKNOWN, PENDING, PROCESSING, PARTIAL, AVAILABLE = 1, 2, 3, 4, 5
STATUS_LABEL = {PENDING: 'Requested', PROCESSING: 'Downloading', PARTIAL: 'Partly available', AVAILABLE: 'Available'}


def status_label(status, server=None):
    """'In Plex' / 'Partly in Jellyfin' when the media server is known, the plain Seerr wording otherwise."""
    if server and status == AVAILABLE:
        return 'In %s' % server
    if server and status == PARTIAL:
        return 'Partly in %s' % server
    return STATUS_LABEL.get(status)


class SeerrError(Exception):
    pass


class Seerr(object):
    def __init__(self, url, api_key):
        self.base = url.rstrip('/') + '/api/v1'
        self.key = api_key

    def _call(self, path, params=None, body=None):
        url = self.base + path
        if params:
            url += '?' + urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={'X-Api-Key': self.key, 'Accept': 'application/json',
                                                              'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                msg = json.load(e).get('message') or e.reason
            except Exception:
                msg = e.reason
            raise SeerrError('Seerr said: %s (%s)' % (msg, e.code))
        except Exception as e:
            raise SeerrError('Cannot reach Seerr: %s' % e)

    def search(self, query, page=1):
        return self._call('/search', {'query': query, 'page': page})

    def trending(self, page=1):
        return self._call('/discover/trending', {'page': page})

    def details(self, media_type, tmdb_id):
        return self._call('/%s/%s' % (media_type, tmdb_id))

    def season(self, tmdb_id, season):
        return self._call('/tv/%s/season/%s' % (tmdb_id, season))

    def requests(self, take=20):
        return self._call('/request', {'take': take, 'skip': 0, 'filter': 'all', 'sort': 'added'})

    def public(self):
        """Seerr's public settings: mediaServerType (1 Plex, 2 Jellyfin, 3 Emby), streamingRegion..."""
        return self._call('/settings/public')

    def request(self, media_type, tmdb_id, seasons=None):
        body = {'mediaType': media_type, 'mediaId': int(tmdb_id)}
        if media_type == 'tv':
            body['seasons'] = seasons or 'all'
        return self._call('/request', body=body)


# JustWatch technical names -> Android TV package, and whether its web URL opens the title page in the app
# (otherwise the Android TV deep link is used).
SERVICES = [
    # (technical name prefix, display name, package, use web url)
    ('netflix', 'Netflix', 'com.netflix.ninja', True),
    ('amazonprimevideo', 'Prime Video', 'com.amazon.amazonvideo.livingroom', True),
    ('disneyplus', 'Disney+', 'com.disney.disneyplus', True),
    ('paramountplus', 'Paramount+', 'com.cbs.ott', True),
    ('hbomax', 'HBO Max', 'com.wbd.stream', True),
    ('max', 'HBO Max', 'com.wbd.stream', True),
    ('hulu', 'Hulu', 'com.hulu.livingroomplus', True),
    ('appletvplus', 'Apple TV+', 'com.apple.atve.androidtv.appletv', True),
    ('peacock', 'Peacock', 'com.peacocktv.peacockandroid', True),
    ('crunchyroll', 'Crunchyroll', 'com.crunchyroll.crunchyroid', True),
    ('mubi', 'MUBI', 'com.mubi', True),
    ('tubi', 'Tubi', 'com.tubitv', True),
    ('plutotv', 'Pluto TV', 'tv.pluto.android', True),
    ('crave', 'Crave', 'ca.bellmedia.cravetv', True),
    ('bbciplayer', 'BBC iPlayer', 'uk.co.bbc.iplayer', False),
    ('itvx', 'ITVX', 'air.ITVMobilePlayer', False),
    # Australia
    ('stan', 'Stan', 'au.com.stan.and', True),
    ('binge', 'Binge', 'au.com.streamotion.ares.tv', True),
    ('kayo', 'Kayo', 'au.com.kayosports.tv', True),
    ('ninenow', '9Now', 'au.com.ninenow.ctv', False),
    ('iview', 'ABC iview', 'au.net.abc.iview', False),
    ('sbs', 'SBS On Demand', 'com.sbs.ondemand.tv', False),
    ('sevenplus', '7plus', 'au.com.seven', False),  # JustWatch calls it sevenplus
    ('tenplay', '10 Play', 'au.com.tenplay', False),
    ('youtube', 'YouTube', 'com.google.android.youtube.tv', True),
]
STREAM_TYPES = ('FLATRATE', 'FREE', 'ADS')

JW_QUERY = '''query S($q:String!,$c:Country!,$l:Language!){popularTitles(country:$c,first:6,filter:{searchQuery:$q}){edges{node{
objectType content(country:$c,language:$l){externalIds{tmdbId}}
offers(country:$c,platform:WEB){monetizationType standardWebURL deeplinkURL(platform:ANDROID_TV) package{technicalName clearName}}}}}}'''


def _intent_to_uri(intent):
    # intent://host/path#Intent;scheme=x;package=y;end -> x://host/path
    if not intent or not intent.startswith('intent://'):
        return None
    rest, _, extra = intent[len('intent://'):].partition('#Intent;')
    scheme = None
    for part in extra.split(';'):
        if part.startswith('scheme='):
            scheme = part[len('scheme='):]
    return '%s://%s' % (scheme, rest) if scheme else None


def where_to_watch(title, tmdb_id, media_type, region):
    """[(display name, package, uri)] for subscription/free offers of this exact title (matched by TMDb id).
    package and uri are None for services with no Android TV app mapped in SERVICES."""
    body = json.dumps({'query': JW_QUERY, 'variables': {'q': title, 'c': region, 'l': 'en'}}).encode()
    req = urllib.request.Request('https://apis.justwatch.com/graphql', data=body,
                                 headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            edges = json.load(r)['data']['popularTitles']['edges']
    except Exception:
        return []
    want = 'MOVIE' if media_type == 'movie' else 'SHOW'
    node = next((e['node'] for e in edges if e['node']['objectType'] == want
                 and str((e['node']['content'] or {}).get('externalIds', {}).get('tmdbId')) == str(tmdb_id)), None)
    if not node:
        return []
    out, seen = [], set()
    for o in node['offers']:
        if o['monetizationType'] not in STREAM_TYPES:
            continue
        tech = o['package']['technicalName']
        svc = next((s for s in SERVICES if tech.startswith(s[0])), None)
        if not svc:  # a service with no app mapped here: still worth naming
            name = o['package']['clearName']
            if 'channel' in name.lower():  # add-on channels inside Prime/Apple TV: noise
                continue
            if name not in seen and not any(name.startswith(n) for n in seen):
                seen.add(name)
                out.append((name, None, None))
            continue
        if svc[2] in seen:
            continue
        uri = o['standardWebURL'] if svc[3] else (_intent_to_uri(o.get('deeplinkURL')) or o['standardWebURL'])
        if uri:
            uri = uri.replace('&amp;', '&')
        seen.add(svc[2])
        seen.add(svc[1])
        out.append((svc[1], svc[2], uri))
    return out


def art(item):
    a = {}
    if item.get('posterPath'):
        a['poster'] = a['thumb'] = TMDB_IMG + 'w500' + item['posterPath']
    if item.get('backdropPath'):
        a['fanart'] = TMDB_IMG + 'w1280' + item['backdropPath']
    return a


# ---- download clients ----
# Every client hands back the same shapes, so the rest of the add-on doesn't care which one it is:
#   torrent: {'hash', 'name', 'progress' (0-1), 'dlspeed' (bytes/s), 'state', 'added_on', 'save_path'}
#            state is one of downloading / queued / paused / checking / done
#   file:    {'index', 'name' (path inside the save folder), 'size', 'progress' (0-1), 'piece_range' (first, last)}
#   pieces:  [True/False] per piece: finished or not

def _piece_ranges(files, piece_size):
    """Torrent files sit end to end in piece space, in the order the torrent lists them."""
    offset = 0
    for f in files:
        f['piece_range'] = (offset // piece_size, max(offset, offset + f['size'] - 1) // piece_size)
        offset += f['size']
    return files


class TorrentClient(object):
    name = 'the download client'
    can_stream = True  # can tell which pieces are on disk, so watching while it downloads works

    def incomplete(self):
        return [t for t in self.torrents() if t['progress'] < 1]

    def torrent(self, h):
        t = self.torrents(h)
        if not t:
            raise SeerrError('That download is no longer in %s' % self.name)
        return t[0]


class QBit(TorrentClient):
    """qBittorrent Web API. Logs in only when the server asks for it (it can skip auth for the local network)."""
    name = 'qBittorrent'
    STATES = {'queuedDL': 'queued', 'stoppedDL': 'paused', 'pausedDL': 'paused', 'checkingDL': 'checking',
              'checkingResumeData': 'checking', 'moving': 'checking'}

    def __init__(self, url, user='', password=''):
        self.root = url.rstrip('/')
        self.base = self.root + '/api/v2'
        self.user, self.password = user, password
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _open(self, url, data=None):
        req = urllib.request.Request(url, data=data, headers={'Referer': self.root, 'Origin': self.root})
        with self.opener.open(req, timeout=15) as r:
            return r.read()

    def login(self):
        if not self.user:
            raise SeerrError('qBittorrent wants a login - add its user name and password in Seerr Hub settings')
        try:
            body = self._open(self.base + '/auth/login',
                              urllib.parse.urlencode({'username': self.user, 'password': self.password}).encode())
        except urllib.error.HTTPError as e:
            raise SeerrError('qBittorrent refused the login (%s %s)' % (e.code, e.reason))
        except Exception as e:
            raise SeerrError('Cannot reach qBittorrent: %s' % e)
        if not body.strip().lower().startswith(b'ok'):
            raise SeerrError('qBittorrent refused the login - check the user name and password')

    def _call(self, path, params=None, post=None):
        url = self.base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = urllib.parse.urlencode(post).encode() if post is not None else None
        try:
            try:
                body = self._open(url, data)
            except urllib.error.HTTPError as e:
                if e.code not in (401, 403):
                    raise
                self.login()
                body = self._open(url, data)
        except SeerrError:
            raise
        except Exception as e:
            raise SeerrError('Cannot reach qBittorrent: %s' % e)
        try:
            return json.loads(body)
        except ValueError:
            return body.decode(errors='replace')

    def torrents(self, h=None):
        out = []
        for t in self._call('/torrents/info', {'hashes': h.lower()} if h else None):
            avg = t.get('downloaded', 0) / float(max(1, t.get('time_active') or 0))
            state = 'done' if t['progress'] >= 1 else self.STATES.get(t['state'], 'downloading')
            out.append({'hash': t['hash'].lower(), 'name': t['name'], 'progress': t['progress'],
                        'dlspeed': t['dlspeed'] or (avg if state == 'downloading' else 0), 'state': state,
                        'added_on': t['added_on'], 'save_path': t['save_path'], 'raw': t})
        return out

    def files(self, h):
        return self._call('/torrents/files', {'hash': h})

    def pieces(self, h):
        return [s == 2 for s in self._call('/torrents/pieceStates', {'hash': h})]

    def piece_size(self, h):
        return self._call('/torrents/properties', {'hash': h})['piece_size']

    def rate_limit(self):
        """Download cap in bytes/s, or 0 for none."""
        return max(0, self._call('/transfer/info').get('dl_rate_limit') or 0)

    def save_root(self):
        return self._call('/app/preferences')['save_path'].rstrip('/')

    def make_watchable(self, t):
        """Download in order, first/last pieces first, and don't sit in the queue. Returns what changed."""
        h, raw, done = t['hash'], t['raw'], []
        if not raw['seq_dl']:
            self._call('/torrents/toggleSequentialDownload', post={'hashes': h})
            done.append('in order')
        if not raw['f_l_piece_prio']:
            self._call('/torrents/toggleFirstLastPiecePrio', post={'hashes': h})
        if t['state'] in ('queued', 'paused'):
            try:
                self._call('/torrents/start', post={'hashes': h})  # qBittorrent 5
            except SeerrError:
                self._call('/torrents/resume', post={'hashes': h})  # qBittorrent 4
            self._call('/torrents/setForceStart', post={'hashes': h, 'value': 'true'})
            done.append('started now')
        return done


class Transmission(TorrentClient):
    """Transmission RPC (2.x to 4.x). Downloading in order needs Transmission 4.1 or newer."""
    name = 'Transmission'
    STATES = {0: 'paused', 1: 'checking', 2: 'checking', 3: 'queued', 4: 'downloading'}

    def __init__(self, url, user='', password=''):
        url = url.rstrip('/')
        self.url = url if url.endswith('/rpc') else url + '/transmission/rpc'
        self.headers = {'Content-Type': 'application/json', 'X-Transmission-Session-Id': ''}
        if user or password:
            self.headers['Authorization'] = 'Basic ' + base64.b64encode(('%s:%s' % (user, password)).encode()).decode()

    def _call(self, method, args=None):
        body = json.dumps({'method': method, 'arguments': args or {}}).encode()
        try:
            for _ in range(2):  # the first call is answered 409 with the session id to use
                try:
                    with urllib.request.urlopen(urllib.request.Request(self.url, body, self.headers), timeout=15) as r:
                        out = json.load(r)
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 409:
                        self.headers['X-Transmission-Session-Id'] = e.headers.get('X-Transmission-Session-Id', '')
                        continue
                    if e.code in (401, 403):
                        raise SeerrError('Transmission refused the login - check the user name and password '
                                         '(or its rpc-whitelist)')
                    raise
        except SeerrError:
            raise
        except Exception as e:
            raise SeerrError('Cannot reach Transmission: %s' % e)
        if out.get('result') != 'success':
            raise SeerrError('Transmission said: %s' % out.get('result'))
        return out.get('arguments', {})

    def _get(self, h, fields):
        args = {'fields': fields}
        if h:
            args['ids'] = [h.lower()]
        return self._call('torrent-get', args)['torrents']

    def torrents(self, h=None):
        out = []
        for t in self._get(h, ['hashString', 'name', 'percentDone', 'rateDownload', 'status', 'addedDate', 'downloadDir',
                               'sequential_download']):
            state = 'done' if t['percentDone'] >= 1 else self.STATES.get(t['status'], 'downloading')
            out.append({'hash': t['hashString'].lower(), 'name': t['name'], 'progress': t['percentDone'],
                        'dlspeed': t['rateDownload'], 'state': state, 'added_on': t['addedDate'],
                        'save_path': t['downloadDir'], 'raw': t})
        return out

    def files(self, h):
        t = self._get(h, ['files', 'pieceSize'])[0]
        files = [{'index': i, 'name': f['name'], 'size': f['length'], 'progress': f['bytesCompleted'] / float(max(1, f['length']))}
                 for i, f in enumerate(t['files'])]
        return _piece_ranges(files, t['pieceSize'])

    def pieces(self, h):
        t = self._get(h, ['pieces', 'pieceCount'])[0]
        bits = base64.b64decode(t['pieces'])
        return [bool(bits[i >> 3] & (0x80 >> (i & 7))) for i in range(t['pieceCount'])]

    def piece_size(self, h):
        return self._get(h, ['pieceSize'])[0]['pieceSize']

    def _session(self):
        return self._call('session-get', {'fields': ['download-dir', 'speed-limit-down', 'speed-limit-down-enabled',
                                                     'alt-speed-enabled', 'alt-speed-down']})

    def rate_limit(self):
        s = self._session()
        if s.get('alt-speed-enabled'):  # "turtle mode" wins over the normal limit
            return s['alt-speed-down'] * 1000
        return s['speed-limit-down'] * 1000 if s.get('speed-limit-down-enabled') else 0

    def save_root(self):
        return self._session()['download-dir'].rstrip('/')

    def make_watchable(self, t):
        done = []
        if 'sequential_download' not in t['raw']:
            done.append('as fast as it can (Transmission 4.1 or newer can download in order)')
        elif not t['raw']['sequential_download']:
            self._call('torrent-set', {'ids': [t['hash']], 'sequential_download': True})  # this name even in the old API
            done.append('in order')
        if t['state'] in ('queued', 'paused'):
            self._call('torrent-start-now', {'ids': [t['hash']]})
            done.append('started now')
        return done


class Deluge(TorrentClient):
    """Deluge 2 through its Web UI (the Web UI needs to be connected to the daemon; this connects it if not)."""
    name = 'Deluge'
    STATES = {'Queued': 'queued', 'Paused': 'paused', 'Checking': 'checking', 'Moving': 'checking',
              'Allocating': 'checking'}
    KEYS = ['name', 'progress', 'download_payload_rate', 'state', 'time_added', 'save_path', 'sequential_download',
            'prioritize_first_last']

    def __init__(self, url, user='', password=''):
        url = url.rstrip('/')
        self.url = url if url.endswith('/json') else url + '/json'
        self.password = password or 'deluge'  # Deluge's out-of-the-box password
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.n = 0

    def _raw(self, method, *params):
        self.n += 1
        body = json.dumps({'method': method, 'params': list(params), 'id': self.n}).encode()
        req = urllib.request.Request(self.url, body, {'Content-Type': 'application/json'})
        try:
            with self.opener.open(req, timeout=15) as r:
                return json.load(r)
        except Exception as e:
            raise SeerrError('Cannot reach Deluge: %s' % e)

    def _connect(self):
        if not self._raw('auth.login', self.password).get('result'):
            raise SeerrError('Deluge refused the password - check it in Seerr Hub settings')
        if not self._raw('web.connected').get('result'):
            hosts = self._raw('web.get_hosts').get('result') or []
            if not hosts:
                raise SeerrError('Deluge\'s Web UI has no daemon to connect to')
            self._raw('web.connect', hosts[0][0])

    def _call(self, method, *params):
        for attempt in (0, 1):
            out = self._raw(method, *params)
            err = out.get('error')
            if not err:
                return out.get('result')
            # 1: not logged in; 2: "unknown method", which is what the Web UI says while it has no daemon
            if attempt == 0 and err.get('code') in (1, 2):
                self._connect()
                continue
            raise SeerrError('Deluge said: %s' % err.get('message', '')[:200])

    def torrents(self, h=None):
        found = self._call('core.get_torrents_status', {'id': [h.lower()]} if h else {}, self.KEYS) or {}
        out = []
        for key, t in found.items():
            progress = t['progress'] / 100.0
            state = 'done' if progress >= 1 else self.STATES.get(t['state'], 'downloading')
            out.append({'hash': key.lower(), 'name': t['name'], 'progress': progress, 'dlspeed': t['download_payload_rate'],
                        'state': state, 'added_on': t['time_added'], 'save_path': t['save_path'], 'raw': t})
        return out

    def _status(self, h, keys):
        return self._call('core.get_torrent_status', h, keys) or {}

    def files(self, h):
        t = self._status(h, ['files', 'file_progress', 'piece_length'])
        prog = t.get('file_progress') or []
        files = [{'index': f['index'], 'name': f['path'], 'size': f['size'],
                  'progress': prog[n] if n < len(prog) else 0} for n, f in enumerate(t.get('files') or [])]
        size = t['piece_length']
        for f, raw in zip(files, t.get('files') or []):
            f['piece_range'] = (raw['offset'] // size, max(raw['offset'], raw['offset'] + raw['size'] - 1) // size)
        return files

    def pieces(self, h):
        t = self._status(h, ['pieces', 'num_pieces', 'progress'])
        if not t.get('pieces'):  # Deluge leaves it empty once everything is there
            return [t.get('progress', 0) >= 100] * (t.get('num_pieces') or 0)
        return [p == 3 for p in t['pieces']]

    def piece_size(self, h):
        return self._status(h, ['piece_length'])['piece_length']

    def rate_limit(self):
        kib = self._call('core.get_config_value', 'max_download_speed') or -1
        return int(kib * 1024) if kib > 0 else 0

    def save_root(self):
        return (self._call('core.get_config_value', 'download_location') or '').rstrip('/')

    def make_watchable(self, t):
        h, raw, done = t['hash'], t['raw'], []
        if not raw.get('sequential_download') or not raw.get('prioritize_first_last'):
            self._call('core.set_torrent_options', [h], {'sequential_download': True, 'prioritize_first_last_pieces': True})
            if not raw.get('sequential_download'):
                done.append('in order')
        if t['state'] in ('queued', 'paused'):
            self._call('core.set_torrent_options', [h], {'auto_managed': False})  # so the queue can't hold it back
            self._call('core.resume_torrent', [h])
            done.append('started now')
        return done


class ArrQueue(TorrentClient):
    """Any download client (SABnzbd, NZBGet, rTorrent, ...): what Radarr/Sonarr's queue says about it.
    Shows progress and time left, but can't tell what's playable, so no watching while it downloads."""
    name = 'Radarr/Sonarr'
    can_stream = False
    STATES = {'queued': 'queued', 'paused': 'paused', 'delay': 'queued', 'downloadClientUnavailable': 'paused'}

    def __init__(self, seerr):
        self.seerr, self.queue = seerr, None

    def entries(self):
        if self.queue is None:
            self.queue = arr_queue(self.seerr)
        return self.queue

    def torrents(self, h=None):
        out = []
        for key, q in self.entries().items():
            if h and key != h.lower():
                continue
            size = q.get('size') or 0
            progress = 1 - (q.get('sizeleft') or 0) / float(size) if size else 0
            state = 'done' if progress >= 1 else self.STATES.get(q.get('status'), 'downloading')
            out.append({'hash': key, 'name': q.get('release') or q.get('title'), 'progress': progress, 'dlspeed': 0,
                        'state': state, 'added_on': q.get('added') or '', 'save_path': '', 'timeleft': q.get('timeleft'),
                        'client': q.get('client'), 'raw': q})
        return out

    def rate_limit(self):
        return 0


def timeleft_s(text):
    """Radarr/Sonarr's '1.02:03:04' / '02:03:04' as seconds, None if missing."""
    m = re.match(r'(?:(\d+)\.)?(\d+):(\d+):(\d+)', text or '')
    if not m:
        return None
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


CLIENTS = [QBit, Transmission, Deluge]  # in the order of the "Download client" setting


def same_release(a, b):
    """Indexers and torrents spell release names differently ('WEB DL ... RAWR' vs 'WEB-DL ... -RAWR', '.mkv')."""
    norm = lambda x: re.sub(r'[^a-z0-9]', '', re.sub(r'\.(mkv|mp4|avi)$', '', (x or '').lower()))
    return bool(a and b) and norm(a) == norm(b)


class Arr(object):
    """Radarr/Sonarr: the queue (torrent hash -> title), and interactive search: add a title, list releases, grab one."""

    def __init__(self, kind, url, key, settings=None):
        self.kind, self.base, self.key = kind, url.rstrip('/') + '/api/v3', key
        self.settings = settings or {}  # Seerr's settings for this server: profile, root folder, tags

    def _call(self, path, params=None, body=None, method=None, timeout=20):
        url = self.base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            'X-Api-Key': self.key, 'Content-Type': 'application/json', 'Accept': 'application/json'})
        name = self.kind.capitalize()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                return json.loads(body) if body else None
        except urllib.error.HTTPError as e:
            try:
                err = json.load(e)
                msg = err.get('message') if isinstance(err, dict) else '; '.join(x.get('errorMessage', '') for x in err)
            except Exception:
                msg = e.reason
            raise SeerrError('%s said: %s (%s)' % (name, msg or e.reason, e.code))
        except Exception as e:
            raise SeerrError('Cannot reach %s: %s' % (name, e))

    def grabbed_hash(self, release):
        """Torrent hash of the newest grab of this release, from the grab history (recorded as soon as it's sent)."""
        try:
            h = self._call('/history', {'pageSize': 20, 'sortKey': 'date', 'sortDirection': 'descending', 'eventType': 1})
        except SeerrError:
            return None
        for r in h.get('records', []):
            if r.get('downloadId') and same_release(r.get('sourceTitle'), release):
                return r['downloadId'].lower()
        return None

    def queue(self):
        params = {'pageSize': 200, 'includeMovie' if self.kind == 'radarr' else 'includeSeries': 'true'}
        if self.kind == 'sonarr':
            params['includeEpisode'] = 'true'
        try:
            records = self._call('/queue', params).get('records', [])
        except SeerrError:
            return []
        out = []
        for q in records:
            if not q.get('downloadId'):
                continue
            progress = {k: q.get(k) for k in ('size', 'sizeleft', 'timeleft', 'status', 'added')}
            progress['client'] = q.get('downloadClient')
            if self.kind == 'radarr':
                m = q.get('movie') or {}
                out.append({'hash': q['downloadId'].lower(), 'type': 'movie', 'tmdb_id': m.get('tmdbId'),
                            'title': m.get('title') or q.get('title'), 'year': m.get('year'), 'runtime': m.get('runtime'),
                            'release': q.get('title'), **progress})
            else:
                s, e = q.get('series') or {}, q.get('episode') or {}
                out.append({'hash': q['downloadId'].lower(), 'type': 'tv', 'tmdb_id': s.get('tmdbId'),
                            'title': s.get('title') or q.get('title'), 'season': e.get('seasonNumber'),
                            'episode': e.get('episodeNumber'), 'runtime': e.get('runtime') or s.get('runtime'),
                            'release': q.get('title'), **progress})
        return out

    def size_limits(self, profile_id=None):
        """{quality id: (min, preferred, max, resolution)} in MB per minute of video; None where unlimited/unset.
        Sonarr 5 keeps sizes on each quality profile instead of the quality definitions."""
        out = {}
        for d in self._call('/qualitydefinition'):
            out[d['quality']['id']] = (d.get('minSize'), d.get('preferredSize'), d.get('maxSize'), d['quality'].get('resolution'))
        if profile_id and not any(v[1] for v in out.values()):
            def flat(items):
                for i in items:
                    if i.get('items'):
                        for x in flat(i['items']):
                            yield x
                    else:
                        yield i
            for i in flat(self._call('/qualityprofile/%d' % profile_id)['items']):
                q = i['quality']
                out[q['id']] = (i.get('minSize'), i.get('preferredSize'), i.get('maxSize'), q.get('resolution'))
        return out

    # -- making sure the title exists here, without letting it search on its own --

    def movie(self, tmdb_id, add=True):
        found = self._call('/movie', {'tmdbId': tmdb_id})
        if found or not add:
            return found[0] if found else None
        m = self._call('/movie/lookup/tmdb', {'tmdbId': tmdb_id})
        st = self.settings
        m.update({'qualityProfileId': st['activeProfileId'], 'rootFolderPath': st['activeDirectory'],
                  'minimumAvailability': st.get('minimumAvailability') or 'released', 'monitored': True,
                  'tags': st.get('tags') or [], 'addOptions': {'searchForMovie': False}})
        return self._call('/movie', body=m)

    def series(self, tvdb_id, season, anime=False, add=True):
        found = self._call('/series', {'tvdbId': tvdb_id})
        if found:
            s = found[0]
            seasons = [x for x in s['seasons'] if x['seasonNumber'] == season]
            if seasons and not seasons[0]['monitored']:  # watch the season we're downloading so it imports cleanly
                seasons[0]['monitored'] = True
                s = self._call('/series/%d' % s['id'], body=s, method='PUT')
            return s
        if not add:
            return None
        look = self._call('/series/lookup', {'term': 'tvdb:%s' % tvdb_id})
        if not look:
            raise SeerrError('Sonarr cannot find this show on TheTVDB')
        s, st = look[0], self.settings
        for x in s['seasons']:
            x['monitored'] = x['seasonNumber'] == season
        s.update({'qualityProfileId': st['activeAnimeProfileId' if anime else 'activeProfileId'],
                  'languageProfileId': st.get('activeLanguageProfileId') or 1,
                  'rootFolderPath': st['activeAnimeDirectory' if anime else 'activeDirectory'],
                  'tags': st.get('animeTags' if anime else 'tags') or [], 'seasonFolder': st.get('enableSeasonFolders', True),
                  'seriesType': 'anime' if anime else 'standard', 'monitored': True,
                  'addOptions': {'searchForMissingEpisodes': False, 'searchForCutoffUnmetEpisodes': False}})
        return self._call('/series', body=s)

    def episodes(self, series_id, season):
        return self._call('/episode', {'seriesId': series_id, 'seasonNumber': season})

    def releases(self, **params):
        """Interactive search across every indexer: slow (tens of seconds)."""
        return self._call('/release', params, timeout=180) or []

    def monitor_episodes(self, ids):
        return self._call('/episode/monitor', body={'episodeIds': ids, 'monitored': True}, method='PUT')

    def season_search(self, series_id, season):
        """Let Sonarr find the season by itself: a pack, or the best release for each monitored episode."""
        return self._call('/command', body={'name': 'SeasonSearch', 'seriesId': series_id, 'seasonNumber': season})

    def grab(self, rel, override=None):
        body = {'guid': rel['guid'], 'indexerId': rel['indexerId']}
        if override:  # the release didn't map to the title by itself: tell it which one it is
            body.update(override, shouldOverride=True, quality=rel['quality'], languages=rel.get('languages') or [])
        return self._call('/release', body=body, timeout=60)


def arr_server(seerr, kind):
    """The default (non-4K) Radarr/Sonarr server Seerr sends requests to."""
    servers = seerr._call('/settings/' + kind)
    if not servers:
        raise SeerrError('Seerr has no %s server set up' % kind.capitalize())
    s = next((x for x in servers if x.get('isDefault') and not x.get('is4k')), servers[0])
    url = '%s://%s:%s%s' % ('https' if s.get('useSsl') else 'http', s['hostname'], s['port'], s.get('baseUrl') or '')
    return Arr(kind, url, s['apiKey'], s)


def arr_queue(seerr):
    """{torrent hash: queue entry} across every Radarr/Sonarr server Seerr knows."""
    out = {}
    for kind in ('radarr', 'sonarr'):
        try:
            servers = seerr._call('/settings/' + kind)
        except SeerrError:
            continue
        for s in servers:
            url = '%s://%s:%s%s' % ('https' if s.get('useSsl') else 'http', s['hostname'], s['port'], s.get('baseUrl') or '')
            for q in Arr(kind, url, s['apiKey'], s).queue():
                out.setdefault(q['hash'], q)  # season packs: one hash, many episodes; keep the first
    return out


VIDEO_EXT = ('.mkv', '.mp4', '.m4v', '.avi', '.ts', '.mov', '.wmv')
SAFETY = 1.2      # download must outpace playback by 20%
MIN_BUFFER = 90   # seconds of video on disk before starting


def video_files(files):
    return sorted([f for f in files if f['name'].lower().endswith(VIDEO_EXT) and 'sample' not in f['name'].lower()],
                  key=lambda f: f['name'])


def estimate(f, states, piece_size, speed, runtime_s):
    """Can this file be played now while it downloads?
    Only the unbroken run of finished pieces from the start of the file counts as playable."""
    first, last = f['piece_range']
    run = 0
    for s in states[first:last + 1]:
        if not s:
            break
        run += 1
    size = f['size']
    have = size if f['progress'] >= 1 else min(size, run * piece_size)
    bitrate = size / float(runtime_s)  # bytes per second of video
    left = size - have
    if left <= 0:
        wait = 0
    elif speed <= 0:
        wait = None  # stalled: no estimate
    else:
        wait = max(0.0, left * SAFETY / speed - left / bitrate, (MIN_BUFFER * bitrate - have) / speed)
    return {'have': have, 'size': size, 'buffer_s': have / bitrate, 'bitrate': bitrate, 'speed': speed,
            'wait': wait, 'ready': wait == 0, 'tail': bool(states and states[last]),
            'runtime': runtime_s}


def mins(seconds):
    if seconds is None:
        return '?'
    m = int(seconds // 60)
    return '%d h %02d min' % (m // 60, m % 60) if m >= 60 else '%d min' % max(1, m)


def mbps(bytes_per_s):
    return '%.1f Mbit/s' % (bytes_per_s * 8 / 1e6)


# ---- picking a release ----

# MB per minute of video (min, preferred, max) when neither Sonarr nor Radarr has a size set for that resolution
DEFAULT_SIZES = {480: (3, 8, 25), 576: (3, 8, 25), 720: (8, 17, 60), 1080: (15, 40, 140), 2160: (60, 200, 400)}
WEIGHTS = {'quality': 0.30, 'format': 0.15, 'size': 0.20, 'seeds': 0.30, 'stream': 0.05}
GOOD_SEEDS = 200  # this many seeders counts as full marks


def size_reference(limits, fallback=None):
    """Turn {quality id: (min, pref, max, resolution)} into a lookup that also knows each resolution's typical sizes,
    so a quality left unlimited (Sonarr here) borrows the sizes set for the same resolution (in Radarr)."""
    by_res = {}
    for src in (fallback or {}, limits):
        for lo, pref, hi, res in src.values():
            if pref:
                by_res.setdefault(res, []).append((lo or 0, pref, hi))
    res_ref = {}
    for res, rows in by_res.items():
        rows.sort(key=lambda r: r[1])
        res_ref[res] = rows[len(rows) // 2]  # the median row

    def ref(quality):
        q = quality['quality']
        own = limits.get(q['id'])
        if own and own[1]:
            return own[:3], 'your %s size' % q['name']
        r = res_ref.get(q.get('resolution')) or DEFAULT_SIZES.get(q.get('resolution'))
        return (r, 'your %dp size' % q['resolution']) if r else (None, None)
    return ref


def size_fit(mb_min, lo, pref, hi):
    """1 at the preferred size, falling to 0 at half or double it; 0 outside min/max."""
    if (lo and mb_min < lo) or (hi and mb_min > hi):
        return 0.0
    if not pref:
        return 0.5
    return max(0.0, 1 - abs(math.log(mb_min / float(pref), 2)))


def score_releases(releases, ref, minutes, rate_limit=0):
    """Rank releases. minutes(release) is how much video it holds. Adds a 'why' summary and a 'score' to each,
    returns them best first; anything Radarr/Sonarr would reject goes below everything it would accept."""
    if not releases:
        return []
    weights = sorted(set(r.get('qualityWeight') or 0 for r in releases))
    best_cf = max(max(r.get('customFormatScore') or 0 for r in releases), 1)
    for r in releases:
        mins_ = max(1, minutes(r))
        mb_min = r['size'] / 1048576.0 / mins_
        bitrate = r['size'] / (mins_ * 60.0)
        (limits, ref_name) = ref(r['quality'])
        parts = {
            'quality': weights.index(r.get('qualityWeight') or 0) / float(max(1, len(weights) - 1)),
            'format': max(0, r.get('customFormatScore') or 0) / float(best_cf),
            'size': size_fit(mb_min, *limits) if limits else 0.5,
            'seeds': min(1.0, math.log10(1 + (r.get('seeders') or 0)) / math.log10(1 + GOOD_SEEDS)),
            'stream': 1.0 if not rate_limit or bitrate * SAFETY <= rate_limit else 0.0,
        }
        r['score'] = sum(WEIGHTS[k] * v for k, v in parts.items()) * (0.2 if not r.get('seeders') else 1)
        r['ok'] = bool(r.get('approved'))
        notes = ['%.0f MB/min' % mb_min]
        if limits:
            lo, pref, hi = limits
            if (lo and mb_min < lo) or (hi and mb_min > hi):
                notes[0] += ' - outside %s (%s-%s)' % (ref_name, int(lo or 0), int(hi) if hi else 'no max')
            else:
                notes[0] += ' vs %s %d' % (ref_name, pref)
        notes.append(mbps(bitrate) + ('' if parts['stream'] else ' - too fast to watch while downloading'))
        r['why'], r['bitrate'], r['parts'] = notes, bitrate, parts
    releases.sort(key=lambda r: (r['ok'], r['score']), reverse=True)
    return releases
