import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import is_allowed_url, safe_filename

def test_allowed_domains():
    assert is_allowed_url("https://www.youtube.com/watch?v=abc")
    assert is_allowed_url("https://www.tiktok.com/@user/video/123")
    assert is_allowed_url("https://www.instagram.com/reel/abc/")
    assert not is_allowed_url("https://example.com/video")

def test_filename_sanitization():
    name = safe_filename("../../hello:world?.mp4")
    assert "/" not in name
    assert "\\" not in name
    assert ".." not in name
