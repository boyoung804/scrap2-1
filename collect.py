"""문화체육관광부 관련 보도 수집기 (GitHub Actions용)

- 5분마다 실행되는 GitHub Actions에서 동작합니다.
- 네이버 뉴스 검색에서 '문화체육관광부'와 '문체부'를 각각 최신순으로 검색합니다.
- 지정된 16개 매체만 남깁니다.
- 제목 또는 검색 요약문에 실제 키워드가 포함된 기사만 저장합니다.
- 동일 기사는 중복 제거합니다.
- 보고서 날짜는 기존 코드와 동일하게 07:00 ~ 다음날 07:00 기준입니다.
- 지면번호가 검색 결과에서 확인되면 지면으로 표시합니다.

※ 네이버 뉴스 검색 화면은 API 키 없이 접근할 수 있지만 HTML 구조가 바뀔 수 있으므로
   현재 뉴스 결과의 news_area / news_tit / info 구조를 우선 사용하고,
   구형 구조도 일부 보조적으로 처리합니다.
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

# 사용자가 지정한 16개 매체. 이 목록은 임의로 늘리지 않습니다.
MEDIA = [
    "조선일보",
    "중앙일보",
    "동아일보",
    "한국일보",
    "경향신문",
    "한겨레",
    "매일경제",
    "한국경제",
    "서울경제",
    "파이낸셜뉴스",
    "이데일리",
    "세계일보",
    "국민일보",
    "서울신문",
    "전자신문",
    "머니투데이",
]

# 두 키워드를 별도 검색해서 누락 가능성을 줄입니다.
QUERIES = ["문화체육관광부", "문체부"]

NEWS_URL = "https://search.naver.com/search.naver"

# 상대 날짜 / 절대 날짜
REL_RE = re.compile(r"(\d+)\s*(초|분|시간|일|주)\s*전")
ABS_RE = re.compile(r"(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")
PAGE_RE = re.compile(r"([A-Za-z가-힣]?\s*\d{1,3}\s*면)")


def report_window():
    now = dt.datetime.now(KST)

    if os.environ.get("REPORT_DATE"):
        d = dt.date.fromisoformat(os.environ["REPORT_DATE"])
    else:
        # 07시 이전이면 전날 보고서, 07시 이후면 당일 보고서
        d = now.date() if now.hour < 7 else now.date() + dt.timedelta(days=1)

    start = dt.datetime.combine(d - dt.timedelta(days=1), dt.time(7), KST)
    end = dt.datetime.combine(d, dt.time(7), KST)
    return d, start, end, now


def clean_text(value):
    if value is None:
        return ""
    value = html.unescape(str(value))
    value = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", value).strip()


def normalize_media(text):
    """네이버의 언론사 표기에서 지정된 16개 매체를 정확하게 찾습니다."""
    text = clean_text(text)
    if not text:
        return ""

    # 긴 이름부터 검사하여 부분 일치 충돌을 줄입니다.
    for media in sorted(MEDIA, key=len, reverse=True):
        if media == text or media in text:
            return media
    return ""


def art_id(link, original="", title=""):
    link = link or ""

    # 네이버 뉴스 URL
    m = re.search(r"/article/(\d+)/(\d+)", link)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    m = re.search(r"[?&]oid=(\d+).*?[?&]aid=(\d+)", link)
    if m:
        return f"{m.group(1)}-{m.group(2)}"

    # 원문 URL을 이용한 안정적인 중복 키
    if original:
        return original.split("#", 1)[0].strip()

    return f"{clean_text(title)}|{link}"


def when(text, now):
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
        # 날짜만 노출되는 경우 정오로 두고 날짜 필터에는 허용 오차를 둡니다.
        return dt.datetime(y, mo, day, 12, tzinfo=KST)

    return None


def has_keyword(title, description, query):
    """검색어가 제목 또는 검색 요약문에 실제로 존재하는지 확인."""
    text = f"{clean_text(title)} {clean_text(description)}"
    return query in text


def parse_new_style(soup, now, query):
    """현재 네이버 뉴스 검색 결과의 일반적인 news_area 구조 파싱."""
    result = {}

    blocks = soup.select("div.news_area")

    for blk in blocks:
        title_a = blk.select_one("a.news_tit")
        if not title_a:
            continue

        title = clean_text(title_a.get_text(" ", strip=True))
        href = title_a.get("href", "")
        if not title or not href:
            continue

        desc_node = blk.select_one("div.dsc_wrap")
        description = clean_text(desc_node.get_text(" ", strip=True)) if desc_node else ""

        # 언론사 정보는 info 영역 전체에서 찾습니다.
        info_text = " ".join(
            clean_text(x.get_text(" ", strip=True))
            for x in blk.select("span.info, div.info_group, a.info")
        )
        media = normalize_media(info_text)
        if not media:
            # 블록 전체에서도 한 번 더 찾습니다.
            media = normalize_media(blk.get_text(" ", strip=True))
        if not media:
            continue

        if not has_keyword(title, description, query):
            continue

        all_text = clean_text(blk.get_text(" ", strip=True))
        pub_dt = when(all_text, now)
        page_match = PAGE_RE.search(all_text)

        # 원문 링크가 news.naver.com이 아니면 원문 링크를 그대로 사용합니다.
        link = href
        naver_link = ""
        for a in blk.select("a"):
            h = a.get("href", "")
            if "news.naver.com" in h:
                naver_link = h
                break

        aid = art_id(naver_link or href, href, title)
        result[aid] = {
            "id": aid,
            "title": title,
            "media": media,
            "url": link,
            "link": naver_link or link,
            "pub_dt": pub_dt,
            "section": "지면" if page_match else "온라인",
            "page": clean_text(page_match.group(1)) if page_match else "",
            "keyword": query,
            "description": description,
        }

    return result


def parse_old_style(soup, now, query):
    """네이버가 구조를 바꿨을 때를 위한 보조 파서."""
    result = {}

    for a in soup.select('a[href*="news.naver.com"], a[href*="n.news.naver.com"]'):
        title = clean_text(a.get_text(" ", strip=True))
        href = a.get("href", "")
        if len(title) < 6 or not href:
            continue

        node = a
        blk = None
        for _ in range(10):
            node = node.parent
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) > 2500:
                break
            if REL_RE.search(text) or ABS_RE.search(text):
                blk = node
                break

        if blk is None:
            continue

        text = clean_text(blk.get_text(" ", strip=True))
        if not has_keyword(title, text, query):
            continue

        media = normalize_media(text)
        if not media:
            continue

        pub_dt = when(text, now)
        page_match = PAGE_RE.search(text)
        aid = art_id(href, "", title)

        result[aid] = {
            "id": aid,
            "title": title,
            "media": media,
            "url": href,
            "link": href,
            "pub_dt": pub_dt,
            "section": "지면" if page_match else "온라인",
            "page": clean_text(page_match.group(1)) if page_match else "",
            "keyword": query,
            "description": "",
        }

    return result


def parse(page_html, now, query):
    soup = BeautifulSoup(page_html, "html.parser")

    result = parse_new_style(soup, now, query)
    old = parse_old_style(soup, now, query)

    for aid, item in old.items():
        result.setdefault(aid, item)

    return result


def scrape(query, start, end, now, pages=10):
    """검색어 하나를 최신순으로 여러 페이지 수집.

    중요: 특정 페이지에서 지정 16개 매체가 0건이어도 다음 페이지로 계속 이동합니다.
    네이버 최신순 결과에서는 1페이지에 지정 매체가 하나도 없고 2~3페이지에
    지정 매체가 나오는 경우가 있기 때문입니다.
    """
    out = {}

    for page_no in range(pages):
        start_num = page_no * 10 + 1
        params = {
            "where": "news",
            "sort": "1",  # 최신순
            "query": query,
            "start": str(start_num),
        }

        try:
            r = requests.get(
                NEWS_URL,
                params=params,
                headers={
                    "User-Agent": UA,
                    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": "https://www.naver.com/",
                },
                timeout=25,
            )
        except Exception as e:
            print(f"[{query}] {page_no + 1}쪽 요청 실패: {e}")
            break

        if r.status_code != 200:
            print(f"[{query}] {page_no + 1}쪽 응답 코드: {r.status_code}")
            break

        items = parse(r.text, now, query)
        print(
            f"[{query}] {page_no + 1}쪽: 지정 16개 매체 기사 {len(items)}건 "
            f"(HTML {len(r.text)}자)"
        )

        # 지정 16개 매체가 이 페이지에 0건이어도 절대 여기서 종료하지 않습니다.
        # 다음 페이지에 지정 매체가 있을 수 있습니다.
        out.update(items)

        # 페이지 안에서 날짜를 읽을 수 있는 기사만 가지고 종료 여부를 판단합니다.
        # 단, 현재 페이지의 지정 매체 기사 수가 0이어도 종료하지 않습니다.
        dates = [x["pub_dt"] for x in items.values() if x.get("pub_dt")]
        if dates and min(dates) < start - dt.timedelta(hours=2):
            break

        # 네이버에 과도하게 연속 요청하지 않도록 짧게 대기
        time.sleep(0.7)

    return out


def load_data(path, d):
    if not os.path.exists(path):
        return {"date": str(d), "items": []}

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"date": str(d), "items": []}
        return data
    except Exception as e:
        print("기존 JSON 읽기 실패, 새로 시작:", e)
        return {"date": str(d), "items": []}


def main():
    d, start, end, now = report_window()
    path = f"data/{d}.json"

    data = load_data(path, d)
    by = {x.get("id"): x for x in data.get("items", []) if x.get("id")}

    # 두 검색어를 각각 검색한 뒤 합칩니다.
    # 검색 결과 1페이지에 지정 매체가 없어도 2~10페이지까지 계속 확인합니다.
    # 10페이지 × 10건 = 검색어당 최대 100개 결과를 훑습니다.
    MAX_PAGES = 10
    found = {}
    for query in QUERIES:
        print(f"[{query}] 최신순 최대 {MAX_PAGES}페이지(최대 100개 검색 결과) 확인 시작")
        for aid, item in scrape(query, start, end, now, pages=MAX_PAGES).items():
            # 두 검색어에서 동시에 잡힌 기사는 첫 번째 정보를 유지하되
            # keyword는 두 키워드 모두 확인된 것으로 표시합니다.
            if aid in found:
                old_kw = found[aid].get("keyword", "")
                if query not in old_kw.split(","):
                    found[aid]["keyword"] = f"{old_kw},{query}"
            else:
                found[aid] = item

    tol = dt.timedelta(hours=2)

    for aid, item in found.items():
        pub = item.pop("pub_dt", None) or now

        if not (start - tol <= pub < end + tol):
            continue

        item["pub"] = pub.isoformat(timespec="minutes")

        old = by.get(aid)
        if old is None:
            by[aid] = item
        else:
            # 기존 기사의 최신 정보 보강
            for key in ("title", "media", "url", "link", "description", "keyword"):
                if item.get(key):
                    old[key] = item[key]

            if item.get("section") == "지면":
                old["section"] = "지면"
                if item.get("page"):
                    old["page"] = item["page"]

    # 오래된 예전 코드에서 남은 불필요한 pub_dt가 있으면 제거
    for item in by.values():
        item.pop("pub_dt", None)

    items = sorted(
        by.values(),
        key=lambda x: x.get("pub", ""),
        reverse=False,
    )

    data.update(
        {
            "date": str(d),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "updated": now.strftime("%m-%d %H:%M"),
            "items": items,
        }
    )

    os.makedirs("data", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    print(
        f"{path} 저장 완료: 총 {len(items)}건 / "
        f"지면 {sum(1 for x in items if x.get('section') == '지면')}건 / "
        f"온라인 {sum(1 for x in items if x.get('section') != '지면')}건"
    )


if __name__ == "__main__":
    main()
