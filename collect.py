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
OID = {'조선일보': '023', '중앙일보': '025', '동아일보': '020', '한국일보': '469', '경향신문': '032', '한겨레': '028',
       '매일경제': '009', '한국경제': '015', '서울경제': '011', '파이낸셜뉴스': '014', '이데일리': '018',
       '세계일보': '022', '국민일보': '005', '서울신문': '081', '전자신문': '030', '머니투데이': '008'}
MAX_PAGES = int(os.environ.get('MAX_PAGES') or 60)
QUERY = (os.environ.get('SEARCH_QUERY') or '문화체육관광부|문체부').strip()  # GitHub 저장소 변수 SEARCH_QUERY 로 바꿀 수 있음
BASE = 'https://search.naver.com/search.naver?where=news&sort=1&query=' + quote(QUERY)

def with_range(url, start, end):
    """네이버에 날짜 범위를 직접 지정(보고서 범위가 걸친 이틀). 전체 결과를 훑지 않아도 됨."""
    a, b = start.date(), end.date()
    return (url + f'&ds={a:%Y.%m.%d}&de={b:%Y.%m.%d}&pd=3&nso=so%3Add%2Cp%3Afrom{a:%Y%m%d}to{b:%Y%m%d}')

def with_press(url):
    """지정 16개 매체만 보이게 하는 언론사 필터(네이버 '언론사 선택'과 같은 값)."""
    return url + '&mynews=1&office_type=1&office_section_code=1&news_office_checked=' + ','.join('1' + v for v in OID.values())
REL = re.compile(r'(\d+)\s*(분|시간|일|주)\s*전')
ABS = re.compile(r'(\d{4})\.(\d{1,2})\.(\d{1,2})\.')
PAGE = re.compile(r'([A-Za-z]?\d{1,2}면)')

def report_window(d=None):
    now = dt.datetime.now(KST)
    d = d or (dt.date.fromisoformat(os.environ['REPORT_DATE']) if os.environ.get('REPORT_DATE') else
              (now.date() if now.hour < 7 else now.date() + dt.timedelta(days=1)))
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

BLIND = '새 창 열림'

def clean(t):
    return re.sub(r'\s+', ' ', t or '').strip()

def txt(el, sep=' '):
    return clean((el.get_text(sep) if el is not None else '').replace(BLIND, ''))

def parse(page_html, now):
    """네이버 뉴스 검색 결과(2026-10 화면 구조) 읽기.
    제목 링크(data-heatmap-target=".tit")마다, 그 기사 하나만 담긴 가장 큰 덩어리에서
    매체(profile-info-title-text), 시간·면수(profile-info-subtext), 네이버뉴스 링크(.nav)를 읽는다."""
    soup, res = BeautifulSoup(page_html, 'html.parser'), {}
    ts = soup.select('a[data-heatmap-target=".tit"], a.news_tit')
    tset = {id(x) for x in ts}
    for t in ts:
        node, blk = t, None
        while node.parent is not None:
            node = node.parent
            if sum(1 for x in node.find_all('a') if id(x) in tset) > 1 or len(node.get_text(' ', strip=True)) > 3000:
                break
            blk = node
        if blk is None:
            continue
        title = txt(t.select_one('[class*="headline"]'), '') or txt(t, '')
        if len(title) < 3:
            continue
        mname = txt(blk.select_one('[class*="profile-info-title-text"]'))
        metas = [txt(x) for x in blk.select('[class*="profile-info-subtext"]')]
        meta = ' '.join(metas)
        media = next((m for m in MEDIA if mname == m or mname.startswith(m + ' ')), '')
        nav = blk.select_one('a[data-heatmap-target=".nav"]')
        link = nav.get('href', '') if nav else ''
        href = t.get('href', '')
        i = art_id(link) or art_id(href) or href
        if not i or i in res:
            continue
        pg = PAGE.search(meta) if media else None
        res[i] = {'head': [mname] + metas, 'id': i, 'title': html.unescape(title), 'media': media,
                  'url': href or link, 'link': link, 'pub_dt': when(meta, now),
                  'section': '지면' if pg else '온라인', 'page': pg.group(1) if pg else ''}
    return res

