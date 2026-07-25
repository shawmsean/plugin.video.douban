import time
from resources.lib.httpClient import get
from resources.lib.settings import getTmdbApiKey, getTmdbLanguage
from resources.lib.logger import logInfo, logError

_cache = {}
_cacheTs = {}
CACHE_TTL = 3600


def _tmdbUrl(path, **params):
    key = getTmdbApiKey()
    lang = getTmdbLanguage()
    q = f'api_key={key}&language={lang}'
    for k, v in params.items():
        q += f'&{k}={v}'
    return f'https://api.tmdb.org/3{path}?{q}'


def _getCached(key):
    if key in _cache and (time.time() - _cacheTs.get(key, 0) < CACHE_TTL):
        return _cache[key]
    return None


def _setCached(key, value):
    _cache[key] = value
    _cacheTs[key] = time.time()


def searchTmdb(title, skip_detail=False):
    if not title:
        return None
    cached = _getCached(f'search_{title}')
    if cached is not None:
        return cached

    url = _tmdbUrl('/search/multi', query=title)
    resp = get(url, timeoutKey='search')
    if not resp:
        return None

    try:
        data = resp.json()
    except Exception:
        return None

    results = data.get('results', [])
    if not results:
        _setCached(f'search_{title}', None)
        return None

    best = None
    for r in results:
        if r.get('media_type') in ('movie', 'tv'):
            best = r
            break

    if not best:
        best = results[0]

    result = _formatTmdbResult(best)

    if result and result.get('tmdbId') and not skip_detail:
        mediaType = result.get('mediaType', 'movie')
        if mediaType not in ('movie', 'tv'):
            mediaType = 'movie'
        fullResult = fetchTmdbDetail(result['tmdbId'], mediaType)
        if fullResult:
            result = fullResult

    _setCached(f'search_{title}', result)
    return result


def fetchTmdbDetail(tmdbId, mediaType='movie'):
    url = _tmdbUrl(f'/{mediaType}/{tmdbId}', append_to_response='credits')
    resp = get(url, timeoutKey='detail')
    if not resp:
        return None
    try:
        return _formatTmdbResult(resp.json())
    except Exception:
        return None


def _formatTmdbResult(item):
    if not item:
        return None

    mediaType = item.get('media_type', item.get('type', 'movie'))
    title = item.get('title') or item.get('name', '')
    overview = item.get('overview', '')
    posterPath = item.get('poster_path', '')
    backdropPath = item.get('backdrop_path', '')
    rating = item.get('vote_average', 0)
    year = ''
    releaseDate = item.get('release_date') or item.get('first_air_date', '')
    if releaseDate and len(releaseDate) >= 4:
        year = releaseDate[:4]

    genre = ''
    genres = item.get('genres', [])
    if genres:
        genre = ' / '.join(g.get('name', '') for g in genres if g.get('name'))

    cast = ''
    credits = item.get('credits', {})
    castList = credits.get('cast', [])[:5]
    if castList:
        cast = ' / '.join(c.get('name', '') for c in castList if c.get('name'))

    director = ''
    crew = credits.get('crew', [])
    directors = [c.get('name', '') for c in crew if c.get('job') == 'Director']
    if directors:
        director = ' / '.join(directors)

    writer = ''
    writers = [c.get('name', '') for c in crew if c.get('job') in ('Writer', 'Screenplay')]
    if writers:
        writer = ' / '.join(writers[:3])

    tagline = item.get('tagline', '')
    duration = item.get('runtime', 0) or 0
    if not duration:
        episodeRunTime = item.get('episode_run_time', [])
        if episodeRunTime:
            duration = episodeRunTime[0]
    status = item.get('status', '')
    numberOfSeasons = item.get('number_of_seasons', 0)
    numberOfEpisodes = item.get('number_of_episodes', 0)

    poster = f'https://images.tmdb.org/t/p/w500{posterPath}' if posterPath else ''
    backdrop = f'https://images.tmdb.org/t/p/w1280{backdropPath}' if backdropPath else ''

    return {
        'title': title,
        'overview': overview,
        'poster': poster,
        'backdrop': backdrop,
        'year': year,
        'genre': genre,
        'cast': cast,
        'director': director,
        'writer': writer,
        'rating': str(round(rating, 1)) if rating else '',
        'mediaType': mediaType,
        'tmdbId': item.get('id', ''),
        'tagline': tagline,
        'premiered': releaseDate,
        'duration': duration,
        'status': status,
        'numberOfSeasons': numberOfSeasons,
        'numberOfEpisodes': numberOfEpisodes,
    }


def fetchSeasons(tmdbId):
    url = _tmdbUrl(f'/tv/{tmdbId}')
    resp = get(url, timeoutKey='detail')
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    seasons = data.get('seasons', [])
    result = []
    showName = data.get('name', '')
    showPosterPath = data.get('poster_path', '')
    showBackdropPath = data.get('backdrop_path', '')
    showPoster = f'https://images.tmdb.org/t/p/w500{showPosterPath}' if showPosterPath else ''
    showBackdrop = f'https://images.tmdb.org/t/p/w1280{showBackdropPath}' if showBackdropPath else ''
    for s in seasons:
        sn = s.get('season_number', 0)
        if sn == 0:
            continue
        posterPath = s.get('poster_path', '')
        poster = f'https://images.tmdb.org/t/p/w500{posterPath}' if posterPath else showPoster
        airDate = s.get('air_date', '')
        year = airDate[:4] if airDate and len(airDate) >= 4 else ''
        result.append({
            'season': sn,
            'title': s.get('name', f'第{sn}季'),
            'overview': s.get('overview', ''),
            'poster': poster,
            'backdrop': showBackdrop,
            'episodeCount': s.get('episode_count', 0),
            'year': year,
            'premiered': airDate,
            'showName': showName,
            'showPoster': showPoster,
            'showBackdrop': showBackdrop,
            'tmdbId': tmdbId,
        })
    return result


def fetchEpisodes(tmdbId, seasonNumber):
    url = _tmdbUrl(f'/tv/{tmdbId}/season/{seasonNumber}')
    resp = get(url, timeoutKey='detail')
    if not resp:
        return []
    try:
        data = resp.json()
    except Exception:
        return []
    episodes = data.get('episodes', [])
    seasonPosterPath = data.get('poster_path', '')
    seasonPoster = f'https://images.tmdb.org/t/p/w500{seasonPosterPath}' if seasonPosterPath else ''
    result = []
    for ep in episodes:
        en = ep.get('episode_number', 0)
        if en == 0:
            continue
        stillPath = ep.get('still_path', '')
        thumb = f'https://images.tmdb.org/t/p/w1280{stillPath}' if stillPath else ''
        airDate = ep.get('air_date', '')
        year = airDate[:4] if airDate and len(airDate) >= 4 else ''
        runtime = ep.get('runtime', 0) or 0
        result.append({
            'season': seasonNumber,
            'episode': en,
            'title': ep.get('name', ''),
            'overview': ep.get('overview', ''),
            'thumb': thumb,
            'rating': ep.get('vote_average', 0),
            'year': year,
            'premiered': airDate,
            'duration': runtime,
            'showName': ep.get('show_name', data.get('name', '')),
            'tmdbId': tmdbId,
            'seasonPoster': seasonPoster,
        })
    return result