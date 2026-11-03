"""Offline checks for the home page's YouTube list (no network).

  py -3 -B site/tests/test_youtube_feed.py
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import youtube_feed as Y                                 # noqa: E402

results = []

def check(name, cond):
    results.append((name, bool(cond))); print(('PASS ' if cond else 'FAIL ') + name)

FEED = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
<entry><yt:videoId>AAAAAAAAAA1</yt:videoId><title>Short clip</title><link rel="alternate" href="https://www.youtube.com/shorts/AAAAAAAAAA1"/><published>2026-11-01T12:00:00+00:00</published></entry>
<entry><yt:videoId>BBBBBBBBBB2</yt:videoId><title>Newest video</title><link rel="alternate" href="https://www.youtube.com/watch?v=BBBBBBBBBB2"/><published>2026-10-31T12:00:00+00:00</published></entry>
<entry><yt:videoId>CCCCCCCCCC3</yt:videoId><title>Older video</title><link rel="alternate" href="https://www.youtube.com/watch?v=CCCCCCCCCC3"/><published>2026-10-20T12:00:00+00:00</published></entry>
</feed>"""

def lockup(vid, title):
    return {'lockupViewModel': {'contentId': vid, 'contentType': 'LOCKUP_CONTENT_TYPE_VIDEO',
                                'metadata': {'lockupMetadataViewModel': {'title': {'content': title}}}}}

PAGE = 'var ytInitialData = ' + json.dumps({'contents': [lockup('DDDDDDDDDD4', 'Page video'),
                                                         {'lockupViewModel': {'contentId': 'PLx', 'contentType': 'LOCKUP_CONTENT_TYPE_PLAYLIST'}},
                                                         lockup('DDDDDDDDDD4', 'Duplicate'), lockup('bad id', 'Bad'),
                                                         {'videoRenderer': {'videoId': 'EEEEEEEEEE5', 'title': {'runs': [{'text': 'Old layout'}]}}}]}) + ';</script>'

f = Y.clean(Y.from_feed(FEED))
check('feed: shorts skipped, newest first', [v['id'] for v in f] == ['BBBBBBBBBB2', 'CCCCCCCCCC3'])
check('feed: published date kept', f[0]['published'] == '2026-10-31')
p = Y.clean(Y.from_page(PAGE))
check('page: videos only, deduped, bad ids dropped, old layout read', [v['id'] for v in p] == ['DDDDDDDDDD4', 'EEEEEEEEEE5'])
check('page: missing data -> empty', Y.from_page('<html></html>') == [])

calls = []
def fake_get(url):
    calls.append(url)
    if url == Y.FEED:
        raise OSError('404')
    return PAGE
Y._get = fake_get; Y._state.update(videos=[], next=0.0)
v = Y.latest()
check('latest: feed fails -> page used', [x['id'] for x in v] == ['DDDDDDDDDD4', 'EEEEEEEEEE5'] and calls == [Y.FEED, Y.PAGE])
Y.latest()
check('latest: cached (no refetch)', len(calls) == 2)
Y._get = lambda url: (_ for _ in ()).throw(OSError('down')); Y._state['next'] = 0.0
check('latest: refresh fails -> last good list kept', [x['id'] for x in Y.latest()] == ['DDDDDDDDDD4', 'EEEEEEEEEE5'])

bad = [n for n, ok in results if not ok]
print(f'{len(results) - len(bad)}/{len(results)} passed')
sys.exit(1 if bad else 0)
