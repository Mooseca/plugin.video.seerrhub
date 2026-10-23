# Watched / resume points for things played straight off the downloads folder: the media server never sees that
# playback, so we keep it here and push it to Plex, Jellyfin or Emby once Radarr/Sonarr have imported the file
# and the server has the title.
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import xbmcaddon
import xbmcvfs

from resources.lib import hub

DATA = 'special://profile/addon_data/plugin.video.seerrhub/'
PENDING = xbmcvfs.translatePath(DATA + 'sync_pending.json')
OLD_PENDING = xbmcvfs.translatePath(DATA + 'plex_pending.json')  # name used up to 1.4
WATCHED = 0.9            # past 90% counts as watched
MIN_POSITION = 60        # under a minute isn't worth a resume point
GIVE_UP_DAYS = 30        # stop looking for titles that never reach the server


class NotReady(Exception):
    """The media server (or its Kodi add-on) isn't set up: keep the queue, try again later."""


def load():
    for path in (PENDING, OLD_PENDING):
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            continue
    return []


def save(items):
    os.makedirs(os.path.dirname(PENDING), exist_ok=True)
    with open(PENDING, 'w') as fh:
        json.dump(items, fh)
    if os.path.exists(OLD_PENDING):
        os.remove(OLD_PENDING)


def key(i):
    return i.get('type'), str(i.get('tmdb_id')), i.get('season'), i.get('episode')


def record(q, position, total, ended):
    """Remember how far a download was watched; the newest viewing of a title replaces older ones."""
    if not q.get('tmdb_id') or (position < MIN_POSITION and not ended):
        return
    if q.get('type') == 'tv' and (q.get('season') is None or q.get('episode') is None):
        hub.log('sync: no episode number for %s, not recorded' % q.get('title'))
        return
    watched = ended or (total > 0 and position / total >= WATCHED)
    item = {k: q.get(k) for k in ('type', 'tmdb_id', 'title', 'season', 'episode')}
    item.update(position=int(position), total=int(total), watched=bool(watched), added=time.time())
    save([i for i in load() if key(i) != key(item)] + [item])
    hub.log('sync: recorded %s' % item)


# ---- Plex, through PlexKodiConnect: its library paths hold the Plex id, its settings the token ----

class Plex(object):
    name = 'Plex'
    LIBRARY = 'com.plexapp.plugins.library'

    def bases(self):
        """Plex address and token from PlexKodiConnect; plain http on the LAN IP as a fallback for TLS trouble."""
        try:
            pkc = xbmcaddon.Addon('plugin.video.plexkodiconnect')
        except RuntimeError:
            raise NotReady('PlexKodiConnect is not installed')
        host, port = pkc.getSetting('ipaddress'), pkc.getSetting('port') or '32400'
        token = pkc.getSetting('accessToken') or pkc.getSetting('plexToken')
        if not host:
            raise NotReady('PlexKodiConnect is not signed in')
        bases = ['%s://%s:%s' % ('https' if pkc.getSetting('https') == 'true' else 'http', host, port)]
        m = re.match(r'(\d+-\d+-\d+-\d+)\.[0-9a-f]+\.plex\.direct$', host)
        if m:
            bases.append('http://%s:%s' % (m.group(1).replace('-', '.'), port))
        return bases, token

    def call(self, path, **params):
        bases, token = self.bases()
        params['X-Plex-Token'] = token
        err = None
        for base in bases:
            try:
                req = urllib.request.Request(base + path + '?' + urllib.parse.urlencode(params), headers={'Accept': 'application/json'})
                urllib.request.urlopen(req, timeout=10).read()
                return
            except (urllib.error.URLError, OSError) as e:
                err = e
        raise err

    def check(self):
        self.call('/identity')

    def find(self, i):
        """(plex_id, already watched) once PlexKodiConnect has synced the title into Kodi, else (None, False)."""
        if i['type'] == 'movie':
            dbid = hub.find_in_library('movie', i['tmdb_id'], i['title'])
            if dbid is None:
                return None, False
            d = hub.rpc('VideoLibrary.GetMovieDetails', {'movieid': dbid, 'properties': ['file', 'playcount']}).get('moviedetails', {})
        else:
            show = hub.find_in_library('tv', i['tmdb_id'], i['title'])
            if show is None:
                return None, False
            eps = hub.rpc('VideoLibrary.GetEpisodes', {'tvshowid': show, 'season': int(i['season']),
                                                       'properties': ['file', 'episode', 'playcount']}).get('episodes', [])
            d = next((e for e in eps if e['episode'] == int(i['episode'])), {})
        m = re.search(r'plex_id=(\d+)', d.get('file', ''))
        return (m.group(1) if m else None), bool(d.get('playcount'))

    def mark_watched(self, plex_id):
        self.call('/:/scrobble', key=plex_id, identifier=self.LIBRARY)

    def set_position(self, plex_id, seconds):
        self.call('/:/progress', key=plex_id, identifier=self.LIBRARY, time=seconds * 1000, state='stopped')


# ---- Jellyfin / Emby, through their API: the settings, or the login of their Kodi add-on ----

