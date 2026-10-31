"""Persistent state: creators, their collections, scrape records and covers."""

import json
import shutil
import tempfile
import threading
import time
import uuid
from datetime import datetime
from io import BytesIO
from pathlib import Path
import copy
from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas

import scraper
from svg_render import svg_to_png

BASE_DIR = Path(__file__).resolve().parent
LIBRARY_FILE = BASE_DIR / "library.json"
COVERS_DIR = BASE_DIR / "covers"


class Library:
    def __init__(self, path=LIBRARY_FILE, covers_dir=COVERS_DIR):
        self.path = Path(path)
        self.covers_dir = Path(covers_dir)
        self._lock = threading.Lock()
        self.data = self._load()
        self.save()  # writes the migrated, relative paths immediately

    # ------------------------------------------------ storage
    @staticmethod
    def _to_abs(s):
        """Stored path -> absolute path under the current project folder.
        Old absolute entries are re-anchored on their 'books'/'covers' part,
        so a renamed or moved project folder keeps working."""
        if not s:
            return s
        p = Path(s)
        if not p.is_absolute():
            return str(BASE_DIR / p)
        parts = p.parts
        for anchor in ("books", "covers"):
            if anchor in parts:
                idx = len(parts) - 1 - parts[::-1].index(anchor)  # rightmost match
                cand = BASE_DIR.joinpath(*parts[idx:])
                if cand.exists():
                    return str(cand)
        return s  # nothing found locally: leave untouched

    @staticmethod
    def _to_rel(s):
        """Absolute path inside the project -> forward-slash relative path."""
        if not s:
            return s
        p = Path(s)
        for cand in (p, p.resolve()):
            try:
                return cand.relative_to(BASE_DIR).as_posix()
            except ValueError:
                pass
        return s  # outside the project folder: keep as is

    @staticmethod
    def _convert_paths(data, fn):
        for rec in data["scraped"].values():
            for key in ("folder", "pdf"):
                if rec.get(key):
                    rec[key] = fn(rec[key])
        for cid, p in data["covers"].items():
            if p:
                data["covers"][cid] = fn(p)

    def _load(self):
        data = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
        if "creators" not in data:  # migrate the old single-creator format
            data["creators"] = {}
            slug = scraper.creator_slug(data.get("list_url", ""))
            if slug:
                data["creators"][slug] = {
                    "name": slug,
                    "list_url": scraper.collections_url(slug),
                    "collections": data.get("known", []),
                }
            data["last_creator"] = slug
            data.pop("list_url", None)
            data.pop("known", None)
        data.setdefault("scraped", {})
        data.setdefault("covers", {})
        data.setdefault("renames", {})
        data.setdefault("last_creator", None)
        data.setdefault("appearance", "dark")
        data.setdefault("ui_scale", None)
        data.setdefault("text_scale", 1.0)
        self._convert_paths(data, self._to_abs)
        return data

    def save(self):
        with self._lock:
            out = copy.deepcopy(self.data)
            self._convert_paths(out, self._to_rel)
            self.path.write_text(
                json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    # ------------------------------------------------ settings
    @property
    def appearance(self):
        return self.data["appearance"]

    @appearance.setter
    def appearance(self, value):
        self.data["appearance"] = value
        self.save()

    @property
    def ui_scale(self):
        return self.data["ui_scale"]

    @ui_scale.setter
    def ui_scale(self, value):
        self.data["ui_scale"] = value
        self.save()

    @property
    def text_scale(self):
        return self.data["text_scale"]

    @text_scale.setter
    def text_scale(self, value):
        self.data["text_scale"] = value
        self.save()

    @property
    def last_creator(self):
        return self.data["last_creator"]

    @last_creator.setter
    def last_creator(self, slug):
        self.data["last_creator"] = slug
        self.save()

    # ------------------------------------------------ creators
    def creator_slugs(self):
        c = self.data["creators"]
        return sorted(c, key=lambda s: c[s]["name"].lower())

    def creator(self, slug):
        return self.data["creators"].get(slug)

    def add_creator(self, slug):
        if slug not in self.data["creators"]:
            self.data["creators"][slug] = {
                "name": slug,
                "list_url": scraper.collections_url(slug),
                "collections": [],
            }
        self.data["last_creator"] = slug
        self.save()

    def remove_creator(self, slug):
        self.data["creators"].pop(slug, None)
        if self.data["last_creator"] == slug:
            self.data["last_creator"] = None
        self.save()

    def update_creator(self, slug, name, collections):
        """Merge a fresh Patreon list into the stored one. Nothing is ever removed:
        entries missing from Patreon (taken down by the author) and manually added
        books stay in the list."""
        c = self.data["creators"].get(slug)
        if not c:
            return
        c["name"] = name or c["name"]
        fresh = {x["id"]: x for x in collections}
        merged, seen = [], set()
        for old in c["collections"]:
            if old["id"] in fresh:
                merged.append(fresh[old["id"]])  # refresh title and url
                seen.add(old["id"])
            else:
                merged.append(old)  # manual, or no longer on Patreon: keep
        merged += [x for x in collections if x["id"] not in seen]  # new ones
        c["collections"] = merged
        self.save()

    def collections(self, slug):
        c = self.data["creators"].get(slug)
        return c["collections"] if c else []

    # ------------------------------------------------ display names
    def display_title(self, cid, real_title):
        return self.data["renames"].get(cid) or real_title

    def set_display_title(self, cid, name, real_title):
        """Set the name shown in the list. An empty name, or the original one,
        removes the rename. Nothing else (files, PDF, scraping) is affected."""
        name = (name or "").strip()
        if not name or name == real_title:
            self.data["renames"].pop(cid, None)
        else:
            self.data["renames"][cid] = name
        self.save()

    # ------------------------------------------------ scrape records
    def record(self, cid):
        return self.data["scraped"].get(cid)

    def set_record(self, cid, res):
        self.data["scraped"][cid] = {
            "title": res["title"],
            "folder": res["folder"],
            "pdf": res["pdf"],
            "chapters": res["chapters"],
            "expected": res["expected"],
            "failed": res["failed"],
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.save()

    def set_pdf(self, cid, pdf_path):
        rec = self.record(cid)
        if rec:
            rec["pdf"] = str(pdf_path)
            self.save()

    def status(self, cid):
        """-> (kind, short label, detail). kind is 'new', 'ok' or 'warn'."""
        rec = self.record(cid)
        if not rec:
            return "new", "Not downloaded", ""
        if rec.get("manual"):
            return "ok", "Imported PDF", rec["date"][:10]

        detail = f"{rec['chapters']}/{rec['expected']} posts  |  {rec['date'][:10]}"
        if rec["failed"]:
            return "warn", f"{len(rec['failed'])} failed", detail
        return "ok", "Downloaded", detail

    def pdf_path(self, cid):
        rec = self.record(cid)
        p = Path(rec["pdf"]) if rec and rec.get("pdf") else None
        return p if p and p.exists() else None

    def folder_path(self, cid):
        rec = self.record(cid)
        p = Path(rec["folder"]) if rec else None
        return p if p and p.exists() else None

    # ------------------------------------------------ manually added books
    def add_manual_book(self, slug, title, pdf_source):
        """Add a book that does not come from Patreon: copy the PDF into books/
        and register it like a scraped collection. Returns the new id."""
        creator = self.data["creators"][slug]
        cid = f"pdf-{uuid.uuid4().hex[:8]}"
        name = scraper.safe_name(title)
        folder = scraper.BOOKS_DIR / f"{cid} - {name}"
        folder.mkdir(parents=True, exist_ok=True)
        pdf = folder / f"{name}.pdf"
        shutil.copyfile(pdf_source, pdf)
        creator["collections"].append(
            {"id": cid, "title": title, "url": "", "manual": True}
        )
        self.data["scraped"][cid] = {
            "title": title,
            "folder": str(folder),
            "pdf": str(pdf),
            "chapters": 0,
            "expected": 0,
            "failed": [],
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "manual": True,
        }
        self.save()
        return cid

    def is_manual(self, cid):
        rec = self.record(cid)
        return bool(rec and rec.get("manual"))

    def remove_manual_book(self, slug, cid):
        """Remove a manual book from the list. Its files stay on disk."""
        if not self.is_manual(cid):
            return
        c = self.data["creators"].get(slug)
        if c:
            c["collections"] = [x for x in c["collections"] if x["id"] != cid]
        self.data["scraped"].pop(cid, None)
        self.data["covers"].pop(cid, None)
        self.data["renames"].pop(cid, None)
        self.save()

    # ------------------------------------------------ covers
    def cover_path(self, cid):
        p = self.data["covers"].get(cid)
        return Path(p) if p and Path(p).exists() else None

    def set_cover(self, cid, source_path):
        """Store a normalized JPEG copy of the chosen image (PNG, JPG, ... or SVG).
        Raises on unreadable files."""
        self.covers_dir.mkdir(exist_ok=True)
        target = self.covers_dir / f"{cid}.jpg"
        source = Path(source_path)
        if source.suffix.lower() == ".svg":
            shutil.copyfile(
                source, self.covers_dir / f"{cid}.svg"
            )  # keep the vector original
            with tempfile.TemporaryDirectory() as tmp:
                png = svg_to_png(source, Path(tmp) / "cover.png")
                # flat artwork: no chroma subsampling keeps edges and text crisp
                Image.open(png).convert("RGB").save(target, quality=95, subsampling=0)
        else:
            Image.open(source).convert("RGB").save(target, quality=92)
        self.data["covers"][cid] = str(target)
        self.save()
        if self.is_manual(cid) and self.pdf_path(cid):
            self.apply_cover_to_pdf(self.pdf_path(cid), target)
        return target

    def apply_cover_to_pdf(self, pdf_path, cover_path):
        """Prepend the cover image as page 1 of an existing PDF.

        The cover page takes the size of the book's current first page, so
        it matches regardless of the PDF's original format (this one is
        6x9 in, not A5). The original is kept as <name>.orig.pdf on the
        first run, so the operation can be repeated without stacking covers.
        """
        pdf_path = Path(pdf_path)
        backup = pdf_path.with_suffix(".orig.pdf")
        if not backup.exists():
            shutil.copy2(pdf_path, backup)  # untouched original

        src = PdfReader(str(backup))
        box = src.pages[0].mediabox
        w, h = float(box.width), float(box.height)

        buf = BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(w, h))
        with Image.open(cover_path) as im:
            iw, ih = im.size
        scale = max(w / iw, h / ih)  # fill the page, not fit inside it
        dw, dh = iw * scale, ih * scale
        c.saveState()
        p = c.beginPath()
        p.rect(0, 0, w, h)
        c.clipPath(p, stroke=0, fill=0)  # discard the overflow explicitly
        c.drawImage(
            str(cover_path),
            (w - dw) / 2,  # centre the image; the overflow
            (h - dh) / 2,  # is cut equally on both sides
            width=dw,
            height=dh,
        )
        c.restoreState()
        c.save()
        buf.seek(0)

        out = PdfWriter()
        out.append(PdfReader(buf))  # cover first
        out.append(src)  # then all original pages
        tmp = pdf_path.with_suffix(".tmp.pdf")
        with open(tmp, "wb") as f:
            out.write(f)
        tmp.replace(pdf_path)  # swap in only after a full write

    # ------------------------------------------------ login
    @staticmethod
    def login_age_days():
        """Days since auth.json was written, or None if it does not exist."""
        if not scraper.AUTH_FILE.exists():
            return None
        return (time.time() - scraper.AUTH_FILE.stat().st_mtime) / 86400
