from datetime import datetime, timedelta
import json
import requests
from urllib.parse import quote as _quote

__all__ = ['API', 'APIError', 'APIServerError', 'APIAuthError', 'BASE_URL', 'Resource']

BASE_URL = 'https://core.lambdavpn.net/v1/'


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        raise
        return s


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
    def __init__(self, key_id, secret, base_url=BASE_URL):
        self.auth = (key_id, secret)
        self.base_url = base_url

        self._cache = dict()
        self._cache_ttl = timedelta(minutes=5)

        self.info = self.get('/meow')

    @property
    def public_key(self):
        return self.auth[0]

    def _raise_for_error(self, data):
        error_type = data.get('error')

        if not error_type:
            return

        if error_type == 'auth_error':
            raise APIAuthError(data.get('message', "Unknown auth error."))
        if error_type == 'server_error':
            raise APIServerError(data.get('message', "Unknown server error."))
        else:
            raise APIError("Unknown error: {} ({})"
                           .format(error_type, data.get('message')))

    def _query(self, method, url, *args, **kwargs):
        kwargs['auth'] = self.auth
        try:
            req = method(url, *args, **kwargs)
            data = Resource(self, req.json())
        except ValueError as e:
            try:
                print(req.text)
            except:
                pass
            raise APIError("Invalid response content from %r on %r" % (self, url)) from e
        except requests.exceptions.ConnectionError as e:
            raise APIError("Error connecting to %r" % self) from e

        self._raise_for_error(data)

        return data

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
        data = self._query(requests.post, url, data=data)
        return Resource(self, data)

    def put(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        data = self._query(requests.put, url, data=data)
        return Resource(self, data)

    def patch(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        data = self._query(requests.patch, url, data=data)
        return Resource(self, data)

    def delete(self, url, data, **kwargs):
        url = self.build_url(url, **kwargs)
        data = self._query(requests.delete, url, data=data)
        return Resource(self, data)

    def __str__(self):
        return "{self.public_key} on {self.base_url}".format(self=self)

    def __repr__(self):
        cn = self.__class__.__name__
        return "<{cn}({self!s})>".format(cn=cn, self=self)

