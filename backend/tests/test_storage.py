"""Storage abstraction: local backend and the GCS backend against an in-memory fake bucket."""
import pytest
from izge.storage import LocalStorage, GCSStorage, object_store, valid_key

KEY = 'a' * 32 + '.mp4'


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name
    def upload_from_string(self, data):
        self.store[self.name] = bytes(data)
    def upload_from_filename(self, path):
        self.store[self.name] = open(path, 'rb').read()
    def download_as_bytes(self):
        return self.store[self.name]
    def download_to_filename(self, path):
        if self.name not in self.store:
            raise FileNotFoundError(self.name)
        open(path, 'wb').write(self.store[self.name])
    def exists(self):
        return self.name in self.store
    def delete(self):
        del self.store[self.name]


class FakeBucket:
    def __init__(self):
        self.store = {}
    def blob(self, name):
        return FakeBlob(self.store, name)
    def list_blobs(self, prefix=''):
        return [FakeBlob(self.store, n) for n in sorted(self.store) if n.startswith(prefix)]


class FakeClient:
    def __init__(self):
        self.buckets = {}
    def bucket(self, name):
        return self.buckets.setdefault(name, FakeBucket())


@pytest.mark.parametrize('key, ok', [(KEY, True), ('probs/' + 'b' * 32 + '.parquet', True),
    ('crops/run/sample-ctx.jpg', True), ('embeddings/test-stub/x.npy', True), ('models/../x', False),
    ('../etc/passwd', False), ('other/x', False), ('crops/A/x.png', False), ('crops/a/b/c/d/e', False)])
def test_key_validation(key, ok):
    assert valid_key(key) is ok


def test_local_round_trip(tmp_path):
    store = LocalStorage(tmp_path)
    store.put_bytes('models/m.npz', b'x')
    assert store.get_bytes('models/m.npz') == b'x' and store.list('models') == ['models/m.npz']
    assert store.local_path('models/m.npz') == tmp_path / 'models/m.npz'
    store.delete('models/m.npz')
    assert not store.exists('models/m.npz')


def test_gcs_backend_uses_bucket_and_local_cache(tmp_path):
    client = FakeClient()
    store = GCSStorage('izge-media', 'prod', root=tmp_path / 'cache', client=client)
    bucket = client.buckets['izge-media'].store
    store.path(KEY).write_bytes(b'video')         # written locally (upload/ffmpeg), then published
    store.commit(KEY)
    assert bucket == {f'prod/{KEY}': b'video'}
    store.path(KEY).unlink()
    assert store.local_path(KEY).read_bytes() == b'video'   # downloaded into the cache on demand
    store.put_bytes('probs/r/part-0000000.parquet', b'p')
    assert store.exists('probs/r/part-0000000.parquet') and store.get_bytes('probs/r/part-0000000.parquet') == b'p'
    assert store.list('probs/r/') == ['probs/r/part-0000000.parquet']
    store.delete('probs/r/part-0000000.parquet')
    assert not store.exists('probs/r/part-0000000.parquet')
    with pytest.raises(FileNotFoundError):
        store.local_path('b' * 32 + '.mp4')
    with pytest.raises(ValueError):
        store.put_bytes('../escape', b'')


def test_factory(monkeypatch):
    monkeypatch.setenv('IZGE_STORAGE', 'gcs')
    monkeypatch.delenv('IZGE_GCS_BUCKET', raising=False)
    with pytest.raises(RuntimeError, match='IZGE_GCS_BUCKET'):
        object_store()
    monkeypatch.setenv('IZGE_STORAGE', 's3')
    with pytest.raises(RuntimeError, match='Bilinmeyen'):
        object_store()
    monkeypatch.setenv('IZGE_STORAGE', 'local')
    assert type(object_store()) is LocalStorage
