# Seerr Hub plugin: search / trending / requests listings, and the entry point for TMDb Helper's player.
import sys
import urllib.parse

import xbmcgui
import xbmcplugin

from resources.lib import api, hub

BASE, HANDLE = sys.argv[0], int(sys.argv[1])
ARGS = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))


def url(**kw):
    return BASE + '?' + urllib.parse.urlencode(kw)


def folder(label, icon, **kw):
    li = xbmcgui.ListItem(label)
    li.setArt({'icon': icon, 'thumb': icon})
    xbmcplugin.addDirectoryItem(HANDLE, url(**kw), li, isFolder=True)


def add_titles(items):
    for it in items:
        mtype = it.get('mediaType')
        if mtype not in ('movie', 'tv'):
            continue
        title = it.get('title') or it.get('name') or ''
        date = it.get('releaseDate') or it.get('firstAirDate') or ''
        status = (it.get('mediaInfo') or {}).get('status')
        badge = hub.status_label(status)
        label = title + (' (%s)' % date[:4] if date else '') + ('  [%s]' % badge if badge else '')
        li = xbmcgui.ListItem(label)
        li.setArt(api.art(it))
        tag = li.getVideoInfoTag()
        tag.setTitle(title)
        tag.setMediaType('movie' if mtype == 'movie' else 'tvshow')
        tag.setPlot(((badge + '. ') if badge else '') + (it.get('overview') or ''))
        if date[:4].isdigit():
            tag.setYear(int(date[:4]))
        tag.setUniqueIDs({'tmdb': str(it['id'])}, 'tmdb')
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(HANDLE, url(action='open', type=mtype, tmdb_id=it['id'], title=title), li, isFolder=False)


def add_downloads(s):
    qb = hub.client(s)
    rows = hub.active_downloads(s, qb)
    if not rows:
        li = xbmcgui.ListItem('Nothing downloading right now')
        xbmcplugin.addDirectoryItem(HANDLE, '', li, isFolder=False)
    for h, q, t in rows:
        if not qb.can_stream:  # only Radarr/Sonarr's queue to go on
            line = '%d%%  -  %s' % (t['progress'] * 100, hub.queue_verdict(t))
            plot = 'Downloading in %s.[CR]%s' % (t.get('client') or 'the download client', q.get('torrent', ''))
        else:
            try:
                t, f, e = hub.check(qb, h, q)
                line = '%d%%  -  %s' % (t['progress'] * 100, hub.verdict(e, t))
                plot = '%s of %s ready to play.[CR]Downloading at %s, the file needs %s.[CR]%s' % (
                    api.mins(e['buffer_s']), api.mins(e['runtime']), api.mbps(e['speed']), api.mbps(e['bitrate']),
                    q.get('torrent', ''))
            except api.SeerrError as err:
                line, plot = str(err), q.get('torrent', '')
        title = q.get('title', '') + hub.episode_tag(q)
        li = xbmcgui.ListItem('%s  [%s]' % (title, line))
        tag = li.getVideoInfoTag()
        tag.setTitle(title)
        tag.setPlot(plot)
        if q.get('tmdb_id'):
            tag.setUniqueIDs({'tmdb': str(q['tmdb_id'])}, 'tmdb')
        li.setArt({'icon': 'DefaultAddonsUpdates.png'})
        kw = {k: q[k] for k in ('title', 'type', 'tmdb_id', 'season', 'episode', 'runtime') if q.get(k) is not None}
        xbmcplugin.addDirectoryItem(HANDLE, url(action='dl', hash=h, **kw), li, isFolder=False)


def listing(content='videos'):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def main():
    action = ARGS.get('action')
    if not action:
        li = xbmcgui.ListItem('Search')  # an action, not a folder: see 'ask' below
        li.setArt({'icon': 'DefaultAddonsSearch.png', 'thumb': 'DefaultAddonsSearch.png'})
        li.setProperty('IsPlayable', 'false')
        xbmcplugin.addDirectoryItem(HANDLE, url(action='ask'), li, isFolder=False)
        folder('Trending', 'DefaultRecentlyAddedMovies.png', action='trending')
        folder('My requests', 'DefaultAddonPVRClient.png', action='requests')
        folder('Downloading', 'DefaultAddonsUpdates.png', action='downloads')
        return listing('files')
    if action == 'open':  # also TMDb Helper's player: ?action=open&type=movie&tmdb_id=123
        if ARGS.get('tmdb_id'):  # TMDb Helper's episode player also sends &season=&episode=
            hub.open_title(ARGS.get('type', 'movie'), ARGS['tmdb_id'], ARGS.get('title'),
                           hub.number(ARGS.get('season')), hub.number(ARGS.get('episode')))
        else:
            hub.open_by_title(ARGS.get('type', 'movie'), ARGS.get('title', ''), ARGS.get('year'))
        if HANDLE >= 0:  # started as a folder (RunAddon): close it quietly
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False, updateListing=True, cacheToDisc=False)
        return
    if action == 'dl':  # one download: watch it now or once it's safe
        q = {k: ARGS[k] for k in ('title', 'type', 'tmdb_id') if ARGS.get(k)}
        for k in ('season', 'episode', 'runtime'):
            if ARGS.get(k, '').isdigit():
                q[k] = int(ARGS[k])
        hub.watch_download(ARGS['hash'], q)
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False, updateListing=True, cacheToDisc=False)
        return
    if action == 'ask':
        # ask for the search outside any folder load: a keyboard opened while Kodi is loading a folder can be
        # closed by Kodi (it came back empty straight away on the Shield), and Back would ask again
        q = xbmcgui.Dialog().input('Search movies and shows')
        if q:
            import xbmc
            found = url(action='search', query=q)
            if xbmc.getCondVisibility('Window.IsActive(videos)'):  # from the add-on's own menu
                xbmc.executebuiltin('Container.Update(%s)' % found)
            else:  # from the skin's "Search & Request" menu item (RunPlugin)
                xbmc.executebuiltin('ActivateWindow(videos,%s,return)' % found)
        return
    s = hub.seerr()
    try:
        if action == 'search':
            q = ARGS.get('query') or xbmcgui.Dialog().input('Search movies and shows')
            if not q:
                return xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
            found = s.search(q).get('results', [])
            add_titles(found)
            if not any(it.get('mediaType') in ('movie', 'tv') for it in found):
                li = xbmcgui.ListItem('Nothing found for "%s"' % q)
                li.setArt({'icon': 'DefaultIconInfo.png'})
                xbmcplugin.addDirectoryItem(HANDLE, '', li, isFolder=False)
        elif action == 'trending':
            add_titles(s.trending().get('results', []))
        elif action == 'requests':
            items = []
            for r in s.requests(20).get('results', []):
                m = r.get('media') or {}
                try:
                    d = s.details(m.get('mediaType'), m.get('tmdbId'))
                except api.SeerrError:
                    continue
                d['mediaType'] = m.get('mediaType')
                items.append(d)
            add_titles(items)
        elif action == 'downloads':
            add_downloads(s)
    except api.SeerrError as e:
        # these listings also feed home-screen rows: show the problem as an item, never as a pop-up
        hub.log(str(e))
        li = xbmcgui.ListItem(str(e).split(':')[0])
        li.setArt({'icon': 'DefaultIconWarning.png'})
        li.getVideoInfoTag().setPlot(str(e))
        xbmcplugin.addDirectoryItem(HANDLE, '', li, isFolder=False)
    listing()


main()
