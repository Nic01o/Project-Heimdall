"""Unit tests for the JSON key/value store used to persist alarms and
module settings across daemon restarts.
"""

from alarmclock.core.persistence import JSONStore


def test_get_missing_key_returns_default(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    assert store.get("alarms", []) == []
    assert store.get("nonexistent") is None


def test_get_on_missing_file_returns_default(tmp_path):
    store = JSONStore(tmp_path / "not-yet-written" / "state.json")
    assert store.get("alarms", []) == []


def test_set_then_get_round_trip(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    store.set("alarms", [{"id": "a1", "label": "Wake up"}])
    assert store.get("alarms") == [{"id": "a1", "label": "Wake up"}]


def test_set_creates_parent_directories(tmp_path):
    path = tmp_path / "nested" / "dir" / "state.json"
    store = JSONStore(path)
    store.set("alarms", [])
    assert path.exists()


def test_independent_keys_do_not_clobber_each_other(tmp_path):
    store = JSONStore(tmp_path / "state.json")
    store.set("alarms", [{"id": "a1"}])
    store.set("modules.light", {"brightness": 80})

    assert store.get("alarms") == [{"id": "a1"}]
    assert store.get("modules.light") == {"brightness": 80}


def test_a_new_store_instance_sees_previously_persisted_data(tmp_path):
    path = tmp_path / "state.json"
    JSONStore(path).set("modules.light", {"brightness": 50})

    reloaded = JSONStore(path)
    assert reloaded.get("modules.light") == {"brightness": 50}
