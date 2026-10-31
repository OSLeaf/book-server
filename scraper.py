import html as htmlmod
import io
import re
import shutil
from pathlib import Path
from xml.sax.saxutils import escape

from bs4 import BeautifulSoup, NavigableString
from PIL import Image
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from pdfbuilder import build_pdf

BASE_DIR = Path(__file__).resolve().parent
AUTH_FILE = BASE_DIR / "auth.json"
BOOKS_DIR = BASE_DIR / "books"
HEADLESS = False  # try True later; Patreon may behave differently headless
POST_LINK = 'a[href*="/posts/"]'

CARDS_JS = """
() => [...document.querySelectorAll('[data-tag="box-collection-title"]')].map(t => {
    const card = t.closest('[class*="collectionCard"]') || t.parentElement;
    const a = t.closest('a') || card.querySelector('a');
    return { title: t.innerText.trim(), href: a ? a.href : null };
})
"""

TITLES_READY_JS = """
() => [...document.querySelectorAll('[data-tag="box-collection-title"]')]
        .some(e => e.innerText.trim().length > 0)
"""

POST_URLS_JS = """
(id) => [...new Set(
    [...document.querySelectorAll('a[href*="/posts/"]')]
        .map(a => a.href)
        .filter(h => h.includes("collection=" + id))
)]
"""


# ---------------------------------------------------------------- helpers
def safe_name(text):
    return re.sub(r'[\\/:*?"<>|]+', "", text).strip() or "untitled"


def new_context(p):
    browser = p.chromium.launch(headless=HEADLESS)
    return browser, browser.new_context(storage_state=str(AUTH_FILE))


def scroll_until_stable(page, count_js, max_rounds=40):
    last, stable = -1, 0
    for _ in range(max_rounds):
        page.mouse.wheel(0, 4000)
        page.wait_for_timeout(1000)
        n = page.evaluate(count_js)
        stable = stable + 1 if n == last else 0
        if stable >= 2:
            break
        last = n


RESERVED_PATHS = {
    "posts",
    "collection",
    "collections",
    "home",
    "login",
    "signup",
    "settings",
    "messages",
    "notifications",
    "explore",
    "search",
    "membership",
    "about",
    "join",
    "c",
    "cw",
    "user",
    "checkout",
    "policy",
    "pricing",
}


def creator_slug(url):
    """Extract the creator name from any patreon.com/<creator>... URL, else None."""
    m = re.search(r"patreon\.com/(?:cw/|c/)?([A-Za-z0-9_.-]+)", url or "")
    if not m or m.group(1).lower() in RESERVED_PATHS:
        return None
    return m.group(1)


def collections_url(slug):
    return f"https://www.patreon.com/cw/{slug}/collections"


# ---------------------------------------------------------------- raw HTML -> blocks
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}


def _clean(el):
    """Plain text of an element (used for titles)."""
    text = el.get_text().replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


INLINE_TAGS = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "u": "u",
    "s": "strike",
    "strike": "strike",
    "del": "strike",
}


def _rich_inner(node):
    out = []
    for child in node.children:
        if type(child) is NavigableString:  # skips comments and similar
            out.append(escape(str(child)).replace("\n", " "))
        elif child.name == "br":
            out.append("\n")
        elif child.name in INLINE_TAGS:
            tag = INLINE_TAGS[child.name]
            out.append(f"<{tag}>{_rich_inner(child)}</{tag}>")
        elif child.name in ("img", "script", "style"):
            continue
        else:  # span, a, ... : keep the text only
            out.append(_rich_inner(child))
    return "".join(out)


def _rich(el):
    """Element text as reportlab markup: escaped, with <b>/<i>/<u> and <br/> kept."""
    text = _rich_inner(el).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" ?\n ?", "\n", text)
    prev = None
    while prev != text:  # drop tags that wrap only whitespace
        prev = text
        text = re.sub(r"<(b|i|u|strike)>([ \n]*)</\1>", r"\2", text)
    return text.strip(" \n").replace("\n", "<br/>")


def _post_blocks(post, base_dir, stats):
    blocks = []
    title_el = post.select_one('[data-tag="post-title"]')  # first match only
    title = _clean(title_el) if title_el else ""
    if title:
        blocks.append(("title", title))

    content = post.select_one(".patreon-post-content")
    if content is None:
        return blocks
    for el in content.find_all(["p", "li", "img", "hr"] + sorted(HEADINGS)):
        name = el.name
        if name == "img":
            src = el.get("src") or ""
            local = (
                None if src.startswith(("http:", "https:", "data:")) else base_dir / src
            )
            if local is not None and local.exists():
                blocks.append(("image", str(local)))
            else:
                stats["missing_images"] += 1
        elif name == "hr":
            blocks.append(("rule", ""))
        elif name == "li":
            if not el.find("p") and _clean(
                el
            ):  # <li><p>..</p></li> is handled by the <p>
                blocks.append(("para", "\u2022 " + _rich(el)))
        elif _clean(el):
            blocks.append(("heading" if name in HEADINGS else "para", _rich(el)))
    return blocks


