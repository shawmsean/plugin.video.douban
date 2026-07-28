# -*- coding: utf-8 -*-
import sys
import os
import json
import hashlib
import time
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
from resources.lib.tmdbApi import searchTmdb, fetchSeasons, fetchEpisodes
from resources.lib.settings import getSetting, getEnrichThreads, getDoubanImgProxy, reload as reloadSettings
from resources.lib.logger import logInfo, logError

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')

_TRIGGER_INDEX_PROP = 'bili_trigger_index'


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


def endDir(handle, content_type=None, cache=True):
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


_LIST_CACHE_TTL = 1800


def _writeCache(key, items, ts=None):
    try:
        cacheDir = _getCacheDir()
        if not os.path.exists(cacheDir):
            os.makedirs(cacheDir, exist_ok=True)
        h = hashlib.md5(key.encode()).hexdigest()
        path = os.path.join(cacheDir, f'cache_{h}.json')
        tmp = path + '.tmp'
        payload = {'_ts': ts or int(time.time()), 'data': items}
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        if os.path.exists(path):
            os.remove(path)
        os.rename(tmp, path)
    except Exception:
        pass


def _readCache(key, ttl=0):
    try:
        h = hashlib.md5(key.encode()).hexdigest()
        path = os.path.join(_getCacheDir(), f'cache_{h}.json')
        if not os.path.exists(path):
            return None
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict) and 'data' in payload:
            if ttl > 0:
                ts = payload.get('_ts', 0)
                if int(time.time()) - ts > ttl:
                    return None
            return payload['data']
        return payload
    except Exception:
        pass
    return None


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
    with ThreadPoolExecutor(max_workers=getEnrichThreads()) as pool:
        futures = {pool.submit(_enrichPoster, item): item for item in tasks}
        try:
            for f in as_completed(futures, timeout=15):
                pass
        except Exception:
            pass


def _cleanTitleForSearch(title):
    import re
    cleaned = re.sub(r'\s*第[一二三四五六七八九十\d]+季\s*$', '', title)
    return cleaned.strip() if cleaned.strip() != title.strip() else title


def _enrichPoster(item):
    title = item.get('title', '')
    if not title:
        return
    searchTitle = _cleanTitleForSearch(title)
    tmdbInfo = searchTmdb(searchTitle)
    if not tmdbInfo or not tmdbInfo.get('poster'):
        if searchTitle != title:
            tmdbInfo = searchTmdb(title)
    if tmdbInfo:
        if tmdbInfo.get('poster'):
            item['poster'] = tmdbInfo['poster']
        if tmdbInfo.get('backdrop'):
            item['backdrop'] = tmdbInfo['backdrop']
        item['tmdbInfo'] = tmdbInfo


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


def _addFilterItem(handle, base, name, filter_value):
    li = xbmcgui.ListItem(name if name else '全部', offscreen=True)
    li.setProperty('filter_value', filter_value)
    url = buildUrl(base, 'filter_dummy', fv=filter_value)
    xbmcplugin.addDirectoryItem(handle, url, li, True)


def showFilterCategories(handle, base):
    xbmcgui.Window(10000).setProperty('filter_browse_plugin', ADDON_ID)
    for cat in CATEGORIES:
        if cat['id'] in ('movie_filter', 'tv_filter', 'show_filter'):
            _addFilterItem(handle, base, cat['name'], cat['id'])
    endDir(handle, 'sources', cache=False)


def showFilterGenres(handle, base, cat_id=''):
    if cat_id == 'movie_filter':
        genres = MOVIE_FILTER_GENRES
    elif cat_id == 'tv_filter':
        genres = TV_FILTER_GENRES
    elif cat_id == 'show_filter':
        genres = SHOW_FILTER_GENRES
    else:
        genres = MOVIE_FILTER_GENRES
    for g in genres:
        _addFilterItem(handle, base, g, g)
    endDir(handle, 'sources', cache=False)


def showFilterRegions(handle, base, cat_id=''):
    if cat_id == 'movie_filter':
        regions = FILTER_REGIONS
    elif cat_id in ('tv_filter', 'show_filter'):
        regions = TV_FILTER_REGIONS
    else:
        regions = FILTER_REGIONS
    for r in regions:
        _addFilterItem(handle, base, r, r)
    endDir(handle, 'sources', cache=False)


