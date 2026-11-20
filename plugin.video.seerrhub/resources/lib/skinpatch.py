# Optional Arctic Fuse 3 tweaks (setting "af3_patch", off by default), kept in place across skin updates.
# Search: no A-Z letter row under the search box (OK on the box opens Kodi's full keyboard), and Back on the
# box closes search. A skin update replaces the skin's files, so the service checks them and patches them again.
# Only files that still look exactly as expected are changed: after a bigger skin change they are left alone.
import os

import xbmc
import xbmcaddon
import xbmcvfs

SKIN = 'skin.arctic.fuse.3'
MARK = 'seerrhub-keyboard-patch'
SEARCH = 'Includes_Search.xml'
MULTISEARCH = 'script-skinvariables-multisearch.xml'  # skin 3.5+ searches through SkinVariables multisearch
MENUS = 'script-skinvariables-generator-includes-.xml'  # SkinVariables' generated menus and widgets

# (skin text, patched text). The script never closes search on Back, the skin does it through its hidden button
# 109; then it moves on to the next hub (Home), or Home's menu would still sit on Search and open it again.
SEARCH_EDITS = [
    ("""                <ondown>101</ondown>

                <texturefocus />""",
     """                <ondown>102</ondown> <!-- %s -->
                <onback>SendClick(109)</onback>
                <onback>$INFO[Container(399).ListItem(1).Property(action)]</onback>

                <texturefocus />""" % MARK),
    ("""            <control type="panel" id="101">
                <orientation>vertical</orientation>""",
     """            <control type="panel" id="101">
                <visible>false</visible>
                <orientation>vertical</orientation>"""),
    ("""            <param name="onup">101</param>
            <param name="ondown">103</param>""",
     """            <param name="onup">3001</param>
            <param name="ondown">103</param>"""),
]


def log(msg):
    xbmc.log('[plugin.video.seerrhub] skin: %s' % msg, xbmc.LOGINFO)


def enabled():
    return xbmcaddon.Addon('plugin.video.seerrhub').getSetting('af3_patch') == 'true'


def skin_dir():
    return xbmcvfs.translatePath('special://home/addons/%s/1080i/' % SKIN)


def rewrite(path, edits):
    """Swaps each pair's first text for its second. True when the file changed; nothing is written unless
    every first text is in the file exactly once."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    for old, _ in edits:
        if text.count(old) != 1:
            log('%s is not as expected (skin update?), left as it is' % os.path.basename(path))
            return False
    for old, new in edits:
        text = text.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def fix_search(folder, on):
    """Patches (on) or restores (off) the search include. True when it changed."""
    path = os.path.join(folder, SEARCH)
    if not os.path.exists(os.path.join(folder, MULTISEARCH)) or not os.path.exists(path):
        return False  # older skin layout: not handled
    with open(path, encoding='utf-8') as f:
        patched = MARK in f.read()
    if on == patched:
        return False
    if rewrite(path, SEARCH_EDITS if on else [(new, old) for old, new in SEARCH_EDITS]):
        log('search %s' % ('patched' if on else 'restored'))
        return True
    return False


class SkinKeeper(object):
    """Checks the skin now and then. A changed file only shows after a skin reload, which waits until
    nothing plays and Kodi is idle on Home, so it never pulls the screen from under you."""

    def __init__(self):
        self.reload = False
        self.rebuilt = False  # rebuild once per Kodi session at most, so a failing rebuild can't keep reloading

    def tick(self):
        if not xbmc.getCondVisibility('System.HasAddon(%s)' % SKIN):
            return
        on, folder = enabled(), skin_dir()
        try:
            if fix_search(folder, on):
                self.reload = True
        except (IOError, OSError) as e:
            log('search: %s' % e)
        rebuild = on and not self.rebuilt and xbmc.getCondVisibility('System.HasAddon(script.skinvariables)') and \
            not os.path.exists(os.path.join(folder, MENUS))
        if not (self.reload or rebuild) or xbmc.getSkinDir() != SKIN:
            return
        if xbmc.getCondVisibility('Player.HasMedia') or not xbmc.getCondVisibility('Window.IsActive(Home)') \
                or xbmc.getCondVisibility('System.HasActiveModalDialog'):
            return  # try again at the next check
        self.reload = False
        if rebuild:  # the skin update dropped the generated menus: rebuild them (this reloads the skin too)
            self.rebuilt = True
            log('rebuilding the skin menus')
            xbmc.executebuiltin('RunScript(script.skinvariables,action=buildtemplate,force)')
        else:
            log('reloading the skin')
            xbmc.executebuiltin('ReloadSkin()')
