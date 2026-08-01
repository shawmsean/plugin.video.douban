import json
import re
import urllib.parse
from resources.lib.httpClient import get
from resources.lib.logger import logInfo, logError

_HEADERS = {
    'Accept': '*/*',

    'Connection': 'keep-alive',
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Referer': 'https://movie.douban.com/explore',
}

CATEGORIES = [
    {'id': 'movie', 'name': '选电影'},
    {'id': 'tv', 'name': '选剧集'},
    {'id': 'show', 'name': '选综艺'},
    {'id': 'movie_filter', 'name': '电影筛选'},
    {'id': 'tv_filter', 'name': '电视剧筛选'},
    {'id': 'show_filter', 'name': '综艺筛选'},
]

MOVIE_CATEGORIES = [
    {'name': '热门', 'value': '热门'},
    {'name': '最新', 'value': '最新'},
    {'name': '豆瓣高分', 'value': '豆瓣高分'},
    {'name': '冷门佳片', 'value': '冷门佳片'},
]

MOVIE_REGIONS = [
    {'name': '全部', 'value': '全部'},
    {'name': '华语', 'value': '华语'},
    {'name': '欧美', 'value': '欧美'},
    {'name': '韩国', 'value': '韩国'},
    {'name': '日本', 'value': '日本'},
]

TV_TYPES = [
    {'name': '综合', 'value': 'tv'},
    {'name': '国产剧', 'value': 'tv_domestic'},
    {'name': '欧美剧', 'value': 'tv_american'},
    {'name': '日剧', 'value': 'tv_japanese'},
    {'name': '韩剧', 'value': 'tv_korean'},
    {'name': '动漫', 'value': 'tv_animation'},
    {'name': '纪录片', 'value': 'tv_documentary'},
]

SHOW_TYPES = [
    {'name': '综合', 'value': 'show'},
    {'name': '国内', 'value': 'show_domestic'},
    {'name': '国外', 'value': 'show_foreign'},
]

MOVIE_FILTER_GENRES = [
    '', '喜剧', '爱情', '动作', '科幻', '动画', '悬疑', '犯罪', '惊悚',
    '冒险', '音乐', '历史', '奇幻', '恐怖', '战争', '传记', '歌舞',
    '武侠', '情色', '灾难', '西部', '纪录片', '短片',
]

TV_FILTER_GENRES = [
    '', '喜剧', '爱情', '悬疑', '动画', '武侠', '古装', '家庭', '犯罪',
    '科幻', '恐怖', '历史', '战争', '动作', '冒险', '传记', '剧情',
    '奇幻', '惊悚', '灾难', '歌舞', '音乐',
]

SHOW_FILTER_GENRES = [
    '', '真人秀', '脱口秀', '音乐', '歌舞',
]

FILTER_REGIONS = [
    '', '华语', '欧美', '韩国', '日本', '中国大陆', '美国', '中国香港',
    '中国台湾', '英国', '法国', '德国', '意大利', '西班牙', '印度',
    '泰国', '俄罗斯', '加拿大', '澳大利亚', '爱尔兰', '瑞典', '巴西', '丹麦',
]

TV_FILTER_REGIONS = [
    '', '华语', '欧美', '国外', '韩国', '日本', '中国大陆', '中国香港',
    '美国', '英国', '泰国', '中国台湾', '意大利', '法国', '德国',
    '西班牙', '俄罗斯', '瑞典', '巴西', '丹麦', '印度', '加拿大',
    '爱尔兰', '澳大利亚',
]

FILTER_YEARS = [
    '', '2026', '2025', '2024', '2023', '2022', '2021', '2020', '2019',
    '2020年代', '2010年代', '2000年代', '90年代', '80年代', '70年代', '60年代', '更早',
]

FILTER_SORTS = [
    {'name': '热度', 'value': 'U'},
    {'name': '评分', 'value': 'S'},
    {'name': '时间', 'value': 'R'},
]

TV_FILTER_PLATFORMS = [
    '', '腾讯视频', '爱奇艺', '优酷', '湖南卫视', 'Netflix', 'HBO',
    'BBC', 'NHK', 'CBS', 'NBC', 'tvN',
]


