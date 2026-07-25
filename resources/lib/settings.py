import xbmcaddon

_cache = {}


def _getSetting(key, default=''):
    if key not in _cache:
        addon = xbmcaddon.Addon()
        _cache[key] = addon.getSetting(key) or default
    return _cache[key]


def getSetting(key, default=''):
    return _getSetting(key, default)


def getTmdbApiKey():
    return _getSetting('tmdb_api_key', 'd7040155454e7fdf547c4d889ebbcca7')


def getTmdbLanguage():
    return _getSetting('tmdb_language', 'zh-CN')


def reload():
    _cache.clear()