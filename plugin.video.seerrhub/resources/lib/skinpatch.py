# Optional Arctic Fuse 3 tweaks (setting "af3_patch", off by default), kept in place across skin updates.
# Search: no A-Z letter row under the search box (OK on the box opens Kodi's full keyboard), Back on the box
# closes search, and while the box is empty it shows Seerr Hub's recommended movies and shows.
# A skin update replaces the skin's files, so the service checks them and patches them again.
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
    # recommended rows at the top of the results while nothing is typed (standard layout)
    ("""    <include name="MultiSearch_Results_Standard">
        <include content="Hub_Widgets_Grouplist">
            <param name="onup">103</param>
            <param name="bumper">false</param>
""",
     """    <!-- %s: Seerr Hub's recommendations while the search box is empty -->
    <include name="SeerrHub_Search_Recommended">
        <definition>
            <include content="Widget_Label">
                <param name="groupid">7$PARAM[id]</param>
                <param name="id">$PARAM[id]</param>
                <param name="label">$PARAM[label]</param>
                <param name="include">List_Poster_Row</param>
                <param name="labelheight">widget_label_h_detailed</param>
                <param name="visible">String.IsEmpty(Control.GetLabel(100).index(1)) + [!Integer.IsEqual(Container($PARAM[id]).NumItems,0) | Container($PARAM[id]).IsUpdating]</param>
                <param name="busy_visible">Container($PARAM[id]).IsUpdating</param>
            </include>
            <include content="List_Poster_Row">
                <param name="id">$PARAM[id]</param>
                <param name="detailed">false</param>
                <param name="hidden_button_enabled">false</param>
                <top>widget_items_t_detailed</top>
                <height>widget_items_h</height>
                <visible>String.IsEmpty(Control.GetLabel(100).index(1)) + [!Integer.IsEqual(Container($PARAM[id]).NumItems,0) | Container($PARAM[id]).IsUpdating]</visible>
                <include content="MultiSearch_Widget_Row_OnFocus">
                    <param name="id">$PARAM[id]</param>
                </include>
                <include content="MultiSearch_Widget_Row_OnBack">
                    <param name="id">$PARAM[id]</param>
                </include>
                <content target="videos">plugin://plugin.video.seerrhub/?action=recommended&amp;type=$PARAM[type]</content>
            </include>
        </definition>
    </include>

    <include name="MultiSearch_Results_Standard">
        <include content="Hub_Widgets_Grouplist">
            <param name="onup">103</param>
            <param name="bumper">false</param>
            <include content="SeerrHub_Search_Recommended">
                <param name="id">5801</param>
                <param name="label">Recommended movies</param>
                <param name="type">movie</param>
            </include>
            <include content="SeerrHub_Search_Recommended">
                <param name="id">5802</param>
                <param name="label">Recommended TV shows</param>
                <param name="type">tv</param>
            </include>
""" % MARK),
    # moving down from the buttons lands on the first recommended row
    ("""    <variable name="MultiSearch_Action_Reset">
        <value condition="Control.IsVisible(200)">SetFocus(200)</value>""",
     """    <variable name="MultiSearch_Action_Reset">
        <value condition="Control.IsVisible(5801)">SetFocus(5801)</value>
        <value condition="Control.IsVisible(5802)">SetFocus(5802)</value>
        <value condition="Control.IsVisible(200)">SetFocus(200)</value>"""),
]


def log(msg):
    xbmc.log('[plugin.video.seerrhub] skin: %s' % msg, xbmc.LOGINFO)


def enabled():
    return xbmcaddon.Addon('plugin.video.seerrhub').getSetting('af3_patch') == 'true'


def skin_dir():
    return xbmcvfs.translatePath('special://home/addons/%s/1080i/' % SKIN)


def swapped(text, old, new):
    """Whether old was already swapped for new. When one text holds the other (lines added around the skin's
    own), the longer one decides."""
    if new in old:
        return old not in text and new in text
    return new in text


def rewrite(path, edits):
    """Swaps each pair's first text for its second, skipping pairs already swapped, so a newer Seerr Hub can add
    to an older patch. True when the file changed; nothing is written unless every pair is either swapped
    already or has its first text in the file exactly once."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    todo = []
    for old, new in edits:
        if swapped(text, old, new):
            continue
        if text.count(old) != 1:
            log('%s is not as expected (skin update?), left as it is' % os.path.basename(path))
            return False
        todo.append((old, new))
    if not todo:
        return False
    for old, new in todo:
        text = text.replace(old, new)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(text)
    return True


def fix_search(folder, on):
    """Patches (on) or restores (off) the search include. True when it changed."""
    path = os.path.join(folder, SEARCH)
    if not os.path.exists(os.path.join(folder, MULTISEARCH)) or not os.path.exists(path):
        return False  # older skin layout: not handled
    if rewrite(path, SEARCH_EDITS if on else [(new, old) for old, new in reversed(SEARCH_EDITS)]):
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
