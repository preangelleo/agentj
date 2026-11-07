"""Page-local, serialized media scans. History and owner routes keep their authorities."""
import asyncio
import copy
import re
from . import media, privacy


def explicit(text):
    return '\n'.join(m.group(0) for m in media._MDLINK.finditer(media._strip_fences(text)))


def schedule(host, tid):
    if tid is None or not host.media.workdir:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    pages = getattr(host, '_media_pages', None)
    if pages is None:
        pages = host._media_pages = {}
    row = pages.get(tid)
    if row is None:
        frozen = copy.copy(host.media)
        cwd = host.media.current()
        frozen.current = lambda: cwd
        row = pages[tid] = {'generation': 0, 'scanner': frozen, 'job': None, 'keys': set()}
    row['generation'] += 1
    if row['job'] is not None and not row['job'].done():
        return
    job = row['job'] = loop.create_task(scan(host, tid, row))
    host.media_jobs.add(job)
    job.add_done_callback(host.media_jobs.discard)
    # Bounded transient bookkeeping; active jobs are never evicted.
    for old in list(pages):
        if len(pages) <= 128:
            break
        if old != tid and pages[old]['job'].done():
            pages.pop(old)


async def scan(host, tid, row):
    while True:
        await asyncio.sleep(.08)
        generation = row['generation']
        page = host.hist.get(tid)
        if page is None:
            return
        ids = next((ids for ids in host.hist.parts.values() if tid in ids), [tid])
        text = '\n\n'.join(x['reply']['text'] for x in (host.hist.get(i) for i in ids) if x)
        text = re.sub(r':(?:codex-(?:file-)?citation|file-citation)\{[^}]*\}', '', text)
        if page.get('end') == 'open':
            text = explicit(text)
        try:
            items, skips, keys = await asyncio.to_thread(row['scanner'].scan_keys, text, ids[0])
        except Exception:
            host.st.log('media_scan_fail', turn=tid)
            items, keys = [], []
            skips = [{'name': privacy.owner_text(r.path.rsplit('/', 1)[-1])[:media.NAME_MAX], 'why': 'failed'} for r in media.refs(text) if r.explicit]
        if host.hist.get(tid) is not page:
            return
        if generation != row['generation']:
            continue
        if page.get('end') != 'open' and any(x.get('why') == 'too_big' for x in skips):
            # Only server-resolved explicit references become download offers; phone never sends a path.
            for ref in media.refs(text):
                if not ref.explicit:
                    continue
                name = ref.path.rsplit('/', 1)[-1]
                skip = next((x for x in skips if x.get('why') == 'too_big' and x.get('name') == name), None)
                if skip is None:
                    continue
                try:
                    card = await asyncio.to_thread(host.private_files.offer, row['scanner'], ref, turn=tid)
                    if host.hist.get(tid) is not page:
                        return
                    if card:
                        skip['download'] = card
                except (OSError, ValueError, media.Gone):
                    pass  # keep the honest existing too_big note
        if keys:
            from . import owner_route
            fresh = [pair for pair in keys if pair[1].get('sha256') not in row['keys']]
            row['keys'].update(pair[1].get('sha256') for pair in fresh)
            if fresh:
                await owner_route.offer_files(host, fresh, getattr(host, 'lang', 'zh'))
        if generation != row['generation']:
            continue
        if items or skips or page.get('media') or page.get('media_skip'):
            host.hist_update(tid, media=(items, skips))
            host.st.log('media_page', turn=tid, status=f'{len(items)}/{len(skips)}')
        return
