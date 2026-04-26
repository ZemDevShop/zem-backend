"""ZemDev backend API tests - auth + products CRUD"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://web-creator-1881.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@zemdev.com"
ADMIN_PASSWORD = "ZemDev2026!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data
    return data["token"]


# ---- Products public ----
class TestProductsPublic:
    def test_list_products_returns_seeded(self, session):
        r = session.get(f"{BASE_URL}/api/products")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 6, f"Expected >=6 products, got {len(data)}"
        # validate shape
        p = data[0]
        for k in ["id", "title", "price", "category", "image_url", "features"]:
            assert k in p

    def test_get_product_by_id(self, session):
        r = session.get(f"{BASE_URL}/api/products")
        pid = r.json()[0]["id"]
        r2 = session.get(f"{BASE_URL}/api/products/{pid}")
        assert r2.status_code == 200
        assert r2.json()["id"] == pid

    def test_get_product_404(self, session):
        r = session.get(f"{BASE_URL}/api/products/nonexistent-id-xyz")
        assert r.status_code == 404


# ---- Auth ----
class TestAuth:
    def test_login_success(self, session):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        d = r.json()
        assert d["email"] == ADMIN_EMAIL
        assert d["role"] == "admin"
        assert "token" in d and len(d["token"]) > 20
        # Cookie should be set
        assert "access_token" in r.cookies or any("access_token" in c.name for c in r.cookies)

    def test_login_wrong_password(self, session):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me_with_token(self, session, admin_token):
        r = session.get(f"{BASE_URL}/api/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_me_without_token(self):
        r = requests.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_logout(self, session):
        r = session.post(f"{BASE_URL}/api/auth/logout")
        assert r.status_code == 200


# ---- Products admin CRUD ----
class TestProductsAdmin:
    def test_create_without_auth(self, session):
        r = requests.post(f"{BASE_URL}/api/products", json={
            "title": "TEST_x", "description": "x", "price": 1.0,
            "category": "Scripts", "image_url": "https://x.com/i.jpg", "features": []
        })
        assert r.status_code == 401

    def test_crud_full_flow(self, admin_token):
        h = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}
        # create
        payload = {
            "title": "TEST_Producto", "description": "desc", "price": 99.5,
            "category": "Scripts", "image_url": "https://x.com/i.jpg",
            "features": ["a", "b"], "badge": "TEST"
        }
        r = requests.post(f"{BASE_URL}/api/products", headers=h, json=payload)
        assert r.status_code == 200, r.text
        prod = r.json()
        pid = prod["id"]
        assert prod["title"] == "TEST_Producto"
        assert prod["price"] == 99.5

        # GET to verify persistence
        rg = requests.get(f"{BASE_URL}/api/products/{pid}")
        assert rg.status_code == 200
        assert rg.json()["title"] == "TEST_Producto"

        # update
        payload["title"] = "TEST_Producto_Updated"
        payload["price"] = 199.99
        ru = requests.put(f"{BASE_URL}/api/products/{pid}", headers=h, json=payload)
        assert ru.status_code == 200
        assert ru.json()["title"] == "TEST_Producto_Updated"

        # verify update persisted
        rg2 = requests.get(f"{BASE_URL}/api/products/{pid}")
        assert rg2.json()["price"] == 199.99

        # delete
        rd = requests.delete(f"{BASE_URL}/api/products/{pid}", headers=h)
        assert rd.status_code == 200

        # verify deleted
        rg3 = requests.get(f"{BASE_URL}/api/products/{pid}")
        assert rg3.status_code == 404
