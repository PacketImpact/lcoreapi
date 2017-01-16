from datetime import datetime, timedelta
import json
import requests
from requests import exceptions as rexc
from urllib.parse import quote as _quote

__all__ = ['API', 'APIError', 'APIServerError', 'APIAuthError',
           'APINotFoundError', 'APIMethodNotAllowedError',
           'APIBadRequestError', 'BASE_URL', 'Resource']

BASE_URL = 'https://core.lambdavpn.net/v1/'


def parse_date(s):
    """ Parse API returned datetimes, handling multiple formats and
    compatibility. It's ISO 8601, with or without microseconds.
    It used to have no TZ, now has UTC "Z".
    """
    if not s:
        return None

    s = s.replace('+00:00', 'Z')

    formats = [
        "%Y-%m-%dT%H:%M:%S.%fZ",  # UTC, with microseconds
        "%Y-%m-%dT%H:%M:%SZ",  # UTC
        "%Y-%m-%dT%H:%M:%S.%f",  # with microseconds
        "%Y-%m-%dT%H:%M:%S",
    ]

    for f in formats:
        try:
            return datetime.strptime(s, f)
        except ValueError:
            pass

    raise ValueError("Unknown date format: %r" % s)


def dumps(data, **kwargs):
    def default(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError()
    return json.dumps(data, default=default, **kwargs)


def quote(v):
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (str, bytes)):
        return _quote(v)
    if isinstance(v, datetime):
        return str(v.utctimestamp())


def append_qs(url, **filters):
    if filters:
        if '?' in url:
            url += '&'
        else:
            url += '?'
        url += '&'.join(k + '=' + quote(v) for k, v in sorted(filters.items()))
    return url


class APIError(Exception):
    pass


class APIServerError(APIError):
    pass


class APIAuthError(APIError):
    pass


class APINotFoundError(APIError):
    pass


class APIMethodNotAllowedError(APIError):
    pass


class APIBadRequestError(APIError):
    pass


class Resource(dict):
    def __init__(self, api, data):
        self.api = api

        # Flag to prevent repeating 404's
        self.__loaded = False

        # Response preprocessing
        for k, v in data.items():
            if k.endswith('_date') or k == 'date':
                data[k] = parse_date(v)
                continue

            if isinstance(v, dict):
                data[k] = Resource(api, v)
                continue

            if isinstance(v, list):
                for n, item in enumerate(v):
                    if isinstance(item, dict):
                        v[n] = Resource(api, item)
                        continue

        super().__init__(data)

    @property
    def id(self):
        return self.get('id')

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            # May need loading
            if self.__loaded:
                raise
            if set(self.keys()).issubset({'object', 'href', 'id'}) and self.get('href'):
                self.update(self.api.get(self['href']))
                self.__loaded = True
                return super().__getitem__(key)

    def list_iter(self):
        """ Iterator for "list" objects """
        assert self.get('object') == 'list'

        o = self
        while o and o['items']:
            for item in o['items']:
                yield item

            o = self.get('next')

    def __str__(self):
        cn = self.__class__.__name__
        return "<{cn}#{self.id} on API({self.api!s})>".format(self=self, cn=cn)

    def __repr__(self):
        cn = self.__class__.__name__
        encoded = dumps(self, sort_keys=True, indent=2)
        return "<{cn}#{self.id} on API({self.api!s}) {encoded}>" \
               .format(self=self, cn=cn, encoded=encoded)


class API:
    def __init__(self, key_id, secret, base_url=BASE_URL, timeout=10):
        self.auth = (key_id, secret)
        self.base_url = base_url
        self.timeout = timeout

        self._cache = dict()
        self._cache_ttl = timedelta(minutes=5)

    @property
    def public_key(self):
        return self.auth[0]

    @property
    def info(self):
        return self.get('/meow')

    def _query(self, method, url, data=None):
        r_kwargs = {}
        r_kwargs['auth'] = self.auth

        # POST data= args as JSON instead of form data.
        # the API should accept both, but JSON is better for nested stuff.
        if data is not None:
            r_kwargs['data'] = dumps(data)
            r_kwargs['headers'] = {'Content-Type': 'application/json'}

        r_kwargs['timeout'] = self.timeout

        try:
            req = method(url, **r_kwargs)
            data = Resource(self, req.json())
        except ValueError as e:
            try:
                print(req.text)
            except:
                pass
            raise APIError("Invalid response content from %r on %r" % (self, url)) from e
        except rexc.RequestException as e:
            raise APIError("Error connecting to %r" % self) from e

        if req.status_code == 200 or req.status_code == 201:
            return data

        if req.status_code == 400:
            raise APIBadRequestError(data.get('message', "Bad request"))
        if req.status_code == 401:
            raise APIAuthError(data.get('message', "Unauthorized"))
        if req.status_code == 403:
            raise APIAuthError(data.get('message', "Forbidden"))
        if req.status_code == 404:
            raise APINotFoundError(data.get('message', "Not found"))
        if req.status_code == 405:
            raise APIMethodNotAllowedError(data.get('message', "Method not allowed"))
        if req.status_code >= 500 and req.status_code <= 599:
            raise APIServerError(data.get('message', "Unknown server error"))

        err_type = data.get('error')
        err_msg = data.get('message')
        raise APIError("Unknown error {}: {} ({})"
                       .format(req.status_code, err_type, err_msg))

    def build_url(self, url, **kwargs):
        if url.startswith('/'):
            url = self.base_url + url[1:]

        url = append_qs(url, **kwargs)
        return url

    def get(self, url, **kwargs):
        url = self.build_url(url, **kwargs)

        if self._cache is not None:
            # Clear cache
            cache_limit = datetime.now() - self._cache_ttl
            for k, v in list(self._cache.items()):
                if v[0] < cache_limit:
                    del self._cache[k]
            if url in self._cache:
                return self._cache[url][1]

        data = self._query(requests.get, url)

        if self._cache is not None:
            self._cache[url] = (datetime.now(), data)

        return data

    def post(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        return self._query(requests.post, url, data=data)

    def put(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        return self._query(requests.put, url, data=data)

    def patch(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        return self._query(requests.patch, url, data=data)

    def delete(self, url, **kwargs):
        url = self.build_url(url, **kwargs)
        return self._query(requests.delete, url)

    def __str__(self):
        return "{self.public_key} on {self.base_url}".format(self=self)

    def __repr__(self):
        cn = self.__class__.__name__
        return "<{cn}({self!s})>".format(cn=cn, self=self)

