from __future__ import unicode_literals

import logging
import threading
import urllib

from mopidy import backend
from mopidy.models import Ref, SearchResult, Track

import pykka

from spotify import Link, SpotifyError, ToplistBrowser

from mopidy_spotify import images, translator, utils

logger = logging.getLogger(__name__)

SPOTIFY_COUNTRIES = {
    'AD': 'Andorra',
    'AR': 'Argentina',
    'AT': 'Austria',
    'AU': 'Australia',
    'BE': 'Belgium',
    'CH': 'Switzerland',
    'CO': 'Colombia',
    'CY': 'Cyprus',
    'DE': 'Germany',
    'DK': 'Denmark',
    'EE': 'Estonia',
    'ES': 'Spain',
    'FI': 'Finland',
    'FR': 'France',
    'GB': 'United Kingdom',
    'GR': 'Greece',
    'HK': 'Hong Kong',
    'IE': 'Ireland',
    'IS': 'Iceland',
    'IT': 'Italy',
    'LI': 'Liechtenstein',
    'LT': 'Lithuania',
    'LU': 'Luxembourg',
    'LV': 'Latvia',
    'MC': 'Monaco',
    'MX': 'Mexico',
    'MY': 'Malaysia',
    'NL': 'Netherlands',
    'NO': 'Norway',
    'NZ': 'New Zealand',
    'PT': 'Portugal',
    'SE': 'Sweden',
    'SG': 'Singapore',
    'TR': 'Turkey',
    'TW': 'Taiwan',
    'US': 'United States'}

_API_BASE_URI = 'https://api.spotify.com/v1/search'
_SEARCH_TYPES = ['album', 'artist', 'track']


class SpotifyTrack(Track):
    """Proxy object for unloaded Spotify tracks."""
    __slots__ = ('_spotify_track', '_track')

    def __init__(self, uri=None, track=None):
        super(SpotifyTrack, self).__init__()
        if (uri and track) or (not uri and not track):
            raise AttributeError('uri or track must be provided')
        elif uri:
            self._spotify_track = Link.from_string(uri).as_track()
        elif track:
            self._spotify_track = track
        self._track = None

    @property
    def _proxy(self):
        if self._track is None:
            if not self._spotify_track.is_loaded():
                return translator.to_mopidy_track(self._spotify_track)
            self._track = translator.to_mopidy_track(self._spotify_track)
        return self._track

    def __getattribute__(self, name):
        if name.startswith('_'):
            return super(SpotifyTrack, self).__getattribute__(name)
        return self._proxy.__getattribute__(name)

    def __repr__(self):
        return self._proxy.__repr__()

    def __hash__(self):
        return hash(self._proxy.uri)

    def __eq__(self, other):
        if not isinstance(other, Track):
            return False
        return self._proxy.uri == other.uri

    def copy(self, **values):
        return self._proxy.copy(**values)

    def replace(self, **values):
        return self._proxy.replace(**values)