def showFilterYears(handle, base):
    for y in FILTER_YEARS:
        _addFilterItem(handle, base, y, y)
    endDir(handle, 'sources', cache=False)


def showFilterSorts(handle, base):
    for s in FILTER_SORTS:
        _addFilterItem(handle, base, s['name'], s['value'])
    endDir(handle, 'sources', cache=False)


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
    if page > 1:
        allItems = _readCache(cKey) or []
    else:
        allItems = _readCache(cKey, ttl=_LIST_CACHE_TTL)
        if allItems is not None:
            logInfo(f"近期热门缓存命中: {len(allItems)}项")
            _renderListWithPager(handle, base, allItems, mediaType, contentType, cat_id=cat_id, hasMore=True,
                                 nextUrl=buildUrl(base, 'recent_hot', cat_id=cat_id,
                                                  category=category, type=type, page='2'))
            return
        allItems = []

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
    _renderListWithPager(handle, base, allItems, mediaType, contentType, cat_id=cat_id, hasMore=hasMore, nextUrl=nextUrl)
    _backgroundEnrich(cKey, allItems)


def showFilterList(handle, base, cat_id, genre='', region='', year='', sort='U', page=1):
    logInfo(f"筛选: cat={cat_id}, genre={genre}, region={region}, year={year}, sort={sort}, page={page}")
    contentType = _contentTypeForCategory(cat_id)
    mediaType = _mediatypeForCategory(cat_id)
    limit = 20
    page = int(page)
    start = (page - 1) * limit

    cKey = _cacheKey('filter', cat_id, genre, region, year, sort)
    if page > 1:
        allItems = _readCache(cKey) or []
    else:
        allItems = _readCache(cKey, ttl=_LIST_CACHE_TTL)
        if allItems is not None:
            logInfo(f"筛选缓存命中: {len(allItems)}项")
            _renderListWithPager(handle, base, allItems, mediaType, contentType, cat_id=cat_id, hasMore=True,
                                 nextUrl=buildUrl(base, 'filter_list', cat_id=cat_id,
                                                  genre=genre, region=region, year=year, sort=sort, page='2'))
            return
        allItems = []

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
    _renderListWithPager(handle, base, allItems, mediaType, contentType, cat_id=cat_id, hasMore=hasMore, nextUrl=nextUrl)
    _backgroundEnrich(cKey, allItems)


def _renderListWithPager(handle, base, items, mediaType, contentType, cat_id='', hasMore=False, nextUrl=None):
    for item in items:
        _addListItem(handle, base, item, mediaType, cat_id=cat_id)
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


def _proxyDoubanImage(url):
    if not url or 'doubanio.com' not in url:
        return url
    proxy = getDoubanImgProxy()
    if not proxy:
        return url
    import re
    m = re.match(r'https?://img(\d+)\.doubanio\.com(/.*)', url)
    if m:
        return f'{proxy}/img{m.group(1)}{m.group(2)}'
    return url


