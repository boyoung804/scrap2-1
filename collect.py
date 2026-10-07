"""문화체육관광부 관련 보도 수집기

- GitHub Actions에서 5분마다 실행
- 네이버 뉴스 검색에서 '문화체육관광부' / '문체부'를 각각 검색
- 지정된 16개 매체만 수집
- 검색 결과 1페이지에 지정 매체가 없어도 10페이지까지 계속 확인
- 같은 기사는 중복 제거
- 보고서 날짜: 07:00 ~ 다음날 07:00 구간의 '종료일'을 파일명으로 사용
  예) 2026-10-07 07:00 ~ 2026-10-08 07:00 -> data/2026-10-08.json
"""

import os
import re
import json
import html
import time
import datetime as dt
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

KST = dt.timezone(dt.timedelta(hours=9))
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

MEDIA = [
    "조선일보", "중앙일보", "동아일보", "한국일보", "경향신문", "한겨레",
    "매일경제", "한국경제", "서울경제", "파이낸셜뉴스", "이데일리", "세계일보",
    "국민일보", "서울신문", "전자신문", "머니투데이",
]

QUERIES = ["문화체육관광부", "문체부"]
NEWS_URL = "https://search.naver.com/search.naver"

REL_RE = re.compile(r"(\d+)\s*(초|분|시간|일|주)\s*전")
ABS_RE = re.compile(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
PAGE_RE = re.compile(r"([A-Za-z가-힣]?\s*\d{1,3}\s*면)")


def report_window():
    now = dt.datetime.now(KST)

    # 기존 화면의 날짜 체계에 맞춤.
    # 07:00 이후 현재 수집분은 '다음날' 날짜 파일에 저장.
    # 예: 10/7 07:00~10/8 07:00 -> data/2026-10-08.json
    if os.environ.get("REPORT_DATE"):
        d = dt.date.fromisoformat(os.environ["REPORT_DATE"])
        start = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(7), KST)
        end = dt.datetime.combine(d, dt.time(7), KST)
    elif now.hour >= 7:
        d = now.date() + dt.timedelta(days=1)
        start = dt.datetime.combine(now.date(), dt.time(7), KST)
        end = dt.datetime.combine(d, dt.time(7), KST)
    else:
        d = now.date()
        start = dt.datetime.combine(now.date() - dt.timedelta(days=1), dt.time(7), KST)
        end = dt.datetime.combine(now.date(), dt.time(7), KST)

    return d, start, end, now


def clean_text(value):
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def find_media(text):
    text = clean_text(text)
    for media in sorted(MEDIA, key=len, reverse=True):
        if media in text:
            return media
    return ""


def art_id(link, original="", title=""):
    link = link or ""
    m = re.search(r"/article/(\d+)/(\d+)", link)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.search(r"[?&]oid=(\d+).*?[?&]aid=(\d+)", link)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    if original:
        return original.split("#", 1)[0].strip()
    return f"{clean_text(title)}|{link}"


def parse_datetime(text, now):
    text = clean_text(text)

    m = REL_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        if unit == "초":
            return now - dt.timedelta(seconds=n)
        if unit == "분":
            return now - dt.timedelta(minutes=n)
        if unit == "시간":
            return now - dt.timedelta(hours=n)
        if unit == "일":
            return now - dt.timedelta(days=n)
        if unit == "주":
            return now - dt.timedelta(weeks=n)

    m = ABS_RE.search(text)
    if m:
        y, mo, day = map(int, m.groups())
        return dt.datetime(y, mo, day, 12, tzinfo=KST)

    return None


def make_item(title, original_url, naver_url, block_text, description, now, query):
    title = clean_text(title)
    original_url = clean_text(original_url)
    naver_url = clean_text(naver_url)
    block_text = clean_text(block_text)
    description = clean_text(description)

    if not title:
        return None

    # 실제 검색어가 제목/요약/기사 블록에 있는지 한 번 더 확인.
    if query not in f"{title} {description} {block_text}":
        return None

    media = find_media(block_text)
    if not media:
        return None

    pub_dt = parse_datetime(block_text, now)
    page_match = PAGE_RE.search(block_text)
    aid = art_id(naver_url or original_url, original_url, title)

    return {
        "id": aid,
        "title": title,
        "media": media,
        "url": original_url or naver_url,
        "link": naver_url or original_url,
        "pub_dt": pub_dt,
        "section": "지면" if page_match else "온라인",
        "page": clean_text(page_match.group(1)) if page_match else "",
        "keyword": query,
        "description": description,
    }


