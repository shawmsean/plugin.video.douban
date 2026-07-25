# -*- coding: utf-8 -*-
import sys
import os
import json
import hashlib
import urllib.parse
import xbmc
import xbmcplugin
import xbmcgui
import xbmcaddon
import xbmcvfs
from resources.lib.doubanApi import (
    CATEGORIES, MOVIE_CATEGORIES, MOVIE_REGIONS, TV_TYPES, SHOW_TYPES,
    MOVIE_FILTER_GENRES, TV_FILTER_GENRES, SHOW_FILTER_GENRES,
    FILTER_REGIONS, TV_FILTER_REGIONS, FILTER_YEARS, FILTER_SORTS, TV_FILTER_PLATFORMS,
    fetchRecentHot, fetchRecommend,
)
from resources.lib.tmdbApi import searchTmdb
from resources.lib.settings import getSetting, reload as reloadSettings
from resources.lib.logger import logInfo, logError

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')

_TRIGGER_INDEX_PROP = 'auto_load_trigger_index'


class SettingsMonitor(xbmc.Monitor):
    def onSettingsChanged(self):
        reloadSettings()
        logInfo("设置已更新")


def parseParams(paramStr):
    params = {}
    if not paramStr:
        return params
    if paramStr.startswith('?'):
        paramStr = paramStr[1:]
    for pair in paramStr.split('&'):
        if '=' in pair:
            key, value = pair.split('=', 1)
            params[key] = urllib.parse.unquote_plus(value)
    return params


def buildUrl(base, action, **kwargs):
    params = {'action': action}
    params.update(kwargs)
    query = '&'.join(f'{k}={urllib.parse.quote(str(v), safe="")}' for k, v in params.items() if v)
    return f'{base}?{query}'


def addDir(handle, base, name, action, art=None, info=None, mediatype=None, properties=None, **kwargs):
    url = buildUrl(base, action, **kwargs)
    li = xbmcgui.ListItem(name, offscreen=True)
    if art:
        li.setArt(art)
    if info or mediatype:
        vinfo = dict(info) if info else {}
        if mediatype:
            vinfo['mediatype'] = mediatype
        li.setInfo('video', vinfo)
    if properties:
        for key, val in properties.items():
            if val:
                li.setProperty(key, str(val))
    xbmcplugin.addDirectoryItem(handle, url, li, True)


def addPlayItem(handle, base, name, action, art=None, info=None, mediatype=None, properties=None, **kwargs):
    url = buildUrl(base, action, **kwargs)
    li = xbmcgui.ListItem(name, offscreen=True)
    if art:
        li.setArt(art)
    if info:
        vinfo = dict(info)
        if mediatype:
            vinfo['mediatype'] = mediatype
        li.setInfo('video', vinfo)
    elif mediatype:
        li.setInfo('video', {'mediatype': mediatype})
    if properties:
        for key, val in properties.items():
            if val:
                li.setProperty(key, str(val))
    li.setProperty('IsPlayable', 'true')
    xbmcplugin.addDirectoryItem(handle, url, li, False)


def endDir(handle, content_type=None, cache=False):
    if content_type:
        xbmcplugin.setContent(handle, content_type)
    xbmcplugin.endOfDirectory(handle, cacheToDisc=cache)


_CACHE_DIR = None


def _getCacheDir():
    global _CACHE_DIR
    if _CACHE_DIR is None:
        try:
            _CACHE_DIR = xbmcvfs.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
        except AttributeError:
            _CACHE_DIR = xbmc.translatePath(xbmcaddon.Addon().getAddonInfo('profile'))
    return _CACHE_DIR


def _cacheKey(*parts):
    return 'douban_cache_' + '_'.join(str(p) for p in parts)


def _writeCache(key, items):
    try:
        cacheDir = _getCacheDir()
        if not os.path.exists(cacheDir):
            os.makedirs(cacheDir, exist_ok=True)
        h = hashlib.md5(key.encode()).hexdigest()
        path = os.path.join(cacheDir, f'cache_{h}.json')
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(items, f, ensure_ascii=False)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception:
        pass


def _readCache(key):
    try:
        h = hashlib.md5(key.encode()).hexdigest()
        path = os.path.join(_getCacheDir(), f'cache_{h}.json')
        if not os.path.exists(path):
            return []
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        pass
    return []


def _enrichPosters(items):
    if not items:
        return
    from concurrent.futures import ThreadPoolExecutor, as_completed
    tasks = []
    for item in items:
        poster = item.get('poster', '')
        if poster and 'doubanio.com' in poster:
            tasks.append(item)
    if not tasks:
        return
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_enrichPoster, item): item for item in tasks}
        try:
            for f in as_completed(futures, timeout=15):
                pass
        except Exception:
            pass


