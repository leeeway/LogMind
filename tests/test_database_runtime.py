from unittest.mock import AsyncMock

from logmind.core import database


def test_engine_recreated_after_pid_change(monkeypatch):
    created_engines = []

    class FakeEngine:
        def __init__(self, name: str):
            self.name = name
            self.dispose = AsyncMock()

    def fake_build_engine():
        engine = FakeEngine(f"engine-{len(created_engines) + 1}")
        created_engines.append(engine)
        return engine

    def fake_sessionmaker(engine, class_=None, expire_on_commit=False):
        return {"engine": engine, "expire_on_commit": expire_on_commit}

    monkeypatch.setattr(database, "_ENGINE", None)
    monkeypatch.setattr(database, "_SESSION_FACTORY", None)
    monkeypatch.setattr(database, "_ENGINE_PID", None)
    monkeypatch.setattr(database, "_build_engine", fake_build_engine)
    monkeypatch.setattr(database, "async_sessionmaker", fake_sessionmaker)

    monkeypatch.setattr(database.os, "getpid", lambda: 100)
    engine1 = database.get_engine()
    factory1 = database.get_session_factory()

    monkeypatch.setattr(database.os, "getpid", lambda: 200)
    engine2 = database.get_engine()
    factory2 = database.get_session_factory()

    assert engine1 is not engine2
    assert factory1 is not factory2
    assert len(created_engines) == 2