def _addListItem(handle, base, item, mediaType, cat_id=''):
    itemId = item.get('id', '')
    title = item.get('title', '')
    poster = _proxyDoubanImage(item.get('poster', ''))
    backdrop = _proxyDoubanImage(item.get('backdrop', ''))
    year = item.get('year', '')
    rating = item.get('rating', '')
    meta = item.get('meta', '')
    subtitle = item.get('subtitle', '')
    tmdbInfo = item.get('tmdbInfo')
    isTv = mediaType == 'tvshow' and not _isMovieCategory(cat_id)

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

    art = {}
    if poster:
        art['poster'] = poster
        art['icon'] = poster
    if backdrop:
        art['thumb'] = backdrop
        art['fanart'] = backdrop
        art['landscape'] = backdrop
    elif poster:
        art['thumb'] = poster

    properties = {}

    if tmdbInfo:
        infoExt, _, propsTmdb = _buildTmdbMeta(tmdbInfo, poster)
        info.update(infoExt)
        properties.update(propsTmdb)
        if tmdbInfo.get('overview'):
            info['plot'] = tmdbInfo['overview']

    art = art if art else None

    searchTitle = _cleanTitleForSearch(title)
    pansouUrl = f'plugin://plugin.video.pansou/?action=results&keyword={urllib.parse.quote(searchTitle)}'

    if isTv and tmdbInfo and tmdbInfo.get('tmdbId'):
        tmdbId = tmdbInfo['tmdbId']
        url = buildUrl(base, 'seasons', tmdb_id=tmdbId, douban_id=itemId)
        li = xbmcgui.ListItem(title, offscreen=True)
        if art:
            li.setArt(art)
        li.setInfo('video', info)
        if properties:
            for key, val in properties.items():
                if val:
                    li.setProperty(key, str(val))
        li.addContextMenuItems([
            ('自动播放', f'PlayMedia(plugin://plugin.video.douban/?action=play&douban_id={itemId})'),
            ('盘搜搜索', f'Container.Update({pansouUrl})'),
        ], replaceItems=False)
        xbmcplugin.addDirectoryItem(handle, url, li, True)
    else:
        url = buildUrl(base, 'play', douban_id=itemId)
        li = xbmcgui.ListItem(title, offscreen=True)
        if art:
            li.setArt(art)
        li.setInfo('video', info)
        if properties:
            for key, val in properties.items():
                if val:
                    li.setProperty(key, str(val))
        li.setProperty('IsPlayable', 'true')
        li.addContextMenuItems([('盘搜搜索', f'Container.Update({pansouUrl})')], replaceItems=False)
        xbmcplugin.addDirectoryItem(handle, url, li, False)


def showSeasons(handle, base, tmdb_id, douban_id):
    logInfo(f"季列表: tmdb_id={tmdb_id}, douban_id={douban_id}")
    iKey = _cacheKey('item', douban_id)
    item = _readCache(iKey)
    showTitle = item.get('title', '') if item and isinstance(item, dict) else ''
    tmdbInfo = item.get('tmdbInfo') if item and isinstance(item, dict) else None
    if tmdbInfo and tmdbInfo.get('title'):
        showTitle = tmdbInfo['title']

    seasons = fetchSeasons(tmdb_id)
    if not seasons:
        xbmcgui.Dialog().notification("豆瓣推荐", "获取季列表失败", xbmcgui.NOTIFICATION_ERROR, 3000)
        endDir(handle, 'seasons')
        return

    for s in seasons:
        sn = s['season']
        title = s.get('title', f'第{sn}季')
        epCount = s.get('episodeCount', 0)
        label = f'{title} ({epCount}集)'
        poster = s.get('poster', '')
        backdrop = s.get('backdrop', '')

        info = {'title': label, 'mediatype': 'season', 'tvshowtitle': showTitle, 'season': sn}
        if s.get('year'):
            info['year'] = s['year']
        if s.get('overview'):
            info['plot'] = s['overview']
        if s.get('premiered'):
            info['premiered'] = s['premiered']
        if epCount:
            info['episode'] = epCount

        art = {}
        if poster:
            art['poster'] = poster
            art['icon'] = poster
        if backdrop:
            art['thumb'] = backdrop
            art['fanart'] = backdrop
            art['landscape'] = backdrop
        elif poster:
            art['thumb'] = poster
        art = art if art else None

        properties = {}
        if epCount:
            properties['totalepisodes'] = str(epCount)

        url = buildUrl(base, 'episodes', tmdb_id=tmdb_id, season=str(sn), douban_id=douban_id)
        li = xbmcgui.ListItem(label, offscreen=True)
        if art:
            li.setArt(art)
        li.setInfo('video', info)
        if properties:
            for key, val in properties.items():
                if val:
                    li.setProperty(key, str(val))
        xbmcplugin.addDirectoryItem(handle, url, li, True)

    endDir(handle, 'seasons')