def fetchRecentHot(categoryId, category='', vtype='', start=0, limit=20):
    if categoryId == 'movie':
        cat = category or '热门'
        tp = vtype or '全部'
        url = f'https://m.douban.com/rexxar/api/v2/subject/recent_hot/movie?start={start}&limit={limit}&category={urllib.parse.quote(cat)}&type={urllib.parse.quote(tp)}'
        referer = 'https://movie.douban.com/explore'
    else:
        cat = categoryId
        tp = vtype or ('tv' if categoryId == 'tv' else 'show')
        url = f'https://m.douban.com/rexxar/api/v2/subject/recent_hot/tv?start={start}&limit={limit}&category={urllib.parse.quote(cat)}&type={urllib.parse.quote(tp)}'
        referer = 'https://movie.douban.com/tv/'

    headers = dict(_HEADERS)
    headers['Referer'] = referer
    logInfo(f"fetchRecentHot URL: {url}")
    resp = get(url, headers=headers, timeoutKey='search')
    if not resp:
        logError(f"fetchRecentHot: 请求失败, url={url[:80]}")
        return [], 0
    try:
        data = resp.json()
    except Exception as e:
        logError(f"fetchRecentHot: JSON解析失败: {e}, body={resp.text[:200]}")
        return [], 0
    items = data.get('items', [])
    total = data.get('total', 0)
    logInfo(f"fetchRecentHot: 返回 {len(items)} 项, total={total}")
    result = [_parseItem(item, categoryId) for item in items]
    return result, total


def fetchRecommend(categoryId, genre='', region='', year='', sort='U', platform='', start=0, limit=20):
    if categoryId == 'movie_filter':
        selectedCategories = {}
        if genre:
            selectedCategories['类型'] = genre
        if region:
            selectedCategories['地区'] = region
        selectedCategoriesStr = json.dumps(selectedCategories, ensure_ascii=False)
        tagsArray = []
        if genre:
            tagsArray.append(genre)
        if region:
            tagsArray.append(region)
        if year:
            tagsArray.append(year)
        tags = ','.join(tagsArray)
        url = (f'https://m.douban.com/rexxar/api/v2/movie/recommend?refresh=0&start={start}&count={limit}'
               f'&selected_categories={urllib.parse.quote(selectedCategoriesStr)}'
               f'&uncollect=false&score_range=0,10'
               f'&tags={urllib.parse.quote(tags)}&sort={sort}')
        referer = 'https://movie.douban.com/explore'
    else:
        formType = '电视剧' if categoryId == 'tv_filter' else '综艺'
        selectedCategories = {'形式': formType}
        if genre:
            selectedCategories['类型'] = genre
        if region:
            selectedCategories['地区'] = region
        selectedCategoriesStr = json.dumps(selectedCategories, ensure_ascii=False)
        tagsArray = []
        if genre:
            tagsArray.append(genre)
        if region:
            tagsArray.append(region)
        if year:
            tagsArray.append(year)
        if platform:
            tagsArray.append(platform)
        tags = ','.join(tagsArray)
        url = (f'https://m.douban.com/rexxar/api/v2/tv/recommend?refresh=0&start={start}&count={limit}'
               f'&selected_categories={urllib.parse.quote(selectedCategoriesStr)}'
               f'&uncollect=false&score_range=0,10'
               f'&tags={urllib.parse.quote(tags)}&sort={sort}')
        referer = 'https://movie.douban.com/tv/'

    headers = dict(_HEADERS)
    headers['Referer'] = referer
    logInfo(f"fetchRecommend URL: {url}")
    resp = get(url, headers=headers, timeoutKey='search')
    if not resp:
        logError(f"fetchRecommend: 请求失败, url={url[:80]}")
        return [], 0
    try:
        data = resp.json()
    except Exception as e:
        logError(f"fetchRecommend: JSON解析失败: {e}, body={resp.text[:200]}")
        return [], 0
    items = data.get('items', [])
    total = data.get('total', 0)
    result = [_parseItem(item, categoryId) for item in items]
    return result, total


def _parseItem(item, categoryId):
    cardSubtitle = item.get('card_subtitle', '')
    year = ''
    if cardSubtitle:

        yearMatch = re.match(r'^(\d{4})', cardSubtitle)
        if yearMatch:
            year = yearMatch.group(1)

    episodesInfo = item.get('episodes_info', '').strip()
    isNew = item.get('is_new', False)
    meta = episodesInfo if episodesInfo else ('新片' if isNew and categoryId == 'movie' else ('新剧' if isNew else ''))

    pic = item.get('pic', {})
    poster = pic.get('large', '') or pic.get('normal', '') if pic else ''

    subtitle = ''
    if cardSubtitle:
        parts = cardSubtitle.split(' / ')
        if len(parts) > 1:
            subtitle = ' / '.join(parts[1:])

    rating = item.get('rating', {})
    ratingValue = ''
    if rating and rating.get('value'):
        ratingValue = str(rating['value'])

    return {
        'id': str(item.get('id', '')),
        'title': item.get('title', ''),
        'poster': poster,
        'year': year,
        'rating': ratingValue,
        'meta': meta,
        'subtitle': subtitle,
        'categoryId': categoryId,
    }