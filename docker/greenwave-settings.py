SECRET_KEY = 'greenwave'
HOST = '0.0.0.0'
PORT = 8080
DEBUG = True
POLICIES_DIR = '/etc/greenwave/policies/'
WAIVERDB_API_URL = 'http://waiverdb:5004/api/v1.0'
RESULTSDB_API_URL = 'http://resultsdb:5001/api/v2.0'
GREENWAVE_API_URL = 'http://dev:8080/api/v1.0'
CACHE = {
    # 'backend': "dogpile.cache.null",
    'backend': "dogpile.cache.memcached",
    'expiration_time': 1,  # 1 is 1 second, keep to see that memcached
                           # service is working
    'arguments': {
        'url': 'memcached:11211',
        'distributed_lock': True
    }
}