def _enrichPoster(item):
    title = item.get('title', '')
    if not title:
        return
    tmdbInfo = searchTmdb(title)
    if tmdbInfo and tmdbInfo.get('poster'):
        item['poster'] = tmdbInfo['poster']
    if tmdbInfo and tmdbInfo.get('backdrop'):
        item['backdrop'] = tmdbInfo['backdrop']


def _addTriggerItem(handle, nextUrl, triggerIndex):
    li = xbmcgui.ListItem('', offscreen=True)
    li.setProperty('is_trigger', '1')
    li.setProperty('next_page', nextUrl)
    xbmcgui.Window(10000).setProperty(_TRIGGER_INDEX_PROP, str(triggerIndex))
    xbmcplugin.addDirectoryItem(handle, nextUrl, li, True)


def _dedupItems(items):
    seen = set()
    i = 0
    while i < len(items):
        iid = items[i].get('id', '')
        if iid in seen:
            items.pop(i)
        else:
            seen.add(iid)
            i += 1


def _buildTmdbMeta(tmdbInfo, poster=''):
    if not tmdbInfo:
        return {}, {'thumb': poster, 'poster': poster, 'icon': poster}, {}
    castStr = tmdbInfo.get('cast', '')
    castList = [(n.strip(), '') for n in castStr.split('/') if n.strip()] if castStr else []
    infoExt = {
        'year': tmdbInfo.get('year', ''),
        'genre': tmdbInfo.get('genre', ''),
        'cast': castList,
        'director': tmdbInfo.get('director', ''),
        'rating': tmdbInfo.get('rating', ''),
    }
    for key in ('tagline', 'premiered', 'writer'):
        val = tmdbInfo.get(key, '')
        if val:
            infoExt[key] = val
    duration = tmdbInfo.get('duration', 0)
    if duration:
        infoExt['duration'] = duration
    backdrop = tmdbInfo.get('backdrop', '')
    art = {
        'thumb': poster,
        'poster': poster,
        'icon': poster,
        'fanart': backdrop,
        'landscape': backdrop,
    }
    properties = {}
    if tmdbInfo.get('numberOfEpisodes'):
        properties['totalepisodes'] = str(tmdbInfo['numberOfEpisodes'])
    if tmdbInfo.get('status'):
        properties['Status'] = tmdbInfo['status']
    return infoExt, art, properties


def _isMovieCategory(categoryId):
    return categoryId in ('movie', 'movie_filter')


def _contentTypeForCategory(categoryId):
    return 'movies' if _isMovieCategory(categoryId) else 'tvshows'


def _mediatypeForCategory(categoryId):
    return 'movie' if _isMovieCategory(categoryId) else 'tvshow'


def showRoot(handle, base):
    for cat in CATEGORIES:
        addDir(handle, base, cat['name'], 'category_list', cat_id=cat['id'],
               info={'title': cat['name']}, mediatype='video')
    addDir(handle, base, '搜索', 'search_input',
           art={'icon': 'DefaultAddonsSearch.png'})
    addDir(handle, base, '清除缓存', 'clear_cache',
           art={'icon': 'DefaultAddonService.png'})
    endDir(handle, 'sources')


def showCategoryList(handle, base, cat_id):
    if cat_id == 'movie':
        for cat in MOVIE_CATEGORIES:
            addDir(handle, base, cat['name'], 'recent_hot', cat_id=cat_id,
                   category=cat['value'], type='全部',
                   info={'title': cat['name']}, mediatype='video')
        for reg in MOVIE_REGIONS[1:]:
            addDir(handle, base, f'{reg["name"]}电影', 'recent_hot', cat_id=cat_id,
                   category='热门', type=reg['value'],
                   info={'title': f'{reg["name"]}电影'}, mediatype='video')
        endDir(handle, 'sources')
    elif cat_id == 'tv':
        for tp in TV_TYPES:
            addDir(handle, base, tp['name'], 'recent_hot', cat_id=cat_id,
                   category=cat_id, type=tp['value'],
                   info={'title': tp['name']}, mediatype='video')
        endDir(handle, 'sources')
    elif cat_id == 'show':
        for tp in SHOW_TYPES:
            addDir(handle, base, tp['name'], 'recent_hot', cat_id=cat_id,
                   category=cat_id, type=tp['value'],
                   info={'title': tp['name']}, mediatype='video')
        endDir(handle, 'sources')
    elif cat_id == 'movie_filter':
        _showFilterDimensions(handle, base, cat_id)
    elif cat_id == 'tv_filter':
        _showFilterDimensions(handle, base, cat_id)
    elif cat_id == 'show_filter':
        _showFilterDimensions(handle, base, cat_id)
    else:
        endDir(handle, 'sources')


