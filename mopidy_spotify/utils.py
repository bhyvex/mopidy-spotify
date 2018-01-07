from __future__ import unicode_literals

import locale
import logging
import time

from mopidy import httpclient

import requests

from mopidy_spotify import Extension, __version__

logger = logging.getLogger(__name__)


def locale_decode(bytestr):
    try:
        return unicode(bytestr)
    except UnicodeError:
        return bytes(bytestr).decode(locale.getpreferredencoding())


def wait_for_object_to_load(spotify_obj, timeout):
    # XXX Sleeping to wait for the Spotify object to load is an ugly hack,
    # but it works. We should look into other solutions for this.
    wait_until = time.time() + timeout
    while not spotify_obj.is_loaded():
        time.sleep(0.1)
        if time.time() > wait_until:
            logger.debug(
                'Timeout: Spotify object did not load in %ds', timeout)
            return


def get_requests_session(proxy_config):
    user_agent = '%s/%s' % (Extension.dist_name, __version__)
    proxy = httpclient.format_proxy(proxy_config)
    full_user_agent = httpclient.format_user_agent(user_agent)

    session = requests.Session()
    session.proxies.update({'http': proxy, 'https': proxy})
    session.headers.update({'user-agent': full_user_agent})

    return session
