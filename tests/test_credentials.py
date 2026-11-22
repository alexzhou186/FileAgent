import os

import pytest

from fileagent.credentials import KeyStoreError, LocalKeyStore


def test_remembered_key_can_be_replaced_and_cleared(tmp_path):
    store = LocalKeyStore(tmp_path)
    assert store.load() == ""
    store.save("sk-original-test")
    store.save("sk-replacement-test")
    assert store.load() == "sk-replacement-test"
    assert list(tmp_path.iterdir()) == [store.path]
    store.clear()
    store.clear()
    assert not store.path.exists()


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_key_store_rejects_links(tmp_path, link):
    store = LocalKeyStore(tmp_path)
    other = tmp_path / "other"
    other.write_text("sk-original-test\n")
    other.chmod(0o600)
    if link == "symlink":
        store.path.symlink_to(other)
    else:
        os.link(other, store.path)
    for method in (store.load, store.clear, lambda: store.save("sk-replacement-test")):
        with pytest.raises(KeyStoreError):
            method()
    assert other.read_text() == "sk-original-test\n"
    assert store.path.exists()
