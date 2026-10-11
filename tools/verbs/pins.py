#!/usr/bin/env python3
"""Pin a sha256 for every verb download URL that winetricks gives without one.

Reads data/verbs.json, downloads each unpinned URL (and, with --refresh-rolling, the URLs
listed under "rolling", whose content changes in place) and records the hashes in pins.json.
Run convert.py again afterwards so verbs.json picks them up.
"""

import argparse
import hashlib
import json
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
USER_AGENT = 'GameNative-verbs/1 (+https://github.com/utkarshdalal/GameNative)'
MAX_BYTES = 400 * 1024 * 1024


def sha256_of(url):
    digest = hashlib.sha256()
    total = 0
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': '*/*'})
    with urllib.request.urlopen(request, timeout=120) as response:
        while True:
            block = response.read(1 << 20)
            if not block:
                break
            total += len(block)
            if total > MAX_BYTES:
                raise ValueError('%s is larger than %d bytes' % (url, MAX_BYTES))
            digest.update(block)
    return digest.hexdigest()


def unpinned(verbs_doc):
    out = []
    for verb in verbs_doc.get('verbs', {}).values():
        if verb.get('class') == 'skip':
            continue
        for group in ('files', 'preferred', 'installers', 'exec'):
            for entry in verb.get(group, []):
                if entry.get('url') and not entry.get('sha256') and entry['url'] not in out:
                    out.append(entry['url'])
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--verbs', required=True)
    ap.add_argument('--pins', default=os.path.join(HERE, 'pins.json'))
    ap.add_argument('--refresh-rolling', action='store_true')
    a = ap.parse_args(argv)
    with open(a.verbs, encoding='utf-8') as f:
        verbs_doc = json.load(f)
    with open(a.pins, encoding='utf-8') as f:
        pins = json.load(f)
    todo = [u for u in unpinned(verbs_doc) if u not in pins['sha256']]
    if a.refresh_rolling:
        todo += [u for u in pins.get('rolling', []) if u not in todo]
    failed = 0
    for url in todo:
        try:
            pins['sha256'][url] = sha256_of(url)
            print('%s %s' % (pins['sha256'][url], url))
        except (OSError, ValueError) as error:
            failed += 1
            print('%s: %s' % (url, error), file=sys.stderr)
    with open(a.pins, 'w', encoding='utf-8', newline='\n') as f:
        f.write(json.dumps(pins, indent=2, sort_keys=True) + '\n')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
