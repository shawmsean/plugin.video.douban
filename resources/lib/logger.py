import xbmc
import xbmcaddon

LOG_PREFIX = '[DOUBAN]'


def logInfo(msg):
    xbmc.log(f'{LOG_PREFIX} {msg}', xbmc.LOGINFO)


def logError(msg):
    xbmc.log(f'{LOG_PREFIX} {msg}', xbmc.LOGERROR)