def showEpisodes(handle, base, tmdb_id, season, douban_id):
    season = int(season)
    logInfo(f"集列表: tmdb_id={tmdb_id}, season={season}, douban_id={douban_id}")
    iKey = _cacheKey('item', douban_id)
    item = _readCache(iKey)
    showTitle = item.get('title', '') if item and isinstance(item, dict) else ''
    tmdbInfo = item.get('tmdbInfo') if item and isinstance(item, dict) else None
    if tmdbInfo and tmdbInfo.get('title'):
        showTitle = tmdbInfo['title']

    episodes = fetchEpisodes(tmdb_id, season)
    if not episodes:
        xbmcgui.Dialog().notification("豆瓣推荐", "获取集列表失败", xbmcgui.NOTIFICATION_ERROR, 3000)
        endDir(handle, 'episodes')
        return

    seasonPoster = episodes[0].get('seasonPoster', '') if episodes else ''

    for ep in episodes:
        en = ep['episode']
        epTitle = ep.get('title', '')
        label = f'{season}x{en:02d}. {epTitle}'
        thumb = ep.get('thumb', '')
        rating = ep.get('rating', 0)

        info = {'title': epTitle, 'mediatype': 'episode', 'tvshowtitle': showTitle, 'season': season, 'episode': en}
        if ep.get('year'):
            info['year'] = ep['year']
        if ep.get('overview'):
            info['plot'] = ep['overview']
        if ep.get('premiered'):
            info['premiered'] = ep['premiered']
        if ep.get('duration'):
            info['duration'] = ep['duration']
        if rating:
            try:
                info['rating'] = float(rating)
            except (ValueError, TypeError):
                pass

        art = {}
        if thumb:
            art['thumb'] = thumb
            art['icon'] = thumb
        if seasonPoster:
            art['poster'] = seasonPoster
        if not thumb and seasonPoster:
            art['thumb'] = seasonPoster
        art = art if art else None

        url = buildUrl(base, 'play_episode', douban_id=douban_id, season=str(season), episode=str(en))
        li = xbmcgui.ListItem(label, offscreen=True)
        if art:
            li.setArt(art)
        li.setInfo('video', info)
        li.setProperty('IsPlayable', 'true')

        searchTitle = _cleanTitleForSearch(showTitle)
        pansouUrl = f'plugin://plugin.video.pansou/?action=auto_play&title={urllib.parse.quote(searchTitle)}&season={season}&episode={en}'
        li.addContextMenuItems([('盘搜搜索', f'Container.Update({pansouUrl})')], replaceItems=False)

        xbmcplugin.addDirectoryItem(handle, url, li, False)

    endDir(handle, 'episodes')


def playEpisode(handle, base, douban_id, season='', episode=''):
    iKey = _cacheKey('item', douban_id)
    item = _readCache(iKey)
    if not item or not isinstance(item, dict):
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return

    title = item.get('title', '')
    tmdbInfo = item.get('tmdbInfo')
    if tmdbInfo and tmdbInfo.get('title'):
        title = tmdbInfo['title']

    if not title:
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return

    params = {'action': 'auto_play', 'title': title}
    if season:
        params['season'] = season
    if episode:
        params['episode'] = episode
    query = '&'.join(f'{k}={urllib.parse.quote(str(v), safe="")}' for k, v in params.items())
    pansouUrl = f'plugin://plugin.video.pansou/?{query}'

    li = xbmcgui.ListItem(path=pansouUrl, offscreen=True)
    li.setProperty('IsPlayable', 'true')
    xbmcplugin.setResolvedUrl(handle, True, li)


def play(handle, base, douban_id):
    iKey = _cacheKey('item', douban_id)
    item = _readCache(iKey)
    if not item or not isinstance(item, dict):
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem(offscreen=True))
        return

    title = item.get('title', '')
    year = item.get('year', '')
    tmdbInfo = item.get('tmdbInfo')
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


def showSearchInput(handle, base):
    endDir(handle, 'sources')
    keyboard = xbmcgui.Dialog().input('搜索影片', '', xbmcgui.INPUT_ALPHANUM)
    if not keyboard:
        return
    url = f'plugin://plugin.video.pansou/?action=results&keyword={urllib.parse.quote(keyboard)}'
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
            if f.endswith('.json') and (f.startswith('cache_') or f.startswith('tmdb_')):
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
    'filter_categories': showFilterCategories,
    'filter_genres': showFilterGenres,
    'filter_regions': showFilterRegions,
    'filter_years': showFilterYears,
    'filter_sorts': showFilterSorts,
    'filter_region': showFilterRegion,
    'filter_year': showFilterYear,
    'filter_sort': showFilterSort,
    'filter_list': showFilterList,
    'seasons': showSeasons,
    'episodes': showEpisodes,
    'play': play,
    'play_episode': playEpisode,
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