class Jellyfin(object):
    ADDONS = {'jellyfin': 'plugin.video.jellyfin', 'emby': 'plugin.video.emby'}

    def __init__(self, kind='jellyfin'):
        self.kind, self.name = kind, kind.capitalize()
        self._login = None

    def login(self):
        """(address, token, user id)."""
        if self._login:
            return self._login
        url, token, user = hub.ADDON.getSetting('server_url'), hub.ADDON.getSetting('server_key'), hub.ADDON.getSetting('server_user')
        if url and token:
            self._login = (url.rstrip('/'), token, None)  # call() needs the address to look the user up
            try:
                self._login = (url.rstrip('/'), token, self.user_id(user))
            except Exception:
                self._login = None
                raise
            return self._login
        path = xbmcvfs.translatePath('special://profile/addon_data/%s/data.json' % self.ADDONS[self.kind])
        try:
            with open(path) as fh:
                servers = json.load(fh).get('Servers', [])
        except (OSError, ValueError):
            servers = []
        for srv in servers:
            address = srv.get('address') or srv.get('ManualAddress') or srv.get('LocalAddress')
            if address and srv.get('AccessToken') and srv.get('UserId'):
                self._login = (address.rstrip('/'), srv['AccessToken'], srv['UserId'])
                return self._login
        raise NotReady('%s is not set up: sign in with the %s for Kodi add-on or fill in Seerr Hub settings'
                       % (self.name, self.name))

    def user_id(self, name):
        """With an API key there's no user: find the one named in settings (or the only one there is)."""
        users = self.call('/Users')
        if name:
            match = [u for u in users if u.get('Name', '').lower() == name.lower() or u.get('Id') == name]
        else:
            match = users if len(users) == 1 else [u for u in users if (u.get('Policy') or {}).get('IsAdministrator')][:1]
        if not match:
            raise NotReady('%s has no user called "%s"' % (self.name, name) if name else
                           'Put your %s user name in Seerr Hub settings' % self.name)
        return match[0]['Id']

    def call(self, path, params=None, body=None, fallback=None):
        """GET, or POST when there's a body. fallback: an older route to try if the server doesn't know this one."""
        base, token, _ = self.login()
        url = base + path + ('?' + urllib.parse.urlencode(params) if params else '')
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': 'MediaBrowser Client="Seerr Hub", Device="Kodi", DeviceId="seerrhub", Version="1", Token="%s"' % token,
            'X-Emby-Token': token, 'Accept': 'application/json', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if fallback and e.code in (404, 405):
                return self.call(fallback, params, body)
            raise
        return json.loads(raw) if raw else None

    def check(self):
        self.call('/System/Info/Public')

    def find_item(self, kind, tmdb_id, title):
        _, _, uid = self.login()
        params = {'userId': uid, 'Recursive': 'true', 'IncludeItemTypes': kind, 'Fields': 'ProviderIds',
                  'searchTerm': title}
        items = (self.call('/Items', params) or {}).get('Items', [])
        tmdb = lambda it: str({k.lower(): v for k, v in (it.get('ProviderIds') or {}).items()}.get('tmdb'))
        return (next((it for it in items if tmdb(it) == str(tmdb_id)), None)
                or next((it for it in items if it.get('Name', '').lower() == title.lower()), None))

    def find(self, i):
        """(item id, already watched) once the server has the title, else (None, False)."""
        _, _, uid = self.login()
        if i['type'] == 'movie':
            item = self.find_item('Movie', i['tmdb_id'], i['title'])
        else:
            show = self.find_item('Series', i['tmdb_id'], i['title'])
            if not show:
                return None, False
            eps = (self.call('/Shows/%s/Episodes' % show['Id'], {'season': int(i['season']), 'userId': uid}) or {}).get('Items', [])
            item = next((e for e in eps if e.get('IndexNumber') == int(i['episode'])), None)
        if not item:
            return None, False
        return item['Id'], bool((item.get('UserData') or {}).get('Played'))

    def mark_watched(self, item_id):
        _, _, uid = self.login()
        self.call('/UserPlayedItems/%s' % item_id, {'userId': uid}, body={},
                  fallback='/Users/%s/PlayedItems/%s' % (uid, item_id))

    def set_position(self, item_id, seconds):
        _, _, uid = self.login()
        self.call('/UserItems/%s/UserData' % item_id, {'userId': uid}, body={'PlaybackPositionTicks': int(seconds * 10 ** 7)},
                  fallback='/Users/%s/Items/%s/UserData' % (uid, item_id))


def backend():
    kind = hub.server_kind()
    if kind == 'plex':
        return Plex()
    if kind in ('jellyfin', 'emby'):
        return Jellyfin(kind)
    return None


def check():
    """For the log at start-up: can we reach the media server?"""
    b = backend()
    if not b:
        hub.log('sync: no media server set up, watched / resume sync is off')
        return
    try:
        b.check()
        hub.log('sync: %s reachable' % b.name)
    except Exception as e:
        hub.log('sync: %s not reachable yet (%s)' % (b.name, e))


def push(b, i, item_id, played):
    label = i['title'] + hub.episode_tag(i)
    if i['watched']:
        b.mark_watched(item_id)
        return 'Marked %s watched in %s' % (label, b.name)
    if played:
        return None  # already watched there: don't turn it back into a half-watched title
    b.set_position(item_id, i['position'])
    return 'Saved where you stopped in %s to %s' % (label, b.name)


def sync():
    items = load()
    b = backend() if items else None
    if not b:
        return
    left, done = [], []
    for i in items:
        try:
            item_id, played = b.find(i)
            if not item_id:
                if time.time() - i.get('added', 0) < GIVE_UP_DAYS * 86400:
                    left.append(i)
                continue
            msg = push(b, i, item_id, played)
            if msg:
                done.append(msg)
        except NotReady as e:  # nothing will work this round
            hub.log('sync: %s' % e)
            return
        except Exception as e:  # server / Kodi not reachable right now: try again next round
            hub.log('sync: %s for %s' % (e, i.get('title')))
            left.append(i)
    if len(left) != len(items):
        save(left)
    for msg in done:
        hub.log('sync: ' + msg)
        hub.notify(msg)
