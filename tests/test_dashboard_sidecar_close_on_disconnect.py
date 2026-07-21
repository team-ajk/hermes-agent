import re
from pathlib import Path

CHAT_SIDEBAR = Path(__file__).resolve().parent.parent / "web/src/components/ChatSidebar.tsx"


def test_sidecar_session_create_requests_close_on_disconnect():
    """The sidecar must opt its session into close_on_disconnect so the gateway
    reaps the slash_worker on WS disconnect (the #21370/#21467 leak)."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8")
    # v0.19 refactored params into sidecarSessionCreateParams(); check that function.
    func = re.search(r'function sidecarSessionCreateParams.*?\{(.*?)\}\s*;\s*\}', source, re.DOTALL)
    assert func, "sidecar session.create call not found"
    assert re.search(r"close_on_disconnect:\s*true", func.group(1))


def test_sidecar_session_create_scopes_profile():
    """The sidecar must pass the dashboard's selected profile so model/credential
    info matches the PTY child under profile-scoped chat."""
    source = CHAT_SIDEBAR.read_text(encoding="utf-8")
    # v0.19 refactored params into sidecarSessionCreateParams(); check that function.
    func = re.search(r'function sidecarSessionCreateParams.*?\{(.*?)\}\s*;\s*\}', source, re.DOTALL)
    assert func, "sidecar session.create call not found"
    body = func.group(1)
    assert re.search(r"close_on_disconnect:\s*true", body)
    assert re.search(r'source:\s*"tool"', body)
    assert re.search(r"\.\.\.\(profile\s*\?\s*\{\s*profile\s*\}\s*:\s*\{\}\)", body)
