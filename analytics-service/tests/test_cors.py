import unittest
from fastapi.testclient import TestClient
from app.cors_origins import cors_origins
from main import app


class CORSTests(unittest.TestCase):
    def test_development_supports_vite_fallback_ports(self):
        origins = cors_origins({'CORS_ORIGINS': 'http://localhost:5173'})
        self.assertIn('http://localhost:5174', origins)
        self.assertIn('http://127.0.0.1:5174', origins)
        self.assertNotIn('*', origins)

    def test_production_is_explicit(self):
        self.assertEqual(cors_origins({'NODE_ENV': 'production', 'CORS_ORIGINS': ' https://indicadores.example, '}), ['https://indicadores.example'])
        self.assertEqual(cors_origins({'NODE_ENV': 'production'}), [])

    def test_preflight_allows_local_frontend_and_rejects_external_origin(self):
        client = TestClient(app)
        headers = {'Origin': 'http://localhost:5174', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'authorization,content-type'}
        response = client.options('/v2/workspaces/restore-menu', headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], headers['Origin'])
        self.assertEqual(client.options('/v2/workspaces/restore-menu', headers={**headers, 'Origin': 'https://untrusted.example'}).status_code, 400)
