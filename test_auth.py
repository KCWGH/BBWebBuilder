import hashlib
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import server


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(server, 'WEB_USER', 'builder'), patch.object(server, 'WEB_PASSWORD', 'test-password')]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        server.sessions.clear()
        self.client = TestClient(server.app, base_url='https://127.0.0.1/builder/', root_path='/builder')

    def login(self):
        return self.client.post('/api/auth/login', json={'username': 'builder', 'password': 'test-password'})

    def test_login_page_without_browser_auth_challenge_and_api_protection(self):
        response = self.client.get('/', follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['location'], '/builder/login')
        self.assertEqual(self.client.get('/login').status_code, 200)
        for path in ['/api/config', '/api/store/apps', '/releases/private/package.zip', '/app.js']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('www-authenticate', response.headers)

    def test_login_cookie_prefix_secure_and_http_only(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['set-cookie']
        for value in ['HttpOnly', 'Secure', 'SameSite=lax', 'Path=/builder/', 'Max-Age=43200']:
            self.assertIn(value, cookie)
        self.assertEqual(self.client.get('/api/config').status_code, 200)

    def test_invalid_credentials_no_challenge(self):
        response = self.client.post('/api/auth/login', json={'username': 'builder', 'password': 'wrong'})
        self.assertEqual(response.status_code, 401)
        self.assertNotIn('set-cookie', response.headers)
        self.assertNotIn('www-authenticate', response.headers)

    def test_logout_revokes_even_replayed_cookie(self):
        self.login()
        cookie = self.client.cookies.get(server.SESSION_COOKIE)
        self.assertEqual(self.client.post('/api/auth/logout').status_code, 200)
        response = self.client.get('/api/config', headers={'Cookie': server.SESSION_COOKIE + '=' + cookie})
        self.assertEqual(response.status_code, 401)

    def test_expired_session_rejected(self):
        self.login()
        key = hashlib.sha256(self.client.cookies.get(server.SESSION_COOKIE).encode()).hexdigest()
        server.sessions[key] = time.time() - 1
        self.assertEqual(self.client.get('/api/config').status_code, 401)
        self.assertNotIn(key, server.sessions)

    def test_foreign_origin_login_and_authenticated_logout_rejected(self):
        response = self.client.post('/api/auth/login', json={'username': 'builder', 'password': 'test-password'}, headers={'Origin': 'https://other.example'})
        self.assertEqual(response.status_code, 403)
        self.login()
        self.assertEqual(self.client.post('/api/auth/logout', headers={'Origin': 'https://other.example'}).status_code, 403)
        self.assertEqual(self.client.get('/api/config').status_code, 200)

    def test_http_proxy_https_cookie_and_basic_compatibility(self):
        client = TestClient(server.app, base_url='http://127.0.0.1/builder/', root_path='/builder')
        response = client.post('/api/auth/login', json={'username': 'builder', 'password': 'test-password'}, headers={'X-Forwarded-Proto': 'https'})
        self.assertIn('Secure', response.headers['set-cookie'])
        self.assertEqual(client.get('/api/config', auth=('builder', 'test-password')).status_code, 200)


if __name__ == '__main__':
    unittest.main()
