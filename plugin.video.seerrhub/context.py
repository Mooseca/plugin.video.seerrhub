# Context menu: "Watch or request" on any movie, show, season or episode (library, TMDb Helper, Arctic Fuse widgets).
import sys

from resources.lib import hub


def main():
    li = sys.listitem
    tag = li.getVideoInfoTag()
    dbtype = tag.getMediaType() or 'movie'
    if dbtype in ('season', 'episode'):
        # TMDb Helper puts the show's id here; library items only know the show title
        tmdb_id = li.getProperty('tvshow.tmdb_id') or li.getProperty('tvshow.tmdb')
        title = tag.getTVShowTitle()
        season = tag.getSeason() if tag.getSeason() >= 0 else None
        episode = tag.getEpisode() if dbtype == 'episode' and tag.getEpisode() > 0 else None
        if tmdb_id:
            return hub.open_title('tv', tmdb_id, title, season, episode)
        return hub.open_by_title('tv', title)
    tmdb_id = tag.getUniqueID('tmdb') or li.getProperty('tmdb_id')
    if tmdb_id:
        return hub.open_title(dbtype, tmdb_id, tag.getTitle())
    hub.open_by_title(dbtype, tag.getTitle() or li.getLabel(), tag.getYear() or None)


main()
