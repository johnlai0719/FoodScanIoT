#!/usr/bin/env python3
# 為「無內容但有候選URL」的 record 直接抓 inchem/EFSA/WHO/FAO 一手頁,
# 填回 inputs 的 pages,並存檔原文(含抓取日期)以利學術重現。
# 純 HTTP,不用 Tavily、不用 Playwright。可重跑(已有 pages 的略過)。
import json, glob, os, re, time, subprocess, urllib.request, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
INPUTS = os.path.join(HERE, 'inputs')
ARCHIVE = os.path.join(HERE, 'source_archive')
LOG = os.path.join(HERE, 'outputs', 'fetch.log')
os.makedirs(ARCHIVE, exist_ok=True)

# 只抓權威食品來源:其他權威網域,或 inchem 的 /jecfa/ 子目錄。
# inchem 的 ICSC(吸入)/PIM(中毒)等非 /jecfa/ 文件不抓。
OTHER_AUTH = ['efsa.europa.eu', 'who.int', 'fao.org', 'codexalimentarius']
MAX_URLS_PER_RECORD = 4          # 每筆最多抓幾個候選,控制負載

def allowed(url):
    u = (url or '').lower()
    if 'inchem.org' in u:
        return '/jecfa/' in u            # inchem 只抓 JECFA 子目錄
    if 'web.archive.org' in u:           # 存檔頁:認被存的原始網址
        return '/jecfa/' in u or any(a in u for a in OTHER_AUTH)
    return any(a in u for a in OTHER_AUTH)
UA = {'User-Agent': 'FoodScanIoT-research/1.0 (academic; ADI source archiving)'}

def log(m):
    line = f'{datetime.datetime.now():%H:%M:%S} {m}'
    with open(LOG, 'a') as f: f.write(line + '\n')
    print(line, flush=True)

def _raw(url):
    delay = 2.0
    for attempt in range(5):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 503) and attempt < 4:
                wait = float(e.headers.get('Retry-After') or delay)
                time.sleep(min(wait, 30)); delay *= 2; continue
            raise
    raise RuntimeError('retry exhausted')

def html_to_text(b):
    t = b.decode('latin-1', 'ignore')
    t = re.sub(r'(?is)<script.*?</script>|<style.*?</style>', ' ', t)
    t = re.sub(r'(?s)<[^>]+>', ' ', t)
    t = re.sub(r'&nbsp;', ' ', t); t = re.sub(r'&amp;', '&', t)
    return re.sub(r'[ \t]+', ' ', t)

def pdf_to_text(b):
    p = subprocess.run(['pdftotext', '-', '-'], input=b,
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.decode('utf-8', 'ignore')

def fetch_text(url):
    b = _raw(url)
    if b[:5] == b'%PDF-' or url.lower().endswith('.pdf'):
        return pdf_to_text(b)
    return html_to_text(b)

def main():
    files = sorted(glob.glob(os.path.join(INPUTS, '*.json')))
    todo = []
    for fn in files:
        d = json.load(open(fn))
        if not d.get('pages') and d.get('candidate_source_urls'):
            todo.append((fn, d))
    log(f'[start] 需抓取 {len(todo)} 筆')
    ok = empty = 0
    for i, (fn, d) in enumerate(todo, 1):
        rid = d['record_id']
        urls = [u for u in d['candidate_source_urls']
                if allowed(u)][:MAX_URLS_PER_RECORD]
        pages = []
        for j, u in enumerate(urls):
            try:
                txt = fetch_text(u).strip()
                if len(txt) > 200:                      # 太短視為無效
                    pages.append({'url': u, 'content': txt})
                    open(os.path.join(ARCHIVE, f'{rid}__{j}.txt'), 'w').write(
                        f'# fetched: {datetime.date.today()}\n# url: {u}\n\n{txt}')
            except Exception as e:
                log(f'  [err] {rid} {u}: {e}')
            time.sleep(1.0)
        if pages:
            d['pages'] = pages
            json.dump(d, open(fn, 'w'), ensure_ascii=False, indent=1)
            ok += 1
        else:
            empty += 1
        if i % 20 == 0:
            log(f'  ...{i}/{len(todo)} (ok={ok}, empty={empty})')
    log(f'[done] 成功補內容={ok}, 仍無內容={empty}, archive={ARCHIVE}')

if __name__ == '__main__':
    main()
