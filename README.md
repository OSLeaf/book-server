# Book server

A desktop app that turns Patreon collections into readable PDFs, and is growing into a personal book library.

## Background

The project started with one purpose: getting books published in Patreon into a pdf format that is actually comfortable to read. Patreon is clearly not intended for book reading as the default order is "newest first" (spoilers) and there is no "next chapter" buttons.

It has since expanded beyond that. Books that do not come from Patreon can now be added too, so the app is becoming a general library for personal reading. The longer-term plan is to host it on my home server and add a reader, so I can access it via internet without the need of loading the resulting pdf to my devices.

## Current features

- **Creators and collections**: add a Patreon creator and list their collections; the list is refreshed without ever deleting entries, so books stay in the library even if the author later removes them from Patreon
- **PDF generation**: each collection is downloaded and built into an A5 PDF with cover, title page and page numbers
- **Covers**: set a cover from any image (PNG, JPG, ...) or from an SVG, which is rendered at print resolution and is not tied to a fixed pixel size
- **Imported PDFs**: add books that are not from Patreon; the cover can be applied to them as a new first page (the untouched original is kept as `*.orig.pdf`, so the cover can be changed repeatedly)
- **Display names**: rename any book without affecting files, scraping or the PDF; the original name is shown on hover
- **Natural sorting and filtering**: the list is ordered alphabetically with numbers handled correctly

## Roadmap

- Host the library on a home server
- Web reader, so books can be read from phone, tablet and computer
- Reading position and library state shared between devices

## Requirements

- Python 3.10 or newer
- Chromium for Playwright (used for scraping and for rendering SVG covers)

```
pip install customtkinter pillow playwright reportlab beautifulsoup4 pypdf
playwright install chromium
```

## Running

```
python app.py
```

## Project layout

| File            | Purpose                                                                  |
| --------------- | ------------------------------------------------------------------------ |
| `app.py`        | Desktop interface (CustomTkinter)                                        |
| `library.py`    | Persistent state: creators, collections, scrape records, covers, renames |
| `scraper.py`    | Patreon scraping and PDF building                                        |
| `svg_render.py` | SVG cover rendering through headless Chromium                            |

Runtime data is created next to the code:

| Path           | Contents                         |
| -------------- | -------------------------------- |
| `library.json` | Library state                    |
| `books/`       | Generated and imported PDFs      |
| `covers/`      | Cover images (and SVG originals) |
| `auth.json`    | Saved Patreon login session      |

## Notes

- Everything under `books/`, `covers/`, `library.json` and `auth.json` is personal data and is excluded from version control.
- Intended for personal use with content the user has legitimate access to.
