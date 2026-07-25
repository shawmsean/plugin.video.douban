import requests
from resources.lib.logger import logInfo, logError

TIMEOUTS = {
    'default': (10, 30),
    'search': (10, 30),
    'detail': (10, 30),
}


def get(url, headers=None, cookies=None, timeoutKey='default'):
    return _request('GET', url, headers=headers, cookies=cookies,
                    timeoutKey=timeoutKey, allow_redirects=True)


def post(url, data=None, json=None, headers=None, cookies=None, timeoutKey='default'):
    return _request('POST', url, data=data, json=json, headers=headers,
                    cookies=cookies, timeoutKey=timeoutKey)


def _request(method, url, headers=None, cookies=None, timeoutKey='default', **kwargs):
    timeout = TIMEOUTS.get(timeoutKey, (10, 30))
    try:
        resp = requests.request(method, url, headers=headers, cookies=cookies,
                                timeout=timeout, **kwargs)
        return resp
    except requests.Timeout:
        logError(f"请求超时: {url[:80]}")
        return None
    except requests.ConnectionError:
        logError(f"连接失败: {url[:80]}")
        return None
    except Exception as e:
        logError(f"请求异常: {e}")
        return None