def parse_result_block(block, now, query):
    text = clean_text(block.get_text(" ", strip=True))
    if not text or query not in text:
        return None

    # 제목: 현재 네이버 구조를 우선하고, 구형 구조도 지원.
    title_a = block.select_one("a.news_tit")
    if title_a is None:
        candidates = block.select("a[href]")
        candidates = [a for a in candidates if len(clean_text(a.get_text())) >= 8]
        if candidates:
            title_a = candidates[0]
    if title_a is None:
        return None

    title = clean_text(title_a.get_text(" ", strip=True))
    original_url = title_a.get("href", "")

    # 네이버 기사 원문 링크가 별도로 있는 경우 우선 사용.
    naver_url = ""
    for a in block.select('a[href*="news.naver.com"], a[href*="n.news.naver.com"]'):
        href = a.get("href", "")
        if href:
            naver_url = href
            break

    # 언론사/요약 영역.
    desc_node = block.select_one(
        "div.dsc_wrap, div.api_txt_lines, p.dsc, div.news_dsc, .dsc"
    )
    description = clean_text(desc_node.get_text(" ", strip=True)) if desc_node else ""

    return make_item(
        title, original_url, naver_url, text, description, now, query
    )


def parse(page_html, now, query):
    soup = BeautifulSoup(page_html, "html.parser")
    result = {}

    # 현재/최근 네이버 뉴스 결과 구조.
    blocks = soup.select(
        "div.news_area, div.news_wrap.api_ani_send, li.bx, div.api_ani_send"
    )

    seen_blocks = set()
    for block in blocks:
        marker = id(block)
        if marker in seen_blocks:
            continue
        seen_blocks.add(marker)
        item = parse_result_block(block, now, query)
        if item:
            result[item["id"]] = item

    # 구조가 달라졌을 때 제목 링크 기준으로 다시 시도.
    anchors = soup.select("a.news_tit")
    if not anchors:
        anchors = soup.select('a[href*="news.naver.com"], a[href*="n.news.naver.com"]')

    for a in anchors:
        node = a
        for _ in range(10):
            node = node.parent
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) > 2500:
                continue
            if find_media(text) and (REL_RE.search(text) or ABS_RE.search(text)):
                item = parse_result_block(node, now, query)
                if item:
                    result[item["id"]] = item
                break

    return result


def scrape(query, start, end, now, pages=10):
    out = {}
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA,
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.naver.com/",
    })

    for page_no in range(1, pages + 1):
        start_num = (page_no - 1) * 10 + 1
        params = {
            "where": "news",
            "sort": "1",
            "query": query,
            "start": str(start_num),
        }

        try:
            r = session.get(NEWS_URL, params=params, timeout=25)
        except Exception as e:
            print(f"[{query}] {page_no}쪽 요청 실패: {e}")
            continue

        print(
            f"[{query}] {page_no}쪽 응답 {r.status_code}, "
            f"HTML {len(r.text)}자"
        )

        if r.status_code != 200:
            continue

        items = parse(r.text, now, query)
        print(f"[{query}] {page_no}쪽: 지정 16개 매체 {len(items)}건")

        # 1페이지에 0건이어도 절대 중단하지 않고 다음 페이지로 이동.
        for aid, item in items.items():
            out[aid] = item

        time.sleep(0.5)

    return out


def load_data(path, d):
    if not os.path.exists(path):
        return {"date": str(d), "items": []}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"date": str(d), "items": []}
    except Exception as e:
        print("기존 JSON 읽기 실패:", e)
        return {"date": str(d), "items": []}


def main():
    d, start, end, now = report_window()
    path = f"data/{d}.json"
    data = load_data(path, d)

    by = {x.get("id"): x for x in data.get("items", []) if x.get("id")}
    found = {}

    print(f"보고서 날짜: {d}")
    print(f"수집 구간: {start.isoformat()} ~ {end.isoformat()}")

    for query in QUERIES:
        print(f"\n[{query}] 최신순 10페이지 수집 시작")
        items = scrape(query, start, end, now, pages=10)
        for aid, item in items.items():
            if aid in found:
                old_kw = found[aid].get("keyword", "")
                kws = [x for x in old_kw.split(",") if x]
                if query not in kws:
                    found[aid]["keyword"] = ",".join(kws + [query])
            else:
                found[aid] = item

    # 검색 결과 날짜가 약간 늦게/빠르게 표시되는 경우를 위한 2시간 허용.
    tolerance = dt.timedelta(hours=2)

    for aid, item in found.items():
        pub = item.pop("pub_dt", None)
        if pub is None:
            # 날짜를 읽지 못한 기사는 최신 검색 결과이므로 현재 시각으로 처리.
            pub = now

        if not (start - tolerance <= pub < end + tolerance):
            continue

        item["pub"] = pub.isoformat(timespec="minutes")

        old = by.get(aid)
        if old is None:
            by[aid] = item
        else:
            for key in ("title", "media", "url", "link", "description", "keyword"):
                if item.get(key):
                    old[key] = item[key]
            if item.get("section") == "지면":
                old["section"] = "지면"
                if item.get("page"):
                    old["page"] = item["page"]

    items = sorted(by.values(), key=lambda x: x.get("pub", ""))

    data.update({
        "date": str(d),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "updated": now.strftime("%m-%d %H:%M"),
        "items": items,
    })

    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(
        f"\n{path} 저장 완료: 총 {len(items)}건 / "
        f"지면 {sum(1 for x in items if x.get('section') == '지면')}건 / "
        f"온라인 {sum(1 for x in items if x.get('section') != '지면')}건"
    )


if __name__ == "__main__":
    main()