def _showFilterDimensions(handle, base, cat_id):
    addDir(handle, base, '全部', 'filter_list', cat_id=cat_id,
           info={'title': '全部'}, mediatype='video')
    if cat_id == 'movie_filter':
        genres = MOVIE_FILTER_GENRES
        regions = FILTER_REGIONS
    elif cat_id == 'tv_filter':
        genres = TV_FILTER_GENRES
        regions = TV_FILTER_REGIONS
    else:
        genres = SHOW_FILTER_GENRES
        regions = TV_FILTER_REGIONS

    for g in genres:
        if not g:
            continue
        addDir(handle, base, g, 'filter_region', cat_id=cat_id, genre=g,
               info={'title': g}, mediatype='video')
    endDir(handle, 'sources')


def showFilterRegion(handle, base, cat_id, genre):
    if cat_id == 'movie_filter':
        regions = FILTER_REGIONS
    elif cat_id == 'tv_filter':
        regions = TV_FILTER_REGIONS
    else:
        regions = TV_FILTER_REGIONS

    addDir(handle, base, '全部地区', 'filter_year', cat_id=cat_id,
           genre=genre, region='',
           info={'title': '全部地区'}, mediatype='video')
    for r in regions:
        if not r:
            continue
        addDir(handle, base, r, 'filter_year', cat_id=cat_id,
               genre=genre, region=r,
               info={'title': r}, mediatype='video')
    endDir(handle, 'sources')


def showFilterYear(handle, base, cat_id, genre, region):
    addDir(handle, base, '全部年代', 'filter_sort', cat_id=cat_id,
           genre=genre, region=region, year='',
           info={'title': '全部年代'}, mediatype='video')
    for y in FILTER_YEARS:
        if not y:
            continue
        addDir(handle, base, y, 'filter_sort', cat_id=cat_id,
               genre=genre, region=region, year=y,
               info={'title': y}, mediatype='video')
    endDir(handle, 'sources')


def showFilterSort(handle, base, cat_id, genre, region, year):
    for s in FILTER_SORTS:
        addDir(handle, base, s['name'], 'filter_list', cat_id=cat_id,
               genre=genre, region=region, year=year, sort=s['value'],
               info={'title': s['name']}, mediatype='video')
    endDir(handle, 'sources')


def showRecentHot(handle, base, cat_id, category='', type='', page=1):
    logInfo(f"近期热门: cat={cat_id}, category={category}, type={type}, page={page}")
    contentType = _contentTypeForCategory(cat_id)
    mediaType = _mediatypeForCategory(cat_id)
    limit = 20
    page = int(page)
    start = (page - 1) * limit

    cKey = _cacheKey('recent', cat_id, category, type)
    allItems = _readCache(cKey) if page > 1 else []

    items, total = fetchRecentHot(cat_id, category=category, vtype=type, start=start, limit=limit)
    if not items and page == 1:
        xbmcgui.Dialog().notification("豆瓣推荐", "加载失败或无数据", xbmcgui.NOTIFICATION_INFO, 3000)
        endDir(handle, contentType)
        return
    allItems.extend(items)
    _dedupItems(allItems)
    _writeCache(cKey, allItems)
    hasMore = start + limit < total
    nextUrl = buildUrl(base, 'recent_hot', cat_id=cat_id,
                       category=category, type=type, page=str(page + 1)) if hasMore else None
    _renderListWithPager(handle, base, allItems, mediaType, contentType, hasMore=hasMore, nextUrl=nextUrl)
    _backgroundEnrich(cKey, allItems)


def showFilterList(handle, base, cat_id, genre='', region='', year='', sort='U', page=1):
    logInfo(f"筛选: cat={cat_id}, genre={genre}, region={region}, year={year}, sort={sort}, page={page}")
    contentType = _contentTypeForCategory(cat_id)
    mediaType = _mediatypeForCategory(cat_id)
    limit = 20
    page = int(page)
    start = (page - 1) * limit

    cKey = _cacheKey('filter', cat_id, genre, region, year, sort)
    allItems = _readCache(cKey) if page > 1 else []

    items, total = fetchRecommend(cat_id, genre=genre, region=region, year=year, sort=sort, start=start, limit=limit)
    if not items and page == 1:
        xbmcgui.Dialog().notification("豆瓣推荐", "加载失败或无数据", xbmcgui.NOTIFICATION_INFO, 3000)
        endDir(handle, contentType)
        return
    allItems.extend(items)
    _dedupItems(allItems)
    _writeCache(cKey, allItems)
    hasMore = start + limit < total
    nextUrl = buildUrl(base, 'filter_list', cat_id=cat_id,
                       genre=genre, region=region, year=year, sort=sort, page=str(page + 1)) if hasMore else None
    _renderListWithPager(handle, base, allItems, mediaType, contentType, hasMore=hasMore, nextUrl=nextUrl)
    _backgroundEnrich(cKey, allItems)