class SpotifyLibraryProvider(backend.LibraryProvider):

    def __init__(self, *args, **kwargs):
        super(SpotifyLibraryProvider, self).__init__(*args, **kwargs)
        self._timeout = self.backend.config['spotify']['timeout']

        # TODO: add /artists/{top/tracks,albums/tracks} and /users?
        self._root = [Ref.directory(uri='spotify:toplist:current',
                                    name='Personal top tracks'),
                      Ref.directory(uri='spotify:toplist:all',
                                    name='Global top tracks')]
        self._countries = []

        if not self.backend.config['spotify']['toplist_countries']:
            return

        self._root.append(Ref.directory(uri='spotify:toplist:countries',
                                        name='Country top tracks'))
        for code in self.backend.config['spotify']['toplist_countries']:
            code = code.upper()
            self._countries.append(Ref.directory(
                uri='spotify:toplist:%s' % code.lower(),
                name=SPOTIFY_COUNTRIES.get(code, code)))

    def browse(self, uri):
        if uri == self.root_directory.uri:
            return self._root

        variant, identifier = translator.parse_uri(uri.lower())

        if variant == 'album':
            album = Link.from_string(uri).as_album()
            album_browser = self.backend.spotify.session.browse_album(album)
            utils.wait_for_object_to_load(album_browser, self._timeout)
            return [translator.to_mopidy_track_ref(t) for t in album_browser]

        if variant == 'user':
            playlist = Link.from_string(uri).as_playlist()
            utils.wait_for_object_to_load(playlist, self._timeout)
            return [translator.to_mopidy_track_ref(t) for t in playlist]

        if variant != 'toplist':
            return []

        if identifier == 'countries':
            return self._countries

        if identifier not in ('all', 'current'):
            identifier = identifier.upper()
            if identifier not in SPOTIFY_COUNTRIES:
                return []

        result = []
        done = threading.Event()

        def callback(browser, userdata):
            for track in browser:
                result.append(translator.to_mopidy_track_ref(track))
            done.set()

        logger.debug('Performing toplist browse for %s', identifier)
        ToplistBrowser(b'tracks', bytes(identifier), callback, None)
        if not done.wait(self._timeout):
            logger.warning('%s toplist browse timed out.', identifier)

        return result

    def lookup(self, uri):
        try:
            link = Link.from_string(uri)
            if link.type() == Link.LINK_TRACK:
                return self._lookup_track(uri)
            if link.type() == Link.LINK_ALBUM:
                return self._lookup_album(uri)
            elif link.type() == Link.LINK_ARTIST:
                return self._lookup_artist(uri)
            elif link.type() == Link.LINK_PLAYLIST:
                return self._lookup_playlist(uri)
            else:
                return []
        except SpotifyError as error:
            logger.debug(u'Failed to lookup "%s": %s', uri, error)
            return []

    def _lookup_track(self, uri):
        track = Link.from_string(uri).as_track()
        utils.wait_for_object_to_load(track, self._timeout)
        if track.is_loaded():
            return [SpotifyTrack(track=track)]
        else:
            return [SpotifyTrack(uri=uri)]

    def _lookup_album(self, uri):
        album = Link.from_string(uri).as_album()
        album_browser = self.backend.spotify.session.browse_album(album)
        utils.wait_for_object_to_load(album_browser, self._timeout)
        return [SpotifyTrack(track=t) for t in album_browser]

    def _lookup_artist(self, uri):
        artist = Link.from_string(uri).as_artist()
        artist_browser = self.backend.spotify.session.browse_artist(artist)
        utils.wait_for_object_to_load(artist_browser, self._timeout)
        return [SpotifyTrack(track=t) for t in artist_browser]

    def _lookup_playlist(self, uri):
        playlist = Link.from_string(uri).as_playlist()
        utils.wait_for_object_to_load(playlist, self._timeout)
        return [SpotifyTrack(track=t) for t in playlist]

    def refresh(self, uri=None):
        pass  # TODO

    def search(self, query=None, uris=None, exact=False):
        # TODO Only return results within URI roots given by ``uris``
        # TODO Support exact search
        types=_SEARCH_TYPES
        config = self.backend.config['spotify']

        if query is None:
            logger.debug('Ignored search without query')
            return SearchResult(uri='spotify:search')

        uris = query.get('uri', [])
        if uris:
            tracks = []
            for uri in uris:
                tracks += self.lookup(uri)
            if len(uris) == 1:
                uri = uris[0]
            else:
                uri = 'spotify:search'
            return SearchResult(uri=uri, tracks=tracks)

        spotify_query = self._translate_search_query(query)

        if not spotify_query:
            logger.debug('Spotify search aborted due to empty query')
            return SearchResult(uri='spotify:search')

        uri = 'spotify:search:%s' % urllib.quote(spotify_query.encode('utf-8'))
        logger.debug('Spotify search query: %s' % spotify_query)

        if not self.backend.spotify.connected.is_set():
            logger.debug('Not connected: Spotify search cancelled')
            return SearchResult(uri='spotify:search')

        search_count = max(
            config['search_album_count'],
            config['search_artist_count'],
            config['search_track_count'])

        if search_count > 50:
            logger.warn(
                'Spotify currently allows maximum 50 search results of each type. '
                'Please set the config values spotify/search_album_count, '
                'spotify/search_artist_count and spotify/search_track_count '
                'to at most 50.')
            search_count = 50

        result = self.backend._web_client.get(_API_BASE_URI, params={
            'q': spotify_query,
            'limit': search_count,
            'market': 'from_token',
            'type': ','.join(types)})

        albums = [
            translator.web_to_album(web_album) for web_album in
            result['albums']['items'][:config['search_album_count']]
        ] if 'albums' in result else []

        artists = [
            translator.web_to_artist(web_artist) for web_artist in
            result['artists']['items'][:config['search_artist_count']]
        ] if 'artists' in result else []

        tracks = [
            translator.web_to_track(web_track) for web_track in
            result['tracks']['items'][:config['search_track_count']]
        ] if 'tracks' in result else []

        return SearchResult(
            uri=uri, albums=albums, artists=artists, tracks=tracks)

    def _translate_search_query(self, mopidy_query):
        spotify_query = []
        for (field, values) in mopidy_query.iteritems():
            if field == 'albumartist':
                # XXX Don't know of a way to search for the album's artist
                # instead of the track's artist on Spotify.
                field = 'artist'
            if field == 'track_name':
                field = 'track'
            if field == 'track_no':
                # Spotify does not support filtering by track number.
                continue
            if field == 'date':
                field = 'year'
            if not hasattr(values, '__iter__'):
                values = [values]
            for value in values:
                if field == 'any':
                    spotify_query.append(value)
                elif field == 'year':
                    value = int(value.split('-')[0])  # Extract year
                    spotify_query.append('%s:%d' % (field, value))
                else:
                    spotify_query.append('%s:"%s"' % (field, value))
        spotify_query = ' '.join(spotify_query)
        return spotify_query

    def get_images(self, uris):
        return images.get_images(self.backend._web_client, uris)
