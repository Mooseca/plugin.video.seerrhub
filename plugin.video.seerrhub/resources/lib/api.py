# Seerr and JustWatch clients. Standard library only, so the add-on has no dependencies.
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


class QBit(object):
    """qBittorrent Web API. Logs in only when the server asks for it (it can skip auth for the local network)."""

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

    def torrents(self, hashes=None):
        return self._call('/torrents/info', {'hashes': hashes} if hashes else None)

    def incomplete(self):
        return [t for t in self.torrents() if t['progress'] < 1]

    def files(self, h):
        return self._call('/torrents/files', {'hash': h})

    def pieces(self, h):
        return self._call('/torrents/pieceStates', {'hash': h})

    def properties(self, h):
        return self._call('/torrents/properties', {'hash': h})

    def rate_limit(self):
        """Download cap in bytes/s, or 0 for none."""
        return max(0, self._call('/transfer/info').get('dl_rate_limit') or 0)

    def save_root(self):
        return self._call('/app/preferences')['save_path'].rstrip('/')

    def make_watchable(self, t):
        """Download in order, first/last pieces first, and don't sit in the queue. Returns what changed."""
        h, done = t['hash'], []
        if not t['seq_dl']:
            self._call('/torrents/toggleSequentialDownload', post={'hashes': h})
            done.append('in order')
        if not t['f_l_piece_prio']:
            self._call('/torrents/toggleFirstLastPiecePrio', post={'hashes': h})
        if t['state'] in ('queuedDL', 'stoppedDL', 'pausedDL'):
            self._call('/torrents/start', post={'hashes': h})
            self._call('/torrents/setForceStart', post={'hashes': h, 'value': 'true'})
            done.append('started now')
        return done


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
            if self.kind == 'radarr':
                m = q.get('movie') or {}
                out.append({'hash': q['downloadId'].lower(), 'type': 'movie', 'tmdb_id': m.get('tmdbId'),
                            'title': m.get('title') or q.get('title'), 'year': m.get('year'), 'runtime': m.get('runtime'),
                            'release': q.get('title')})
            else:
                s, e = q.get('series') or {}, q.get('episode') or {}
                out.append({'hash': q['downloadId'].lower(), 'type': 'tv', 'tmdb_id': s.get('tmdbId'),
                            'title': s.get('title') or q.get('title'), 'season': e.get('seasonNumber'),
                            'episode': e.get('episodeNumber'), 'runtime': e.get('runtime') or s.get('runtime'),
                            'release': q.get('title')})
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
        if s != 2:
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
            'wait': wait, 'ready': wait == 0, 'tail': states[last] == 2 if states else False,
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
