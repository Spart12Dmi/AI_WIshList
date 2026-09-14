import hashlib
import re
import shutil

import pytest

from app import main


def test_home_versions_current_assets_and_requires_revalidation(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert "Your next find." in response.text
    for name in ("styles.css", "discover.css", "app.js"):
        expected = hashlib.sha256((main.STATIC_ROOT / name).read_bytes()).hexdigest()[:16]
        url = f"/static/{name}?v={expected}"
        assert url in response.text
        asset = client.get(url)
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-cache"
        assert asset.content == (main.STATIC_ROOT / name).read_bytes()
    assert client.get("/").text == response.text


def test_refresh_clears_only_cache_then_returns_to_plain_home(client, account):
    response = client.get("/?refresh=charcoal", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.headers["clear-site-data"] == '"cache"'
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    assert client.get("/api/auth/me").json()["id"] == account["id"]
    home = client.get("/")
    assert "clear-site-data" not in home.headers
    assert "Your next find." in home.text


@pytest.mark.parametrize("name", ["styles.css", "discover.css", "app.js"])
def test_asset_edits_change_version_without_restart(client, monkeypatch, tmp_path, name):
    root = tmp_path / "static"
    shutil.copytree(main.STATIC_ROOT, root)
    monkeypatch.setattr(main, "STATIC_ROOT", root)
    before = client.get("/").text
    asset = root / name
    asset.write_bytes(asset.read_bytes() + b"\n/* Updated asset */\n")
    after = client.get("/").text
    pattern = rf'/static/{re.escape(name)}\?v=([a-f0-9]+)'
    assert re.search(pattern, before).group(1) != re.search(pattern, after).group(1)
