import unittest 
from flask import Flask, Response
from flask_preserve import LocalPreserve
import gzip

class TestPreserve(unittest.TestCase):
    def test_cache_key_generation(self):
        preserve = LocalPreserve(cache_prefix="test")
        key = preserve.cache_key("/test/path", "gzip")
        self.assertEqual(key, "test:response:gzip:/test/path")

    def test_cache_value_generation_and_parsing(self):
        preserve = LocalPreserve()
        response = Response("Hello, World!", status=200, mimetype='text/plain')
        value = preserve.cache_value(response)
        status_code, mimetype, body = preserve.parse_cache_value(value)
        self.assertEqual(status_code, 200)
        self.assertEqual(mimetype, 'text/plain')
        self.assertEqual(body, b"Hello, World!")


    def test_cache_value_parsing_invalid_format(self):
        preserve = LocalPreserve()
        self.assertRaises(ValueError, preserve.parse_cache_value, None)  # Empty value
        self.assertRaises(ValueError, preserve.parse_cache_value, b"")  # Empty value
        self.assertRaises(ValueError, preserve.parse_cache_value, b"f3")  # Too short
        self.assertRaises(ValueError, preserve.parse_cache_value, b"InvalidFormat") # No meta data
        self.assertRaises(ValueError, preserve.parse_cache_value, b"12.3:text/plain\nHello, World!")  # Invalid status code
        self.assertRaises(ValueError, preserve.parse_cache_value, b"200text/plain\nHello, World!")  # Missing colon

            

class TestLocalPreserve(unittest.TestCase):
    def test_simple(self):
        app = Flask(__name__)
        preserve = LocalPreserve()

        @app.route('/test')
        @preserve.preserve_response()
        def test_route():
            return Response("Hello, World!", status=200, mimetype='text/plain')

        with app.test_client() as client:
            # First request should not be cached
            response1 = client.get('/test')
            self.assertEqual(response1.status_code, 200)
            self.assertEqual(response1.data, b"Hello, World!")
            self.assertEqual(response1.mimetype, 'text/plain')
            self.assertEqual(response1.headers['App-Cache'], 'MISS')
            self.assertIsNone(response1.headers["Content-Encoding"])

            # Second request should be cached
            response2 = client.get('/test')
            self.assertEqual(response2.status_code, 200)
            self.assertEqual(response2.data, b"Hello, World!")
            self.assertEqual(response2.mimetype, 'text/plain')
            self.assertEqual(response2.headers['App-Cache'], 'HIT')
            self.assertIsNone(response2.headers["Content-Encoding"])

    def test_simple_compression(self):
        app = Flask(__name__)
        preserve = LocalPreserve()

        @app.route('/test')
        @preserve.preserve_response(compress=True)
        def test_route():
            return Response("Hello, World!", status=200, mimetype='text/plain')

        headers = {"Accept-Encoding": "gzip"}

        with app.test_client() as client:
            # First request should not be cached
            response1 = client.get('/test', headers=headers)
            self.assertEqual(response1.status_code, 200)
            body = gzip.decompress(response1.data)
            self.assertEqual(body, b"Hello, World!")
            self.assertEqual(response1.headers["Content-Encoding"], "gzip")

            # Second request should be cached
            response2 = client.get('/test', headers=headers)
            self.assertEqual(response2.status_code, 200)
            body = gzip.decompress(response2.data)
            self.assertEqual(body, b"Hello, World!")
            self.assertEqual(response2.headers['App-Cache'], 'HIT')
            self.assertEqual(response2.headers["Content-Encoding"], "gzip")


if __name__ == '__main__':
    unittest.main()