"""ZemDev iter2 - top products, views increment, coupons, gallery/video"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://web-creator-1881.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@zemdev.com"
ADMIN_PASSWORD = "ZemDev2026!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ---------------- Products: new fields ----------------
class TestProductsNewFields:
    def test_products_have_views_gallery_video_fields(self):
        r = requests.get(f"{BASE_URL}/api/products")
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 6
        for p in data:
            assert "views" in p and isinstance(p["views"], int)
            assert "gallery" in p and isinstance(p["gallery"], list)
            assert "video_url" in p  # may be None or str

    def test_top_products_returns_3_sorted_by_views(self):
        r = requests.get(f"{BASE_URL}/api/products/top?limit=3")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3
        # sorted desc by views
        views = [p["views"] for p in data]
        assert views == sorted(views, reverse=True), f"Not sorted desc: {views}"
        titles = [p["title"] for p in data]
        # Expected top order (per seed). Note: may shift slightly from incremental views during testing
        # Verify that the seeded top names are in the top-3
        expected_top_set = {"Base Server QBCore Optimizada", "MLO Casino & Hotel Diamond", "Sistema de Trabajos Avanzado"}
        # Allow for views drift but at minimum the top product should be Base Server (started at 198)
        assert "Base Server QBCore Optimizada" == titles[0], f"Top product not Base Server, got {titles[0]}"
        assert set(titles) == expected_top_set, f"Top-3 mismatch: {titles}"

    def test_get_product_increments_views(self):
        # find a product
        r = requests.get(f"{BASE_URL}/api/products")
        prod = next(p for p in r.json() if p["title"] == "MLO Comisaría Premium")
        pid = prod["id"]
        before = prod["views"]
        # call detail twice
        requests.get(f"{BASE_URL}/api/products/{pid}")
        requests.get(f"{BASE_URL}/api/products/{pid}")
        # check views increased on list endpoint
        r2 = requests.get(f"{BASE_URL}/api/products")
        prod2 = next(p for p in r2.json() if p["id"] == pid)
        assert prod2["views"] >= before + 2, f"Views did not increment: before={before} after={prod2['views']}"


# ---------------- Coupons ----------------
class TestCoupons:
    def test_list_coupons_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/coupons")
        assert r.status_code == 401

    def test_list_coupons_admin_has_seeded(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/coupons", headers=admin_headers)
        assert r.status_code == 200
        codes = [c["code"] for c in r.json()]
        assert "ZEMDEV10" in codes

    def test_create_coupon_no_auth(self):
        r = requests.post(f"{BASE_URL}/api/coupons", json={"code": "X1", "discount_percent": 5})
        assert r.status_code == 401

    def test_create_coupon_uppercase(self, admin_headers):
        # cleanup any leftover from previous run
        r0 = requests.get(f"{BASE_URL}/api/coupons", headers=admin_headers)
        for c in r0.json():
            if c["code"] == "TEST20":
                requests.delete(f"{BASE_URL}/api/coupons/{c['id']}", headers=admin_headers)

        r = requests.post(f"{BASE_URL}/api/coupons", headers=admin_headers,
                         json={"code": "test20", "discount_percent": 20, "max_uses": 0})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["code"] == "TEST20"
        assert d["discount_percent"] == 20
        assert "id" in d

        # verify persisted
        rl = requests.get(f"{BASE_URL}/api/coupons", headers=admin_headers)
        assert any(c["code"] == "TEST20" for c in rl.json())

    def test_create_duplicate_coupon_400(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/coupons", headers=admin_headers,
                         json={"code": "ZEMDEV10", "discount_percent": 10})
        assert r.status_code == 400

    def test_validate_valid_coupon(self):
        r = requests.post(f"{BASE_URL}/api/coupons/validate", json={"code": "ZEMDEV10"})
        assert r.status_code == 200
        d = r.json()
        assert d["code"] == "ZEMDEV10"
        assert d["discount_percent"] == 10.0

    def test_validate_lowercase_coupon(self):
        r = requests.post(f"{BASE_URL}/api/coupons/validate", json={"code": "zemdev10"})
        assert r.status_code == 200
        assert r.json()["code"] == "ZEMDEV10"

    def test_validate_invalid_coupon_404(self):
        r = requests.post(f"{BASE_URL}/api/coupons/validate", json={"code": "NOPE"})
        assert r.status_code == 404
        assert "no válido" in r.json().get("detail", "").lower() or "no v" in r.json().get("detail", "").lower()

    def test_delete_coupon(self, admin_headers):
        # create one to delete
        rc = requests.post(f"{BASE_URL}/api/coupons", headers=admin_headers,
                          json={"code": "DELME99", "discount_percent": 5})
        assert rc.status_code == 200
        cid = rc.json()["id"]

        rd = requests.delete(f"{BASE_URL}/api/coupons/{cid}", headers=admin_headers)
        assert rd.status_code == 200

        rd2 = requests.delete(f"{BASE_URL}/api/coupons/{cid}", headers=admin_headers)
        assert rd2.status_code == 404


# ---------------- Product gallery/video PUT ----------------
class TestProductGalleryVideo:
    def test_update_gallery_and_video(self, admin_headers):
        # create product
        payload = {
            "title": "TEST_GVProduct", "description": "d", "price": 10.0,
            "category": "Scripts", "image_url": "https://x.com/i.jpg",
            "features": [], "gallery": [], "video_url": None,
        }
        rc = requests.post(f"{BASE_URL}/api/products", headers=admin_headers, json=payload)
        assert rc.status_code == 200
        pid = rc.json()["id"]

        # update with gallery + video
        payload["gallery"] = ["https://a.com/1.jpg", "https://a.com/2.jpg"]
        payload["video_url"] = "https://www.youtube.com/embed/abc123"
        ru = requests.put(f"{BASE_URL}/api/products/{pid}", headers=admin_headers, json=payload)
        assert ru.status_code == 200
        d = ru.json()
        assert d["gallery"] == ["https://a.com/1.jpg", "https://a.com/2.jpg"]
        assert d["video_url"] == "https://www.youtube.com/embed/abc123"

        # verify persisted
        rg = requests.get(f"{BASE_URL}/api/products/{pid}")
        assert rg.json()["gallery"] == ["https://a.com/1.jpg", "https://a.com/2.jpg"]
        assert rg.json()["video_url"] == "https://www.youtube.com/embed/abc123"

        # cleanup
        requests.delete(f"{BASE_URL}/api/products/{pid}", headers=admin_headers)


# ---------------- Cleanup ----------------
@pytest.fixture(scope="module", autouse=True)
def cleanup(admin_headers):
    yield
    try:
        rl = requests.get(f"{BASE_URL}/api/coupons", headers=admin_headers)
        for c in rl.json():
            if c["code"] in ("TEST20", "DELME99"):
                requests.delete(f"{BASE_URL}/api/coupons/{c['id']}", headers=admin_headers)
    except Exception:
        pass