def extract_blocks(raw_html, base_dir="."):
    """raw.html -> ([(kind, value), ...], stats). kind: title|para|heading|image|rule."""
    base_dir = Path(base_dir)
    soup = BeautifulSoup(raw_html, "html.parser")
    posts = soup.select("section.scraped-post")
    if not posts:  # older raw files: posts separated by <hr>
        posts = [
            BeautifulSoup(part, "html.parser") for part in raw_html.split("<hr>\n")
        ]
    stats = {"posts": len(posts), "missing_images": 0}
    blocks = []
    for post in posts:
        blocks += _post_blocks(post, base_dir, stats)
    return blocks, stats


def write_outputs(folder, title, author, cover_path, raw_html, log=print):
    """Build the PDF next to raw.html. Returns the PDF path."""
    folder = Path(folder)
    cover = cover_path if cover_path and Path(cover_path).exists() else None
    blocks, stats = extract_blocks(raw_html, folder)
    pdf_path = folder / f"{safe_name(title)}.pdf"
    build_pdf(blocks, title, author, cover, pdf_path)
    n_img = sum(1 for kind, _ in blocks if kind == "image")
    log(
        f"Wrote {pdf_path.name}: {stats['posts']} posts, {n_img} images, "
        + ("with cover" if cover else "no cover")
    )
    if stats["missing_images"]:
        log(
            f"  WARNING: {stats['missing_images']} image(s) are not stored locally and were "
            "left out. Scrape this collection again to download them."
        )
    return pdf_path


def rebuild_pdf(folder, title, author="", cover_path=None, log=print):
    """Rebuild the PDF from an existing raw.html without scraping again."""
    raw = (Path(folder) / "raw.html").read_text(encoding="utf-8")
    return write_outputs(folder, title, author, cover_path, raw, log)


# ---------------------------------------------------------------- images
def save_image(data, base_path):
    """Store image bytes as .jpg/.png next to base_path; return the file name."""
    im = Image.open(io.BytesIO(data))
    if im.format == "JPEG":
        target = base_path.with_suffix(".jpg")
        target.write_bytes(data)
    elif im.format == "PNG" and im.mode in ("RGB", "RGBA", "L"):
        target = base_path.with_suffix(".png")
        target.write_bytes(data)
    else:  # webp, gif, palette PNG, ... -> plain PNG
        target = base_path.with_suffix(".png")
        im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB").save(
            target
        )
    return target.name


def localize_images(context, post_html, images_dir, prefix, log):
    """Download the images inside the post body and point their src to the local copy."""
    soup = BeautifulSoup(post_html, "html.parser")
    content = soup.select_one(".patreon-post-content")
    if content is None:
        return post_html, 0
    saved = 0
    for n, img in enumerate(content.find_all("img"), 1):
        src = img.get("src") or ""
        if not src.startswith("http"):
            continue
        try:
            resp = context.request.get(src, timeout=30000)
            if not resp.ok:
                raise RuntimeError(f"HTTP {resp.status}")
            images_dir.mkdir(parents=True, exist_ok=True)
            name = save_image(resp.body(), images_dir / f"{prefix}_{n:02d}")
            img["src"] = f"images/{name}"
            img.attrs.pop("srcset", None)
            saved += 1
        except Exception as e:
            log(f"    image {n} failed ({type(e).__name__}: {e})")
    return str(soup), saved


# ---------------------------------------------------------------- collections list
def fetch_collections(list_url, log=print):
    """Return {'name': creator name, 'collections': [{'id','title','url'}, ...]}."""
    with sync_playwright() as p:
        browser, context = new_context(p)
        try:
            page = context.new_page()
            log(f"Opening {list_url}")
            page.goto(list_url)
            page.wait_for_function(TITLES_READY_JS, timeout=30000)
            scroll_until_stable(
                page,
                "() => document.querySelectorAll('[data-tag=\"box-collection-title\"]').length",
            )
            name = page.title().split(" | ")[0].strip()
            found = {}
            for c in page.evaluate(CARDS_JS):
                m = re.search(r"/collection/(\d+)", c["href"] or "")
                if m and m.group(1) not in found:  # cards appear twice
                    cid = m.group(1)
                    found[cid] = {
                        "id": cid,
                        "title": c["title"],
                        "url": f"https://www.patreon.com/collection/{cid}",
                    }
            log(f"{name}: found {len(found)} collections")
            return {"name": name, "collections": list(found.values())}
        finally:
            browser.close()


