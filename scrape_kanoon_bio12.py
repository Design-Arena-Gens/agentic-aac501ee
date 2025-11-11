#!/usr/bin/env python3
import sys
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser

BASE_DOMAINS = ("kanoon.ir", "www.kanoon.ir", "ghalamchi.ir", "www.ghalamchi.ir")
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/119.0 Safari/537.36"
)

SEARCH_QUERIES_ENCODED = [
    # ????? ?????? ???? ???????
    "%D9%86%D9%85%D9%88%D9%86%D9%87%20%D8%B3%D9%88%D8%A7%D9%84%D8%A7%D8%AA%20%D8%B2%DB%8C%D8%B3%D8%AA%20%D8%AF%D9%88%D8%A7%D8%B2%D8%AF%D9%87%D9%85",
    # ????? ???? ???? ???????
    "%D9%86%D9%85%D9%88%D9%86%D9%87%20%D8%B3%D9%88%D8%A7%D9%84%20%D8%B2%DB%8C%D8%B3%D8%AA%20%D8%AF%D9%88%D8%A7%D8%B2%D8%AF%D9%87%D9%85",
    # ????? ?????? ???? ????? ???????
    "%D9%86%D9%85%D9%88%D9%86%D9%87%20%D8%B3%D9%88%D8%A7%D9%84%D8%A7%D8%AA%20%D8%B2%DB%8C%D8%B3%D8%AA%20%D8%B4%D9%86%D8%A7%D8%B3%DB%8C%20%D8%AF%D9%88%D8%A7%D8%B2%D8%AF%D9%87%D9%85",
    # ???? ????? ??????? ????? ????
    "%D8%B2%DB%8C%D8%B3%D8%AA%20%D8%B4%D9%86%D8%A7%D8%B3%DB%8C%20%D8%AF%D9%88%D8%A7%D8%B2%D8%AF%D9%87%D9%85%20%D9%86%D9%85%D9%88%D9%86%D9%87%20%D8%B3%D9%88%D8%A7%D9%84",
]

SEARCH_URL_TEMPLATE = "https://www.kanoon.ir/Search?text={q_encoded}"
DDG_URL_TEMPLATE = "https://duckduckgo.com/html/?q=site%3Akanoon.ir+{q_encoded}"

class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
    def handle_starttag(self, tag, attrs):
        if tag.lower() != 'a':
            return
        href = None
        for k, v in attrs:
            if k.lower() == 'href' and v:
                href = v
                break
        if href:
            self.links.append(href)


def http_get(url, timeout=20, allow_redirects=True):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            final_url = resp.geturl() if allow_redirects else url
            content = resp.read()
            return final_url, resp.status, resp.headers, content
    except Exception as e:
        return url, None, {}, None


def normalize_url(url, base):
    return urllib.parse.urljoin(base, url)


def is_target_domain(url):
    try:
        netloc = urllib.parse.urlparse(url).netloc.split(':')[0]
    except Exception:
        return False
    return any(netloc.endswith(d) for d in BASE_DOMAINS)


def looks_like_download_link(url):
    low = url.lower()
    if low.endswith('.pdf'):
        return True
    # Common file/download patterns on Kanoon/Ghalamchi
    patterns = [
        '/file/', '/files/', '/File/', '/Files/', 'download', 'filedownload', 'attachment'
    ]
    return any(p in low for p in patterns)


def extract_links_from_html(html_bytes):
    text = html_bytes.decode('utf-8', errors='ignore')
    parser = LinkExtractor()
    parser.feed(text)
    return parser.links


def extract_real_url_from_ddg(href: str) -> str:
    # DDG sometimes uses "/l/?kh=-1&uddg=<encoded>"
    try:
        parsed = urllib.parse.urlparse(href)
        if parsed.path.startswith('/l/'):
            qs = urllib.parse.parse_qs(parsed.query)
            uddg_vals = qs.get('uddg')
            if uddg_vals:
                return urllib.parse.unquote(uddg_vals[0])
    except Exception:
        pass
    return href


def fetch_search_results():
    found_pages = set()
    # Try site-native search first
    for qenc in SEARCH_QUERIES_ENCODED:
        url = SEARCH_URL_TEMPLATE.format(q_encoded=qenc)
        final_url, status, headers, content = http_get(url)
        if content:
            for href in extract_links_from_html(content):
                full = normalize_url(href, final_url)
                if is_target_domain(full):
                    found_pages.add(full)
        time.sleep(0.4)

    # Fallback/augment with DuckDuckGo html
    for qenc in SEARCH_QUERIES_ENCODED:
        url = DDG_URL_TEMPLATE.format(q_encoded=qenc)
        final_url, status, headers, content = http_get(url)
        if not content:
            continue
        for href in extract_links_from_html(content):
            full = normalize_url(href, final_url)
            full = extract_real_url_from_ddg(full)
            if is_target_domain(full):
                found_pages.add(full)
        time.sleep(0.5)

    return sorted(found_pages)


def crawl_for_downloads(pages):
    candidate_pages = set(pages)
    download_links = set()

    # First pass: from search pages themselves
    for page in list(candidate_pages):
        final_url, status, headers, content = http_get(page)
        if not content:
            continue
        for href in extract_links_from_html(content):
            full = normalize_url(href, final_url)
            if not is_target_domain(full):
                continue
            if looks_like_download_link(full):
                download_links.add(full)
            else:
                # Could be an article page; crawl as candidate
                if full not in candidate_pages and len(candidate_pages) < 400:
                    candidate_pages.add(full)
        time.sleep(0.4)

    # Second pass: visit candidate article pages to find embedded files
    for page in list(candidate_pages):
        final_url, status, headers, content = http_get(page)
        if not content:
            continue
        for href in extract_links_from_html(content):
            full = normalize_url(href, final_url)
            if not is_target_domain(full):
                continue
            if looks_like_download_link(full):
                download_links.add(full)
        time.sleep(0.25)

    return sorted(download_links)


def verify_links(links):
    verified = []
    for url in links:
        # Try a lightweight check: request headers only by sending Range
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Range": "bytes=0-0",
                "Accept": "*/*",
            },
        )
        ok = False
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                status = resp.status
                ctype = resp.headers.get('Content-Type', '')
                ok = 200 <= status < 400 and (
                    'pdf' in ctype.lower() or 'application' in ctype.lower() or 'octet-stream' in ctype.lower() or url.lower().endswith('.pdf')
                )
        except Exception:
            ok = False
        if ok:
            verified.append(url)
        time.sleep(0.15)
    # Deduplicate while preserving order
    seen = set()
    result = []
    for u in verified:
        if u not in seen:
            seen.add(u)
            result.append(u)
    return result


def main():
    pages = fetch_search_results()
    downloads = crawl_for_downloads(pages)
    verified = verify_links(downloads)
    for u in verified:
        print(u)

if __name__ == '__main__':
    main()