def _renderListWithPager(handle, base, items, mediaType, contentType, hasMore=False, nextUrl=None):
    for item in items:
        _addListItem(handle, base, item, mediaType)
    if hasMore and nextUrl:
        _addTriggerItem(handle, nextUrl, len(items))
    endDir(handle, contentType)


def _backgroundEnrich(cKey, items):
    needsEnrich = any(
        item.get('poster', '') and 'doubanio.com' in item.get('poster', '')
        for item in items
    )
    if not needsEnrich:
        return
    import threading
    def _worker():
        _enrichPosters(items)
        _writeCache(cKey, items)
        logInfo(f"后台enrich完成，缓存已更新: {cKey}")
    t = threading.Thread(target=_worker, daemon=True)
    t.start()


def _addListItem(handle, base, item, mediaType):
    itemId = item.get('id', '')
    title = item.get('title', '')
    poster = item.get('poster', '')
    backdrop = item.get('backdrop', '')
    year = item.get('year', '')
    rating = item.get('rating', '')
    meta = item.get('meta', '')
    subtitle = item.get('subtitle', '')
    catId = item.get('categoryId', '')

    cKey = _cacheKey('item', itemId)
    _writeCache(cKey, item)

    info = {'title': title, 'mediatype': mediaType}
    if year:
        info['year'] = year
    if rating:
        try:
            info['rating'] = float(rating)
        except (ValueError, TypeError):
            pass
    if meta:
        info['plot'] = meta
    if subtitle:
        info['tagline'] = subtitle

    art = {'poster': poster, 'icon': poster} if poster else {}
    if backdrop:
        art['thumb'] = backdrop
        art['fanart'] = backdrop
        art['landscape'] = backdrop
    elif poster:
        art['thumb'] = poster
    art = art if art else None
    addDir(handle, base, title, 'detail', douban_id=itemId, cat_id=catId,
           art=art, info=info, mediatype=mediaType)


def showDetail(handle, base, douban_id, cat_id):
    logInfo(f"详情: id={douban_id}, cat={cat_id}")
    cKey = _cacheKey('detail', douban_id)
    cached = _readCache(cKey)
    if cached:
        detail = cached.get('detail')
        tmdbInfo = cached.get('tmdbInfo')
    else:
        detail = _fetchDoubanDetail(douban_id)
        if not detail:
            xbmcgui.Dialog().notification("豆瓣推荐", "加载详情失败", xbmcgui.NOTIFICATION_ERROR, 3000)
            endDir(handle, 'videos')
            return
        title = detail.get('title', '')
        tmdbInfo = searchTmdb(title)
        _writeCache(cKey, {'detail': detail, 'tmdbInfo': tmdbInfo})

    title = detail.get('title', '')
    overview = detail.get('overview', '')
    poster = detail.get('poster', '')
    year = detail.get('year', '')
    rating = detail.get('rating', '')
    subtitle = detail.get('subtitle', '')
    meta = detail.get('meta', '')

    if tmdbInfo and tmdbInfo.get('poster'):
        poster = tmdbInfo['poster']
    if tmdbInfo and tmdbInfo.get('overview'):
        overview = tmdbInfo['overview']

    info = {'title': title, 'plot': overview}
    if year:
        info['year'] = year
    if rating:
        try:
            info['rating'] = float(rating)
        except (ValueError, TypeError):
            pass
    if subtitle:
        info['tagline'] = subtitle
    if meta:
        info['mpaa'] = meta

    infoExt, art, properties = _buildTmdbMeta(tmdbInfo, poster)
    info.update(infoExt)
    fanart = art.get('fanart', '')

    contentType = 'movies' if _isMovieCategory(cat_id) else 'episodes'

    addPlayItem(handle, base, '播放', 'play',
                douban_id=douban_id, art=art, info=info, mediatype='video', properties=properties)

    addDir(handle, base, '盘搜搜索', 'pansou_search',
           title=title, art=art, info=info, mediatype='video', properties=properties)

    endDir(handle, 'videos')