# ---------------------------------------------------------------- one collection
def collect_post_urls(page, collection_url, cid, log):
    page.goto(collection_url)
    page.wait_for_function(
        "() => !document.querySelector('svg[aria-label=\"Loading\"]')", timeout=30000
    )
    page.wait_for_selector(POST_LINK, timeout=30000)

    expected = None
    m = re.search(r"(\d+) posts", page.title())
    if m:
        expected = int(m.group(1))

    while True:
        btn = page.get_by_role("button", name="Load more")
        if btn.count() == 0:
            break
        before = page.locator(POST_LINK).count()
        btn.first.scroll_into_view_if_needed()
        btn.first.click()
        try:
            page.wait_for_function(
                "([sel, n]) => document.querySelectorAll(sel).length > n",
                arg=[POST_LINK, before],
                timeout=15000,
            )
        except PlaywrightTimeout:
            break
        log(f"  loaded more posts ({page.locator(POST_LINK).count()} links)")

    urls = page.evaluate(POST_URLS_JS, cid)
    urls.reverse()  # page is newest-first; book is oldest-first
    return urls, expected


def extract_post(page, url):
    page.goto(url)
    body = page.locator('div[class*="singleColumnPost"] > div').first
    body.wait_for(state="visible", timeout=30000)
    try:  # the text itself can render a moment later
        page.wait_for_selector(".patreon-post-content", timeout=8000)
    except PlaywrightTimeout:
        pass  # e.g. a post without text; keep the title only
    return body.inner_html()


def scrape_collection(
    collection_url, title=None, author="", cover_path=None, log=print, progress=None
):
    cid = re.search(r"/collection/(\d+)", collection_url).group(1)

    with sync_playwright() as p:
        browser, context = new_context(p)
        try:
            page = context.new_page()
            urls, expected = collect_post_urls(page, collection_url, cid, log)
            if title is None:
                title = page.title().split(" | ")[0]
            log(f"{title}: {len(urls)} posts found (page says {expected})")

            folder = BOOKS_DIR / f"{cid} - {safe_name(title)}"
            images_dir = folder / "images"
            shutil.rmtree(images_dir, ignore_errors=True)  # fresh image set per scrape
            folder.mkdir(parents=True, exist_ok=True)

            posts, failed = [], []

            if progress:
                progress(0, len(urls))
            for i, url in enumerate(urls, 1):
                try:
                    post_html = extract_post(page, url)
                    post_html, n_img = localize_images(
                        context, post_html, images_dir, f"{i:03d}", log
                    )
                    posts.append((url, post_html))
                    log(
                        f"  [{i}/{len(urls)}] ok"
                        + (f" ({n_img} images)" if n_img else "")
                    )
                except Exception as e:
                    failed.append(url)
                    log(f"  [{i}/{len(urls)}] FAILED {url}: {type(e).__name__}")

                if progress:
                    progress(i, len(urls))
                page.wait_for_timeout(500)
        finally:
            browser.close()

    sections = [
        f'<section class="scraped-post" data-url="{htmlmod.escape(url, quote=True)}">\n'
        f"{post_html}\n</section>"
        for url, post_html in posts
    ]
    raw = (
        '<html><head><meta charset="utf-8"></head><body>\n'
        + "\n".join(sections)
        + "\n</body></html>"
    )
    (folder / "raw.html").write_text(raw, encoding="utf-8")
    pdf_path = write_outputs(folder, title, author, cover_path, raw, log)
    log(f"Saved {len(posts)} posts to {folder}")

    return {
        "id": cid,
        "title": title,
        "folder": str(folder),
        "pdf": str(pdf_path),
        "chapters": len(posts),
        "expected": expected,
        "failed": failed,
    }


def refresh_login(wait_for_user, log=print):
    """Open a clean browser for a manual login and save the session.

    wait_for_user() blocks until the user confirms; returns True to save, False to cancel.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        try:
            context = browser.new_context()  # fresh context, no old session
            page = context.new_page()
            page.goto("https://www.patreon.com/login")
            log("Browser opened. Log in there, then confirm in the app window.")
            if not wait_for_user():
                log("Login refresh cancelled; auth.json unchanged.")
                return False
            context.storage_state(path=str(AUTH_FILE))
            log("Login session saved to auth.json")
            return True
        finally:
            browser.close()


# ---------------------------------------------------------------- CLI test
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python scraper.py <collections-page-url | collection-url>")
    elif "/collection/" in sys.argv[1]:
        print(scrape_collection(sys.argv[1]))
    else:
        for c in fetch_collections(sys.argv[1])["collections"]:
            print(c["id"], c["title"])
