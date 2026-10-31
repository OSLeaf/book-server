# Book server

A desktop app that turns Patreon collections into readable PDFs, and is growing into a personal book library.

## Background

I started this as reading books in Patreon is not fun experience. It is clearly not intended for book reading as the default order is "newest first" (spoilers) and there is no "next chapter" buttons. Every time you want to read the next chapter you first have to find it by scrolling trough as many spoilers as possible. This software scrapes them and turns them into comfortable pdf reading experience. Furthermore I have found it irritating when some early chapters disappear from Royal Road and I can no longer go to relive some of the best moments in the books that I have red. So I will soon add functionality to scrape those as well.

This has since expanded beyond that. Easier Patreon compatibility with GUI was added, so the app is becoming a general book library for me. The longer-term plan is to host it on my home server and add a reader, so I can access it via internet without the need of loading the resulting pdf to my devices.

## Roadmap

- Royal Road downloader.
- Host the library on a home server
- Web reader, so books can be read from phone, tablet and computer

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
