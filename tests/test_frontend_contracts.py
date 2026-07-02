from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_rag_api_matches_backend_route_prefix():
    services_ts = (ROOT / "frontend/src/api/services.ts").read_text()
    knowledge_page = (ROOT / "frontend/src/pages/Knowledge/index.tsx").read_text()

    assert "/knowledge-base" in services_ts
    assert "/knowledge-bases" not in services_ts
    assert "ragApi.uploadDoc" in knowledge_page
    assert "<Upload" in knowledge_page


def test_frontend_websocket_urls_support_dev_proxy_and_https():
    live_tail = (ROOT / "frontend/src/pages/Logs/LiveTail.tsx").read_text()
    vite_config = (ROOT / "frontend/vite.config.ts").read_text()

    assert "window.location.protocol === 'https:' ? 'wss:' : 'ws:'" in live_tail
    assert "'/ws'" in vite_config
