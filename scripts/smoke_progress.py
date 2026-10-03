"""Smoke test for a running server: N concurrent analyze -> progress flows, each in its own browser session.

Usage: python scripts/smoke_progress.py BASE_URL [N]      e.g.  python scripts/smoke_progress.py http://127.0.0.1:5099 8

Exit status 0 only if every flow reaches the final 'done' message. (A multi-worker gunicorn fails this test:
progress queues live in one worker's memory, so most streams answer 'Session not found'.)
The app's rate limiter allows 10 requests per minute, so N is kept at 8 or below.
"""
import http.cookiejar
import json
import sys
import threading
import urllib.request
import uuid


def multipart(fields, filename, content):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                  f'Content-Type: application/octet-stream\r\n\r\n').encode() + content + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def flow(base, content, results, index):
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    try:
        body, ctype = multipart({}, 'smoke.xlsx', content)
        request = urllib.request.Request(f'{base}/analyze', data=body, headers={'Content-Type': ctype})
        session_id = json.load(opener.open(request, timeout=30))['session_id']
        last = None
        with opener.open(f'{base}/progress/{session_id}', timeout=120) as stream:
            for line in stream:
                if line.startswith(b'data: '):
                    last = json.loads(line[6:])
                    if last.get('stage') in ('done', 'error') or last.get('error'):
                        break
        results[index] = last or {'error': 'no data'}
    except Exception as exc:  # noqa: BLE001 - report every failure mode
        results[index] = {'error': repr(exc)}


def main():
    base = sys.argv[1].rstrip('/')
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    with urllib.request.urlopen(f'{base}/generate-test-file?num_rows=300', timeout=30) as response:
        content = response.read()
    results = [None] * count
    threads = [threading.Thread(target=flow, args=(base, content, results, i)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = sum(1 for r in results if r and r.get('stage') == 'done')
    for i, r in enumerate(results):
        print(f'flow {i}: {"done" if r and r.get("stage") == "done" else "FAILED " + json.dumps(r)[:120]}')
    print(f'{ok}/{count} flows completed')
    sys.exit(0 if ok == count else 1)


if __name__ == '__main__':
    main()
