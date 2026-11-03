"""Latest Implied Investing YouTube uploads for the home page (public; no API key).

Tries the channel's RSS feed first, then the public /videos page (the feed has returned 404 for this channel at times).
Shorts are skipped. The list is cached for 3 hours; a failed refresh keeps the last good list and retries in 15 minutes.
Until the first success it returns [], and the page keeps its built-in list.
"""
import json, re, threading, time, urllib.request
from xml.etree import ElementTree as ET

CHANNEL_ID = 'UCBfnRJOy8G5LEkXGwvSFxJQ'
FEED = f'https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}'
PAGE = 'https://www.youtube.com/@impliedinvesting/videos'
TTL, RETRY, LIMIT = 3 * 3600, 15 * 60, 3
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; ImpliedInvesting/1.0)', 'Accept-Language': 'en-US,en;q=0.9'}
_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
_lock = threading.Lock()
_state = dict(videos=[], next=0.0)


def _get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=8) as r:
        return r.read().decode('utf8', 'replace')


def from_feed(xml):
    ns = {'a': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
    out = []
    for e in ET.fromstring(xml).findall('a:entry', ns):
        link = (e.find('a:link', ns).get('href') if e.find('a:link', ns) is not None else '') or ''
        if '/shorts/' in link:
            continue
        out.append(dict(id=e.findtext('yt:videoId', '', ns), title=e.findtext('a:title', '', ns),
                        published=(e.findtext('a:published', '', ns) or '')[:10]))
    return out


def from_page(html):
    m = re.search(r'var ytInitialData = (\{.*?\});</script>', html)
    if not m:
        return []
    out = []

    def walk(o):
        if isinstance(o, dict):
            L = o.get('lockupViewModel')
            if isinstance(L, dict) and L.get('contentType') == 'LOCKUP_CONTENT_TYPE_VIDEO':
                md = (L.get('metadata') or {}).get('lockupMetadataViewModel') or {}
                out.append(dict(id=L.get('contentId'), title=(md.get('title') or {}).get('content'), published=''))
            R = o.get('videoRenderer')                          # older page layout
            if isinstance(R, dict):
                t = R.get('title') or {}
                out.append(dict(id=R.get('videoId'), title=(t.get('runs') or [{}])[0].get('text') or t.get('simpleText'), published=''))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(m.group(1)))
    return out


def clean(videos):
    seen, out = set(), []
    for v in videos:
        vid, title = str(v.get('id') or ''), str(v.get('title') or '').strip()
        if _ID.match(vid) and title and vid not in seen:
            seen.add(vid); out.append(dict(id=vid, title=title[:200], published=v.get('published') or ''))
    return out[:LIMIT]


def latest():
    """Newest uploads, newest first: [{id, title, published}] (published may be '')."""
    now = time.time()
    if now < _state['next']:
        return _state['videos']
    with _lock:
        if time.time() < _state['next']:                       # another request refreshed while we waited
            return _state['videos']
        videos = []
        for url, parse in ((FEED, from_feed), (PAGE, from_page)):
            try:
                videos = clean(parse(_get(url)))
            except Exception:
                videos = []
            if videos:
                break
        if videos:
            _state.update(videos=videos, next=time.time() + TTL)
        else:
            _state['next'] = time.time() + RETRY
        return _state['videos']