def _fetchDoubanDetail(douban_id):
    iKey = _cacheKey('item', douban_id)
    cached = _readCache(iKey)
    if cached and isinstance(cached, dict) and cached.get('title'):
        return cached
    return {
        'id': douban_id,
        'title': '',
        'overview': '',
        'year': '',
    }


def play(handle, base, douban_id):
    iKey = _cacheKey('item', douban_id)
    item = _readCache(iKey)
    title = ''
    year = ''
    if item and isinstance(item, dict):
        title = item.get('title', '')
        year = item.get('year', '')

    cKey = _cacheKey('detail', douban_id)
    cached = _readCache(cKey)
    tmdbInfo = cached.get('tmdbInfo') if cached else None
    if tmdbInfo:
        if tmdbInfo.get('title'):
            title = tmdbInfo['title']
        if tmdbInfo.get('year'):
            year = tmdbInfo['year']

    if not title:
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return

    params = {'action': 'auto_play', 'title': title}
    if year:
        params['year'] = year
    query = '&'.join(f'{k}={urllib.parse.quote(str(v), safe="")}' for k, v in params.items())
    pansouUrl = f'plugin://plugin.video.pansou/?{query}'

    li = xbmcgui.ListItem(path=pansouUrl, offscreen=True)
    li.setProperty('IsPlayable', 'true')
    xbmcplugin.setResolvedUrl(handle, True, li)


def showPansouSearch(handle, base, title=''):
    if not title:
        endDir(handle, 'videos')
        return
    url = f'plugin://plugin.video.pansou/?action=results&keyword={urllib.parse.quote(title)}'
    xbmc.executebuiltin(f'Container.Update({url})')


def showSearchInput(handle, base):
    keyboard = xbmcgui.Dialog().input('搜索影片', '', xbmcgui.INPUT_ALPHANUM)
    if not keyboard:
        endDir(handle, 'sources')
        return
    url = buildUrl(base, 'search_results', keyword=keyboard)
    xbmc.executebuiltin(f'Container.Update({url})')


def showSearchResults(handle, base, keyword, page=1):
    contentType = 'videos'
    mediaType = 'video'
    limit = 20
    start = (int(page) - 1) * limit

    cKey = _cacheKey('search', keyword)
    allItems = _readCache(cKey) if int(page) > 1 else []

    items, total = fetchRecentHot('movie', category='热门', vtype='全部', start=start, limit=limit)
    if not items:
        xbmcgui.Dialog().notification("豆瓣推荐", "搜索暂不支持，请使用盘搜", xbmcgui.NOTIFICATION_INFO, 3000)
        endDir(handle, contentType)
        return

    for item in allItems:
        _addListItem(handle, base, item, mediaType)
    endDir(handle, contentType)


def clearCache(handle, base):
    cacheDir = _getCacheDir()
    count = 0
    if os.path.exists(cacheDir):
        for f in os.listdir(cacheDir):
            if f.startswith('cache_') and f.endswith('.json'):
                try:
                    os.remove(os.path.join(cacheDir, f))
                    count += 1
                except Exception:
                    pass
    xbmcgui.Dialog().notification("豆瓣推荐", f"已清除 {count} 个缓存", xbmcgui.NOTIFICATION_INFO, 3000)
    xbmc.executebuiltin('Container.Update')


ROUTER = {
    'root': showRoot,
    'category_list': showCategoryList,
    'recent_hot': showRecentHot,
    'filter_region': showFilterRegion,
    'filter_year': showFilterYear,
    'filter_sort': showFilterSort,
    'filter_list': showFilterList,
    'detail': showDetail,
    'play': play,
    'pansou_search': showPansouSearch,
    'search_input': showSearchInput,
    'search_results': showSearchResults,
    'clear_cache': clearCache,
}


def main():
    handle = int(sys.argv[1])
    base = sys.argv[0]
    params = parseParams(sys.argv[2]) if len(sys.argv) > 2 else {}
    action = params.get('action', 'root')

    logInfo(f"路由: action={action}, params={params}")

    fn = ROUTER.get(action)
    if fn:
        kwargs = {k: v for k, v in params.items() if k != 'action'}
        try:
            fn(handle, base, **kwargs)
        except TypeError as e:
            logError(f"路由参数错误: action={action}, error={e}")
            xbmcgui.Dialog().notification("豆瓣推荐", f"路由错误: {action}", xbmcgui.NOTIFICATION_ERROR, 3000)
            endDir(handle, 'videos')
    else:
        logError(f"未知路由: action={action}")
        endDir(handle, 'videos')


if __name__ == '__main__':
    main()