"""문체부 관련 보도 수집기 (GitHub Actions용, 네이버 API 키 없이 검색 화면을 읽는 방식)
- 네이버 뉴스 검색(최신순)을 읽어 지정 16개 매체 기사만 모읍니다(5분마다).
- 화면에 지면 표시(예: A24면)가 있으면 '지면', 없으면 '온라인'.
- 지면기사 필터 화면(PRINT_SEARCH_URL, 선택)도 30분마다 읽어 지면을 보강합니다.
- 보고서 날짜 D = (D-1일 07:00 ~ D일 07:00 KST). data/D.json 에 누적."""
import os, re, json, html, time, datetime as dt
from urllib.parse import quote
import requests
from bs4 import BeautifulSoup

KST = dt.timezone(dt.timedelta(hours=9))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
MEDIA = ['조선일보', '중앙일보', '동아일보', '한국일보', '경향신문', '한겨레', '매일경제', '한국경제', '서울경제',
         '파이낸셜뉴스', '이데일리', '세계일보', '국민일보', '서울신문', '전자신문', '머니투데이']
QUERY = '문화체육관광부|문체부'
BASE = 'https://search.naver.com/search.naver?where=news&sort=1&query=' + quote(QUERY)
REL = re.compile(r'(\d+)\s*(분|시간|일|주)\s*전')
ABS = re.compile(r'(\d{4})\.(\d{1,2})\.(\d{1,2})\.')
PAGE = re.compile(r'([A-Za-z]?\d{1,2}면)')

def report_window():
    now = dt.datetime.now(KST)
    d = dt.date.fromisoformat(os.environ['REPORT_DATE']) if os.environ.get('REPORT_DATE') else \
        (now.date() if now.hour < 7 else now.date() + dt.timedelta(days=1))
    start = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(7), KST)
    return d, start, dt.datetime.combine(d, dt.time(7), KST), now

def art_id(link, orig=''):
    m = re.search(r'/article/(\d+)/(\d+)|oid=(\d+)&aid=(\d+)', link or '')
    if m:
        return f'{m.group(1)}-{m.group(2)}' if m.group(1) else f'{m.group(3)}-{m.group(4)}'
    return orig

def when(text, now):
    m = REL.search(text)
    if m:
        n, u = int(m.group(1)), m.group(2)
        return now - dt.timedelta(**{'minutes': n} if u == '분' else {'hours': n} if u == '시간' else {'days': n} if u == '일' else {'weeks': n})
    m = ABS.search(text)
    if m:
        return dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), 12, tzinfo=KST)
    return None

def parse(page_html, now):
    soup, res = BeautifulSoup(page_html, 'html.parser'), {}
    for a in soup.select('a[href*="news.naver.com"]'):
        link = a.get('href', '')
        i = art_id(link)
        if not i or i in res:
            continue
        node, blk = a, None
        for _ in range(12):
            node = node.parent
            if node is None:
                break
            t = node.get_text(' ', strip=True)
            if len(t) > 1000:
                break
            anchors = [x for x in node.find_all('a') if len(x.get_text(strip=True)) >= 6]
            if (REL.search(t) or ABS.search(t)) and anchors:
                blk = node
                break
        if blk is None:
            continue
        strings = list(blk.stripped_strings)
        media = next((m for s in strings for m in MEDIA if s == m or s.startswith(m + ' ')), '')
        if not media:
            continue
        t_a = next(x for x in blk.find_all('a') if len(x.get_text(strip=True)) >= 6)
        text = blk.get_text(' ', strip=True)
        pg = PAGE.search(text)
        href = t_a.get('href', '')
        res[i] = {'id': i, 'title': html.unescape(t_a.get_text(' ', strip=True)), 'media': media,
                  'url': href if href and 'naver.com' not in href else link, 'link': link,
                  'pub_dt': when(text, now), 'section': '지면' if pg else '온라인', 'page': pg.group(1) if pg else ''}
    return res

def scrape(url, start, now, pages=10, label=''):
    out = {}
    for k in range(pages):
        u = url + ('&' if '?' in url else '?') + f'start={k * 10 + 1}'
        try:
            r = requests.get(u, headers={'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9'}, timeout=20)
        except Exception as e:
            print(label, '요청 실패', e); break
        if r.status_code != 200:
            print(label, '응답 코드', r.status_code); break
        items = parse(r.text, now)
        print(f'{label} {k + 1}쪽: 기사 {len(items)}건 (HTML {len(r.text)}자)')
        if not items:
            break
        out.update(items)
        known = [x['pub_dt'] for x in items.values() if x['pub_dt']]
        if known and max(known) < start - dt.timedelta(hours=1):
            break
        time.sleep(2)
    return out

def main():
    d, start, end, now = report_window()
    path = f'data/{d}.json'
    data = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {'date': str(d), 'items': []}
    by = {x['id']: x for x in data['items']}
    found = scrape(BASE, start, now, label='전체')
    last = data.get('printed_at')
    pu = os.environ.get('PRINT_SEARCH_URL', '').strip()
    if pu and (not last or (now - dt.datetime.fromisoformat(last)).total_seconds() >= 1800 or os.environ.get('FORCE_PRINT')):
        for i, x in scrape(pu, start, now, label='지면').items():
            x['section'] = '지면'
            if i in found and not x['page']:
                x['page'] = found[i]['page']
            found[i] = x
        data['printed_at'] = now.isoformat(timespec='seconds')
    tol = dt.timedelta(hours=1)
    for i, x in found.items():
        pub = x.pop('pub_dt') or now
        if not (start - tol <= pub < end + tol):
            continue
        x['pub'] = pub.isoformat(timespec='minutes')
        old = by.get(i)
        if old is None:
            by[i] = x
        elif x['section'] == '지면':
            old['section'], old['page'] = '지면', x['page'] or old.get('page', '')
    data.update({'from': start.isoformat(), 'to': end.isoformat(), 'updated': now.strftime('%m-%d %H:%M'),
                 'items': sorted(by.values(), key=lambda x: x['pub'])})
    os.makedirs('data', exist_ok=True)
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(path, f'총 {len(by)}건 (지면 {sum(1 for x in by.values() if x["section"] == "지면")}건)')

if __name__ == '__main__':
    main()