def scrape(url, start, now, pages=MAX_PAGES, label='', check_media=False):
    """검색 결과를 쪽별로 읽는다. 지정 매체가 없는 쪽이 있어도 계속 넘어가고,
    ① 결과가 끝났거나 ② 다음 쪽이 앞 쪽과 같은 내용이거나 ③ 두 쪽 연속으로 모두 보고서 범위보다 오래되면 멈춘다."""
    out, seen_ids, old_streak, total_raw = {}, set(), 0, 0
    for k in range(pages):
        u = url + ('&' if '?' in url else '?') + f'start={k * 10 + 1}'
        try:
            r = requests.get(u, headers={'User-Agent': UA, 'Accept-Language': 'ko-KR,ko;q=0.9'}, timeout=20)
        except Exception as e:
            print(label, '요청 실패', e); break
        if r.status_code != 200:
            print(label, '응답 코드', r.status_code); break
        raw = parse(r.text, now)
        items = {i: x for i, x in raw.items() if x['media']}
        if k == 0 and (os.environ.get('FORCE_PRINT') or not raw):
            os.makedirs('data', exist_ok=True)
            open('data/debug_naver.html', 'w', encoding='utf-8').write(re.sub(r'<(script|style)[\s\S]*?</\1>', '', r.text)[:200000])
        if not raw:
            sp = BeautifulSoup(r.text, 'html.parser')
            print(f'  [진단] 페이지 제목: {sp.title.get_text(strip=True) if sp.title else "(없음)"}')
            print(f'{label} {k + 1}쪽: 기사 없음 → 여기서 끝')
            break
        new = [i for i in raw if i not in seen_ids]
        print(f'{label} {k + 1}쪽: 읽은 기사 {len(raw)}건(새 {len(new)}건) 중 지정 매체 {len(items)}건 · 첫 기사: {next(iter(raw.values()))["title"][:18]}')
        if k == 0:
            for x in list(raw.values())[:3]:
                print('  [샘플]', x['title'][:20], '| 매체:', x['media'] or '(지정 외)', '| 시간:', x['pub_dt'].strftime('%m-%d %H:%M') if x['pub_dt'] else '(못 읽음)')
            if check_media:  # 언론사 필터가 실제로 먹혔는지 확인
                names = [x['head'][0] for x in raw.values() if x['head'] and x['head'][0]]
                ok = sum(1 for x in raw.values() if x['media'])
                if not raw or ok < len(raw) * 0.8:
                    print(f'  [필터 확인] 지정 외 매체가 섞여 있어(지정 {ok}/{len(raw)}) 매체 필터는 쓰지 않습니다:', names[:6])
                    return None
                print(f'  [필터 확인] 매체 필터 정상 작동 ({ok}/{len(raw)})')
        if not new:
            print(f'{label} 다음 쪽이 앞 쪽과 같은 내용 → 여기서 끝(네이버가 더 넘겨주지 않음)')
            break
        seen_ids.update(new); total_raw += len(new)
        for i in new:
            x = raw[i]
            if x['media']:
                x.pop('head', None); out[i] = x
        known = [raw[i]['pub_dt'] for i in new if raw[i]['pub_dt']]
        if known and max(known) < start - dt.timedelta(hours=2):
            old_streak += 1
            if old_streak >= 2:
                print(f'{label} 보고서 범위보다 오래된 기사 구간 → 여기서 끝'); break
        else:
            old_streak = 0
        time.sleep(1.5)
    else:
        print(f'{label} ⚠ {pages}쪽까지만 읽었습니다(MAX_PAGES 로 늘릴 수 있음)')
    print(f'{label} 합계: 읽은 기사 {total_raw}건 중 지정 매체 {len(out)}건')
    return out

def main(d0=None):
    d, start, end, now = report_window(d0)
    path = f'data/{d}.json'
    data = json.load(open(path, encoding='utf-8')) if os.path.exists(path) else {'date': str(d), 'items': []}
    by = {x['id']: x for x in data['items'] if len(x['title']) >= 3 and BLIND not in x['title'] and '언론사 선정' not in x['title']}  # 잘못 읽힌 옛 항목 정리
    rng = with_range(BASE, start, end)
    found = {}
    f = scrape(with_press(rng), start, now, label='[매체필터]', check_media=True)  # 16개 매체만, 날짜 범위 안
    if f is not None:
        found.update(f)
    g = scrape(rng, start, now, label='[전체]') or {}  # 필터 없이도 읽어 빠진 것이 없게 보강
    for i, x in g.items():
        found.setdefault(i, x)
    # 네이버 '유형: 지면기사' 필터(photo=3)로 지면에 실린 기사를 따로 읽는다(매 회차, 결과가 적어 가볍다)
    pu = os.environ.get('PRINT_SEARCH_URL', '').strip()
    pr = None
    if pu:
        pr = scrape(pu, start, now, label='[지면-지정주소]')
    else:
        pr = scrape(with_press(rng) + '&photo=3', start, now, label='[지면기사]', check_media=True)
        if pr is None:
            pr = scrape(rng + '&photo=3', start, now, label='[지면기사-매체필터없이]')
    for i, x in (pr or {}).items():
        x['section'] = '지면'
        if i in found and not x['page']:
            x['page'] = found[i]['page']
        found[i] = x
    data['printed_at'] = now.isoformat(timespec='seconds')
    tol = dt.timedelta(hours=1)
    drop = {'시간 범위 밖': 0}
    late = []
    for i, x in found.items():
        pub = x.pop('pub_dt') or now
        if not (start - tol <= pub < end + tol):
            drop['시간 범위 밖'] += 1
            late.append(f"{x['media']} {pub:%m-%d %H:%M} {x['title'][:30]}")
            continue
        x['pub'] = pub.isoformat(timespec='minutes')
        old = by.get(i)
        if old is None:
            by[i] = x
        elif x['section'] == '지면':
            old['section'], old['page'] = '지면', x['page'] or old.get('page', '')
    for t in late[:20]:
        print('  [범위 밖으로 뺀 기사]', t)
    print(f'범위 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} / 읽은 지정 매체 기사 {len(found)}건, {drop}')
    data.update({'from': start.isoformat(), 'to': end.isoformat(), 'updated': now.strftime('%m-%d %H:%M'),
                 'items': sorted(by.values(), key=lambda x: x['pub'])})
    os.makedirs('data', exist_ok=True)
    json.dump(data, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(path, f'총 {len(by)}건 (지면 {sum(1 for x in by.values() if x["section"] == "지면")}건)')

if __name__ == '__main__':
    main()
