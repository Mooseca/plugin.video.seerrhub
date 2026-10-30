# Background part of Seerr Hub: notes how far you got in anything played off the downloads folder,
# and hands that to the media server once the title has been imported there (see resources/lib/mediasync.py).
import json

import xbmc
import xbmcgui

from resources.lib import hub, mediasync

SYNC_EVERY = 600  # seconds
DOWNLOADS_EVERY = 30  # seconds between refreshes of the Downloading widget / folder while something downloads
RELOAD_PROP = 'seerrhub.downloads'  # the skin's Downloading widget path ends in &reload=$INFO[Window(Home).Property(...)]


class Tracker(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.q, self.pos, self.total = None, 0, 0
        self.sync_now = True

    def onAVStarted(self):
        self.q = None
        win = xbmcgui.Window(10000)
        try:
            info = json.loads(win.getProperty(hub.PLAYING_PROP) or '{}')
        except ValueError:
            info = {}
        win.clearProperty(hub.PLAYING_PROP)
        try:
            playing = self.getPlayingFile()
        except RuntimeError:
            return
        if info and playing.rstrip('/').split('/')[-1] == info['path'].rstrip('/').split('/')[-1]:
            self.q, self.pos, self.total = info['q'], 0, hub.runtime_s(info['q'])
            hub.log('sync: tracking %s' % playing)
            if info.get('lang'):
                try:
                    hub.pick_streams(info['lang'])
                except Exception as e:
                    hub.log('streams: %s' % e)

    def tick(self):
        if not self.q:
            return
        try:
            self.pos = self.getTime()
            total = self.getTotalTime()
        except RuntimeError:  # player already gone
            return
        if total > 0:
            self.total = total

    def finish(self, ended):
        if self.q:
            mediasync.record(self.q, self.pos, self.total, ended)
            self.q = None
            self.sync_now = True

    def onPlayBackStopped(self):
        self.finish(False)

    def onPlayBackEnded(self):
        self.finish(True)

    def onPlayBackError(self):
        self.finish(False)


class DownloadsRefresher(object):
    """Kodi keeps a widget's listing until its path changes: while torrents are downloading, change the path
    every DOWNLOADS_EVERY seconds (and once more when the last one finishes) so the progress stays current."""

    def __init__(self):
        self.busy, self.n = None, 0

    def tick(self):
        if not hub.configured():
            return
        try:
            busy = bool(hub.client().incomplete())
        except Exception:
            return
        if busy or self.busy:
            self.n += 1
            xbmcgui.Window(10000).setProperty(RELOAD_PROP, str(self.n))
            if 'action=downloads' in xbmc.getInfoLabel('Container.FolderPath'):  # the full Downloading list is open
                xbmc.executebuiltin('Container.Refresh')
        self.busy = busy


def main():
    monitor, player = xbmc.Monitor(), Tracker()
    mediasync.check()
    waited, downloads = 0, DownloadsRefresher()
    while not monitor.waitForAbort(5):
        player.tick()
        waited += 5
        if player.isPlayingVideo():
            continue
        if waited % DOWNLOADS_EVERY == 0:
            downloads.tick()
        if player.sync_now or waited >= SYNC_EVERY:
            player.sync_now, waited = False, 0
            try:
                mediasync.sync()
            except Exception as e:  # never let the service die
                hub.log('sync: %s' % e)


main()
