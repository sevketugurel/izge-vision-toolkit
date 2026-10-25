from concurrent.futures import ThreadPoolExecutor

from izge import admin_overlay as overlay


def test_comments_are_append_only_and_scoped_under_concurrent_writers(tmp_path, monkeypatch):
    monkeypatch.setenv("IZGE_ADMIN_OVERLAY", str(tmp_path / "overlay.sqlite3"))
    overlay.append("a", "comment", {"text": "first"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda n: overlay.append("a", "comment", {"text": str(n)}), range(8)))
    overlay.append("b", "comment", {"text": "other"})
    assert len(overlay.entries("a")) == 9
    assert len({r["id"] for r in overlay.entries("a")}) == 9
    assert len(overlay.entries("b")) == 1
    assert (tmp_path / "overlay.sqlite3").stat().st_mode & 0o777 == 0o600
