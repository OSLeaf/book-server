import os
import platform
import queue
import re
import subprocess
import threading
import tkinter as tk
from tkinter import filedialog
from tkinter import font as tkfont

import customtkinter as ctk
from PIL import Image

import scraper
from library import Library

# ---- appearance settings ------------------------------------------------------------
FONT_FAMILY = None  # e.g. "Noto Sans" to force one; None = pick automatically
FONT_CANDIDATES = [
    "Inter",
    "Noto Sans",
    "Cantarell",
    "Ubuntu",
    "Segoe UI",
    "DejaVu Sans",
    "Liberation Sans",
    "Arial",
    "Helvetica",
]
MONO_CANDIDATES = [
    "JetBrains Mono",
    "DejaVu Sans Mono",
    "Noto Sans Mono",
    "Liberation Mono",
    "Consolas",
    "Courier New",
]
SCALES = [
    1.0,
    1.25,
    1.5,
    1.75,
    2.0,
    2.25,
    2.5,
    3.0,
]  # steps of the interface size buttons
TEXT_SCALES = [0.9, 1.0, 1.1, 1.25]  # steps of the text size buttons
BASE_SIZES = {  # font pixel sizes at 100%
    "body": 16,
    "bold": 16,
    "small": 14,
    "tiny": 12,
    "row_title": 17,
    "title": 30,
    "brand": 22,
    "mono": 14,
}
NATIVE_FONTS = (
    "TkDefaultFont",
    "TkTextFont",
    "TkMenuFont",
    "TkFixedFont",
    "TkHeadingFont",
    "TkCaptionFont",
    "TkSmallCaptionFont",
    "TkIconFont",
    "TkTooltipFont",
)
THUMB_H = 52  # cover thumbnail box in the list (px)
THUMB_W = 44

SECONDARY = dict(
    fg_color=("gray85", "gray25"),
    hover_color=("gray78", "gray32"),
    text_color=("gray10", "gray92"),
)
SUBTLE = dict(
    fg_color="transparent",
    hover_color=("gray85", "gray25"),
    text_color=("gray25", "gray80"),
    border_width=1,
    border_color=("gray70", "gray35"),
)
DANGER = dict(
    fg_color=("#fde2e2", "#4a1f1f"),
    hover_color=("#fbc9c9", "#612828"),
    text_color=("#b42318", "#ff9b92"),
)
CHIPS = {  # kind -> (background, text colour)
    "new": (("#e5e7eb", "#343841"), ("#4b5563", "#aab1bd")),
    "ok": (("#d5f0dc", "#1c4a2b"), ("#166534", "#88e2a5")),
    "warn": (("#fde7c7", "#5a3a0b"), ("#9a5a00", "#f5c27a")),
}
GREEN = ("#16a34a", "#4ade80")
ORANGE = ("#d97706", "#fbbf24")
RED = ("#dc2626", "#f87171")


def open_path(path):
    path = str(path)
    system = platform.system()
    if system == "Windows":
        os.startfile(path)
    elif system == "Darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Fonts:
    """One shared font object per role. Changing the text size reconfigures these
    objects, and every widget that uses them follows automatically."""

    def __init__(self, family, mono_family):
        self.family = family
        self.mono_family = mono_family
        self.fonts = {}
        for name, size in BASE_SIZES.items():
            weight = (
                "bold"
                if name in ("bold", "row_title", "title", "brand", "tiny")
                else "normal"
            )
            self.fonts[name] = ctk.CTkFont(
                family=mono_family if name == "mono" else family,
                size=size,
                weight=weight,
            )

    def __getattr__(self, name):
        try:
            return self.__dict__["fonts"][name]
        except KeyError:
            raise AttributeError(name)

    def set_scale(self, k):
        for name, size in BASE_SIZES.items():
            self.fonts[name].configure(size=max(8, round(size * k)))


def natural_key(text):
    """Case-insensitive sort key that orders numbers numerically ('Book 2' < 'Book 10')."""
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", text)]


def pick_family(candidates, fallback):
    available = {f.lower(): f for f in tkfont.families()}
    for name in candidates:
        if name.lower() in available:
            return available[name.lower()]
    return fallback


# ====================================================================== dialogs
class Popup(ctk.CTkToplevel):
    """Small message window. buttons = [(label, value, style_kwargs | None), ...];
    the first button doubles as the 'close window' answer."""

    def __init__(self, master, title, message, buttons, on_choice=None, modal=False):
        super().__init__(master)
        fonts = master.fonts
        self.title(title)
        self.resizable(False, False)
        self.choice = None
        self._first = buttons[0][1]
        self._on_choice = on_choice
        ctk.CTkLabel(
            self,
            text=message,
            justify="left",
            wraplength=480,
            anchor="w",
            font=fonts.body,
        ).pack(padx=26, pady=(24, 18), anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=26, pady=(0, 22), anchor="e")
        for label, value, style in buttons:
            ctk.CTkButton(
                row,
                text=label,
                width=120,
                height=38,
                font=fonts.body,
                command=lambda v=value: self._pick(v),
                **(style or {}),
            ).pack(side="left", padx=(8, 0))
        self.protocol("WM_DELETE_WINDOW", lambda: self._pick(self._first))
        self.after(60, lambda: self._show(modal))

    def _show(self, modal):
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - self.winfo_reqwidth()) // 2
        y = m.winfo_rooty() + (m.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.transient(m)
        self.lift()
        self.focus_force()
        if modal:
            try:
                self.grab_set()
            except Exception:
                pass

    def _pick(self, value):
        self.choice = value
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()
        if self._on_choice:
            self._on_choice(value)


class Tooltip:
    """Hover text for a widget. The text can be changed at any time; empty = none."""

    def __init__(self, widget, text="", delay=400):
        self.widget, self.text, self.delay = widget, text, delay
        self._tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._hide()
        if self.text:
            self._after = self.widget.after(self.delay, self._show)

    def _show(self):
        self._after = None
        if not self.text:
            return
        x = self.widget.winfo_rootx() + 14
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip,
            text=self.text,
            justify="left",
            background="#222222",
            foreground="#eeeeee",
            relief="solid",
            borderwidth=1,
            padx=8,
            pady=4,
            wraplength=520,
        ).pack()

    def _hide(self, _event=None):
        if self._after:
            self.widget.after_cancel(self._after)
            self._after = None
        if self._tip:
            self._tip.destroy()
            self._tip = None


class TextPrompt(ctk.CTkToplevel):
    """Modal one-line text prompt with a prefilled entry.
    result: the text, "" for the optional extra button, or None if cancelled."""

    def __init__(self, master, title, message, initial="", ok_text="Save", extra=None):
        super().__init__(master)
        fonts = master.fonts
        self.title(title)
        self.resizable(False, False)
        self.result = None
        ctk.CTkLabel(
            self,
            text=message,
            justify="left",
            wraplength=520,
            anchor="w",
            font=fonts.body,
        ).pack(padx=26, pady=(24, 10), anchor="w")
        self.entry = ctk.CTkEntry(self, width=520, height=40, font=fonts.body)
        self.entry.pack(padx=26, pady=(0, 16))
        self.entry.insert(0, initial)
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(padx=26, pady=(0, 22), anchor="e")
        ctk.CTkButton(
            row,
            text="Cancel",
            width=110,
            height=38,
            font=fonts.body,
            command=self._cancel,
            **SECONDARY,
        ).pack(side="left", padx=(8, 0))
        if extra:
            ctk.CTkButton(
                row,
                text=extra[0],
                width=140,
                height=38,
                font=fonts.body,
                command=lambda: self._done(extra[1]),
                **SUBTLE,
            ).pack(side="left", padx=(8, 0))
        ctk.CTkButton(
            row,
            text=ok_text,
            width=110,
            height=38,
            font=fonts.body,
            command=lambda: self._done(self.entry.get().strip()),
        ).pack(side="left", padx=(8, 0))
        self.entry.bind("<Return>", lambda e: self._done(self.entry.get().strip()))
        self.bind("<Escape>", lambda e: self._cancel())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.after(60, self._show)

    def _show(self):
        self.update_idletasks()
        m = self.master
        x = m.winfo_rootx() + (m.winfo_width() - self.winfo_reqwidth()) // 2
        y = m.winfo_rooty() + (m.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.transient(m)
        self.lift()
        self.focus_force()
        self.entry.focus_set()
        self.entry.select_range(0, "end")
        try:
            self.grab_set()
        except Exception:
            pass

    def _done(self, value):
        self.result = value
        self._close()

    def _cancel(self):
        self.result = None
        self._close()

    def _close(self):
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()


# ====================================================================== list row
class CollectionRow(ctk.CTkFrame):
    def __init__(self, master, app, collection):
        super().__init__(master, corner_radius=10, fg_color=("white", "gray20"))
        fonts = app.fonts
        self.app = app
        self.cid = collection["id"]
        self.real_title = collection["title"]  # never changes: scraping and PDFs use it
        self.title_text = app.lib.display_title(self.cid, self.real_title)
        self.visible = True
        self._thumb_key = None
        self._thumb_img = None
        self.columnconfigure(2, weight=1)

        self.var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            self,
            text="",
            variable=self.var,
            width=26,
            checkbox_width=24,
            checkbox_height=24,
            command=app.on_selection_changed,
        ).grid(row=0, column=0, padx=(14, 6), pady=10)

        self.thumb = ctk.CTkLabel(
            self,
            text="no\ncover",
            width=THUMB_W,
            height=THUMB_H,
            corner_radius=6,
            fg_color=("gray88", "gray28"),
            text_color=("gray45", "gray60"),
            font=fonts.tiny,
        )
        self.thumb.grid(row=0, column=1, padx=(4, 12), pady=8)

        text = ctk.CTkFrame(self, fg_color="transparent")
        text.grid(row=0, column=2, sticky="ew")
        self._wrap = 0
        text.bind("<Configure>", self._on_text_resize)
        self.title_label = ctk.CTkLabel(
            text,
            text=self.title_text,
            anchor="w",
            justify="left",
            wraplength=400,
            font=fonts.row_title,
        )
        self.title_label.pack(anchor="w")
        self.detail_label = ctk.CTkLabel(
            text,
            text="",
            anchor="w",
            justify="left",
            wraplength=400,
            font=fonts.small,
            text_color=("gray40", "gray65"),
        )
        self.detail_label.pack(anchor="w")

        self.chip = ctk.CTkLabel(
            self, text="", width=140, height=28, corner_radius=14, font=fonts.small
        )
        self.chip.grid(row=0, column=3, padx=12)

        self.btn_cover = ctk.CTkButton(
            self,
            text="Cover...",
            width=92,
            height=36,
            font=fonts.body,
            command=lambda: app.set_cover(self.cid),
            **SECONDARY,
        )
        self.btn_cover.grid(row=0, column=4, padx=3)
        self.btn_pdf = ctk.CTkButton(
            self,
            text="Open PDF",
            width=104,
            height=36,
            font=fonts.body,
            command=lambda: app.open_pdf(self.cid),
            **SECONDARY,
        )
        self.btn_pdf.grid(row=0, column=5, padx=3)
        self.btn_folder = ctk.CTkButton(
            self,
            text="Folder",
            width=80,
            height=36,
            font=fonts.body,
            command=lambda: app.open_folder(self.cid),
            **SECONDARY,
        )
        self.btn_folder.grid(row=0, column=6, padx=3)

        self.btn_rename = ctk.CTkButton(
            self,
            text="Rename",
            width=88,
            height=36,
            font=fonts.body,
            command=lambda: app.rename_collection(self.cid),
            **SECONDARY,
        )
        self.btn_rename.grid(row=0, column=7, padx=(3, 14))

        if app.lib.is_manual(self.cid):
            self.btn_drop = ctk.CTkButton(
                self,
                text="Remove",
                width=80,
                height=36,
                font=fonts.body,
                command=lambda: app.remove_manual(self.cid),
                **DANGER,
            )
            self.btn_drop.grid(row=0, column=8, padx=(3, 14))
            self.btn_rename.grid_configure(padx=3)

        self.tip = Tooltip(self.title_label)
        self.update_title()
        self.refresh()

    def _on_text_resize(self, event):
        # the label wraps at the real width of its column (CustomTkinter scales
        # wraplength itself, so divide the scaling out)
        wrap = max(200, int(event.width / self.app.scale) - 8)
        if abs(wrap - self._wrap) > 2:  # ignore tiny changes to avoid layout loops
            self._wrap = wrap
            self.title_label.configure(wraplength=wrap)
            self.detail_label.configure(wraplength=wrap)

    def update_title(self):
        self.title_text = self.app.lib.display_title(self.cid, self.real_title)
        self.title_label.configure(text=self.title_text)
        renamed = self.title_text != self.real_title
        self.tip.text = f"Original name: {self.real_title}" if renamed else ""

    def refresh(self):
        lib = self.app.lib
        kind, label, detail = lib.status(self.cid)
        bg, fg = CHIPS[kind]
        self.chip.configure(text=label, fg_color=bg, text_color=fg)
        renamed = self.title_text != self.real_title
        self.detail_label.configure(
            text=f"#{self.cid}"
            + ("   |   renamed" if renamed else "")
            + (f"   |   {detail}" if detail else "")
        )
        self.btn_pdf.configure(state="normal" if lib.pdf_path(self.cid) else "disabled")
        self.btn_folder.configure(
            state="normal" if lib.folder_path(self.cid) else "disabled"
        )

        cover = lib.cover_path(self.cid)
        key = (str(cover), cover.stat().st_mtime) if cover else None
        if key != self._thumb_key:
            self._thumb_key = key
            if cover:
                im = Image.open(cover).convert("RGB")
                scale = min(
                    THUMB_W / im.width, THUMB_H / im.height
                )  # fit inside the box
                size = (
                    max(1, round(im.width * scale)),
                    max(1, round(im.height * scale)),
                )
                self._thumb_img = ctk.CTkImage(light_image=im, dark_image=im, size=size)
                self.thumb.configure(
                    image=self._thumb_img, text="", fg_color="transparent"
                )


# ====================================================================== application
class App(ctk.CTk):
    def __init__(self):
        lib = Library()
        ctk.set_appearance_mode(lib.appearance)
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.lib = lib
        self.title("Patreon Collection Scraper")

        default_family = tkfont.nametofont("TkDefaultFont").actual("family")
        family = FONT_FAMILY or pick_family(FONT_CANDIDATES, default_family)
        mono = pick_family(
            MONO_CANDIDATES, tkfont.nametofont("TkFixedFont").actual("family")
        )
        ctk.ThemeManager.theme["CTkFont"]["family"] = (
            family  # for any widget without a font
        )
        ctk.ThemeManager.theme["CTkFont"]["size"] = BASE_SIZES["body"]
        self.fonts = Fonts(family, mono)
        self.text_scale = lib.text_scale if lib.text_scale in TEXT_SCALES else 1.0
        self.fonts.set_scale(self.text_scale)
        self.apply_scale(lib.ui_scale or self.auto_scale())

        self.events = queue.Queue()  # worker thread -> GUI thread
        self.busy = False
        self.rows = {}  # collection id -> CollectionRow
        self.current = None  # selected creator slug
        self.log_visible = False

        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_main()

        self.update_login_status()
        self.say(f"UI font: {family} / {mono}")
        slugs = self.lib.creator_slugs()
        last = self.lib.last_creator
        self.select_creator(last if last in slugs else (slugs[0] if slugs else None))
        self.after(100, self.drain_events)

    # ------------------------------------------------ interface size
    def allowed_scales(self):
        """Sizes that still fit the screen (the layout needs about 1350 x 780 units)."""
        usable = min(self.winfo_screenwidth() / 1350, self.winfo_screenheight() / 780)
        return [v for v in SCALES if v <= usable] or [SCALES[0]]

    def auto_scale(self):
        """First-start guess from the screen resolution (CustomTkinter cannot detect
        the display density on Linux). The size buttons in the sidebar override it."""
        s = min(self.winfo_screenwidth() / 1920, self.winfo_screenheight() / 1080)
        return min(max(round(s * 4) / 4, 1.0), 3.0)

    def apply_scale(self, s):
        allowed = self.allowed_scales()
        s = min(allowed, key=lambda v: abs(v - min(s, allowed[-1])))
        self.scale = s
        ctk.set_widget_scaling(s)
        ctk.set_window_scaling(s)
        size = max(8, round(10 * s))  # also scales the native file dialog
        for name in NATIVE_FONTS:
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = int(min(1350, sw * 0.96 / s)), int(min(840, sh * 0.92 / s))
        self.geometry(f"{w}x{h}")
        self.minsize(min(1100, w), min(680, h))

    def change_scale(self, direction):
        allowed = self.allowed_scales()
        i = allowed.index(min(allowed, key=lambda v: abs(v - self.scale)))
        i = max(0, min(len(allowed) - 1, i + direction))
        self.apply_scale(allowed[i])
        self.lib.ui_scale = self.scale
        self.size_label.configure(text=f"{int(self.scale * 100)}%")

    def change_text_scale(self, direction):
        i = TEXT_SCALES.index(self.text_scale)
        i = max(0, min(len(TEXT_SCALES) - 1, i + direction))
        self.text_scale = TEXT_SCALES[i]
        self.fonts.set_scale(self.text_scale)
        self.lib.text_scale = self.text_scale
        self.text_label.configure(text=f"{int(round(self.text_scale * 100))}%")

    # ------------------------------------------------ layout: sidebar
    def _stepper(self, parent, row, caption, on_minus, on_plus, value):
        f = self.fonts
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.grid(row=row, column=0, sticky="ew", pady=(0, 8))
        box.columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text=caption, anchor="w", font=f.body).grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkButton(
            box,
            text="-",
            width=38,
            height=34,
            font=f.bold,
            command=on_minus,
            **SECONDARY,
        ).grid(row=0, column=1)
        label = ctk.CTkLabel(box, text=value, width=58, font=f.body)
        label.grid(row=0, column=2, padx=2)
        ctk.CTkButton(
            box,
            text="+",
            width=38,
            height=34,
            font=f.bold,
            command=on_plus,
            **SECONDARY,
        ).grid(row=0, column=3)
        return label

    def _build_sidebar(self):
        f = self.fonts
        sb = ctk.CTkFrame(
            self, width=300, corner_radius=0, fg_color=("gray90", "gray13")
        )
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.rowconfigure(2, weight=1)
        sb.columnconfigure(0, weight=1)

        ctk.CTkLabel(sb, text="Scraper", anchor="w", font=f.brand).grid(
            row=0, column=0, sticky="ew", padx=20, pady=(22, 2)
        )
        ctk.CTkLabel(
            sb,
            text="CREATORS",
            anchor="w",
            text_color=("gray40", "gray60"),
            font=f.tiny,
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(18, 4))

        self.creator_list = ctk.CTkScrollableFrame(sb, fg_color="transparent")
        self.creator_list.grid(row=2, column=0, sticky="nsew", padx=8)
        self.creator_list.columnconfigure(0, weight=1)

        add = ctk.CTkFrame(sb, fg_color="transparent")
        add.grid(row=3, column=0, sticky="ew", padx=16, pady=(10, 6))
        add.columnconfigure(0, weight=1)
        self.add_entry = ctk.CTkEntry(
            add, placeholder_text="Paste creator URL", height=40, font=f.body
        )
        self.add_entry.grid(row=0, column=0, sticky="ew")
        self.add_entry.bind("<Return>", lambda e: self.add_creator())
        self.btn_add = ctk.CTkButton(
            add, text="+ Add creator", height=40, font=f.bold, command=self.add_creator
        )
        self.btn_add.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        foot = ctk.CTkFrame(sb, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=16, pady=(14, 18))
        foot.columnconfigure(0, weight=1)

        login = ctk.CTkFrame(foot, fg_color="transparent")
        login.grid(row=0, column=0, sticky="ew")
        self.login_dot = ctk.CTkFrame(login, width=12, height=12, corner_radius=6)
        self.login_dot.grid(row=0, column=0, padx=(2, 8))
        self.login_label = ctk.CTkLabel(login, text="", anchor="w", font=f.small)
        self.login_label.grid(row=0, column=1, sticky="w")
        self.btn_login = ctk.CTkButton(
            foot,
            text="Refresh login",
            height=38,
            font=f.body,
            command=self.refresh_login,
            **SUBTLE,
        )
        self.btn_login.grid(row=1, column=0, sticky="ew", pady=(8, 12))

        self.size_label = self._stepper(
            foot,
            2,
            "Interface",
            lambda: self.change_scale(-1),
            lambda: self.change_scale(1),
            f"{int(self.scale * 100)}%",
        )
        self.text_label = self._stepper(
            foot,
            3,
            "Text",
            lambda: self.change_text_scale(-1),
            lambda: self.change_text_scale(1),
            f"{int(round(self.text_scale * 100))}%",
        )
        self.dark_switch = ctk.CTkSwitch(
            foot, text="Dark mode", font=f.body, command=self.toggle_appearance
        )
        self.dark_switch.grid(row=4, column=0, sticky="w", pady=(4, 0))
        if self.lib.appearance == "dark":
            self.dark_switch.select()

    # ------------------------------------------------ layout: main area
    def _build_main(self):
        f = self.fonts
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=22, pady=(18, 12))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)

        # header
        head = ctk.CTkFrame(main, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(head, text="", anchor="w", font=f.title)
        self.title_label.grid(row=0, column=0, sticky="w")
        self.subtitle_label = ctk.CTkLabel(
            head, text="", anchor="w", font=f.small, text_color=("gray40", "gray65")
        )
        self.subtitle_label.grid(row=1, column=0, sticky="w")
        self.btn_remove = ctk.CTkButton(
            head,
            text="Remove creator",
            width=150,
            height=38,
            font=f.body,
            command=self.remove_creator,
            **DANGER,
        )
        self.btn_remove.grid(row=0, column=1, rowspan=2, padx=(8, 8))
        self.btn_reload = ctk.CTkButton(
            head,
            text="Reload collections",
            width=180,
            height=38,
            font=f.body,
            command=self.reload_creator,
            **SECONDARY,
        )
        self.btn_reload.grid(row=0, column=2, rowspan=2)

        self.btn_add_pdf = ctk.CTkButton(
            head,
            text="Add PDF book...",
            width=170,
            height=38,
            font=f.body,
            command=self.add_pdf_book,
            **SECONDARY,
        )
        self.btn_add_pdf.grid(row=0, column=3, rowspan=2, padx=(8, 0))

        # filter bar
        bar = ctk.CTkFrame(main, fg_color="transparent")
        bar.grid(row=1, column=0, sticky="ew", pady=(14, 10))
        bar.columnconfigure(0, weight=1)
        self.filter_entry = ctk.CTkEntry(
            bar, placeholder_text="Filter collections...", height=40, font=f.body
        )
        self.filter_entry.grid(row=0, column=0, sticky="ew")
        self.filter_entry.bind("<KeyRelease>", lambda e: self.apply_filter())
        self.mode_buttons = ctk.CTkSegmentedButton(
            bar,
            values=["All", "Not downloaded", "Downloaded"],
            font=f.body,
            command=lambda v: self.apply_filter(),
            text_color=("gray10", "gray92"),
            unselected_color=("gray82", "gray25"),
            unselected_hover_color=("gray76", "gray32"),
            height=38,
        )
        self.mode_buttons.set("All")
        self.mode_buttons.grid(row=0, column=1, padx=(12, 0))

        # list
        self.list_frame = ctk.CTkScrollableFrame(
            main, corner_radius=12, fg_color=("gray93", "gray15")
        )
        self.list_frame.grid(row=2, column=0, sticky="nsew")
        self.list_frame.columnconfigure(0, weight=1)
        self.empty_label = ctk.CTkLabel(
            self.list_frame,
            text="",
            justify="center",
            text_color=("gray40", "gray60"),
            font=f.body,
        )

        # bulk bar
        bulk = ctk.CTkFrame(main, fg_color="transparent")
        bulk.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        bulk.columnconfigure(2, weight=1)
        self.btn_all = ctk.CTkButton(
            bulk,
            text="Select all",
            width=110,
            height=38,
            font=f.body,
            command=self.select_all,
            **SUBTLE,
        )
        self.btn_all.grid(row=0, column=0)
        self.btn_none = ctk.CTkButton(
            bulk,
            text="Clear",
            width=80,
            height=38,
            font=f.body,
            command=self.clear_selection,
            **SUBTLE,
        )
        self.btn_none.grid(row=0, column=1, padx=8)
        self.selection_label = ctk.CTkLabel(
            bulk, text="", anchor="w", font=f.body, text_color=("gray40", "gray65")
        )
        self.selection_label.grid(row=0, column=2, sticky="w", padx=6)
        self.btn_rebuild = ctk.CTkButton(
            bulk,
            text="Rebuild PDF",
            width=140,
            height=42,
            font=f.body,
            command=self.rebuild_selected,
            **SECONDARY,
        )
        self.btn_rebuild.grid(row=0, column=3, padx=8)
        self.btn_scrape = ctk.CTkButton(
            bulk,
            text="Scrape selected",
            width=180,
            height=42,
            font=f.bold,
            command=self.scrape_selected,
        )
        self.btn_scrape.grid(row=0, column=4)

        # log (hidden until toggled)
        self.log_box = ctk.CTkTextbox(main, height=170, state="disabled", font=f.mono)

        # footer: status + progress
        foot = ctk.CTkFrame(main, fg_color="transparent")
        foot.grid(row=5, column=0, sticky="ew", pady=(12, 0))
        foot.columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(
            foot,
            text="Ready",
            anchor="w",
            font=f.small,
            text_color=("gray35", "gray70"),
        )
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.progress = ctk.CTkProgressBar(foot, width=260, height=10)
        self.progress.set(0)
        self.progress.grid(row=0, column=1, padx=12)
        self.progress.grid_remove()  # shown only while a task runs
        self.btn_log = ctk.CTkButton(
            foot,
            text="Show log",
            width=100,
            height=32,
            font=f.small,
            command=self.toggle_log,
            **SUBTLE,
        )
        self.btn_log.grid(row=0, column=2)

        self.action_buttons = [
            self.btn_add,
            self.btn_login,
            self.btn_remove,
            self.btn_reload,
            self.btn_rebuild,
            self.btn_scrape,
        ]

    # ------------------------------------------------ thread plumbing
    def say(self, text):
        self.events.put(("log", text))

    def post(self, fn):
        self.events.put(("call", fn))

    def drain_events(self):
        self.after(100, self.drain_events)  # keep polling whatever happens
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.append_log(payload)
            else:
                payload()

    def append_log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        self.status_label.configure(text=text.strip()[:140])
        if text.startswith("ERROR") and not self.log_visible:
            self.toggle_log()

    def toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_box.grid(row=4, column=0, sticky="ew", pady=(12, 0))
            self.btn_log.configure(text="Hide log")
        else:
            self.log_box.grid_remove()
            self.btn_log.configure(text="Show log")

    def begin_task(self, text, indeterminate=False):
        self.busy = True
        for b in self.action_buttons:
            b.configure(state="disabled")
        self.status_label.configure(text=text)
        self.progress.grid()
        if indeterminate:
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.configure(mode="determinate")
            self.progress.set(0)

    def end_task(self, text="Ready"):
        self.busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self.progress.grid_remove()
        for b in self.action_buttons:
            b.configure(state="normal")
        self.on_selection_changed()
        self.status_label.configure(text=text)

    def set_progress(self, fraction, text):
        self.progress.set(max(0.0, min(1.0, fraction)))
        self.status_label.configure(text=text)

    def run(self, work):
        threading.Thread(target=work, daemon=True).start()

    def ask(self, title, message, buttons):
        popup = Popup(self, title, message, buttons, modal=True)
        self.wait_window(popup)
        return popup.choice

    def tell(self, title, message):
        self.ask(title, message, [("OK", None, None)])

    def ask_text(self, title, message, initial="", ok_text="Save", extra=None):
        prompt = TextPrompt(self, title, message, initial, ok_text, extra)
        self.wait_window(prompt)
        return prompt.result

    # ------------------------------------------------ creators
    def select_creator(self, slug):
        self.current = slug
        self.lib.last_creator = slug
        self.rebuild_creator_list()
        self.build_rows()

    def rebuild_creator_list(self):
        for w in self.creator_list.winfo_children():
            w.destroy()
        for i, slug in enumerate(self.lib.creator_slugs()):
            c = self.lib.creator(slug)
            selected = slug == self.current
            style = (
                dict(fg_color=("gray78", "gray27"), hover_color=("gray74", "gray30"))
                if selected
                else dict(fg_color="transparent", hover_color=("gray84", "gray22"))
            )
            ctk.CTkButton(
                self.creator_list,
                text=c["name"],
                anchor="w",
                height=42,
                text_color=("gray10", "gray92"),
                font=self.fonts.bold if selected else self.fonts.body,
                command=lambda s=slug: self.select_creator(s),
                **style,
            ).grid(row=i, column=0, sticky="ew", pady=2)

    def add_creator(self):
        if self.busy:
            return
        slug = scraper.creator_slug(self.add_entry.get().strip())
        if not slug:
            self.tell(
                "Invalid URL",
                "Paste a creator URL, for example:\n"
                "https://www.patreon.com/cw/alwaysrollsaone/collections\n"
                "or https://www.patreon.com/alwaysrollsaone",
            )
            return
        self.lib.add_creator(slug)
        self.add_entry.delete(0, "end")
        self.select_creator(slug)
        self.reload_creator()

    def remove_creator(self):
        if self.busy or not self.current:
            return
        name = self.lib.creator(self.current)["name"]
        if self.ask(
            "Remove creator",
            f"Remove '{name}' from the list?\n\nFiles that were already "
            "downloaded stay on disk.",
            [("Cancel", False, SECONDARY), ("Remove", True, DANGER)],
        ):
            self.lib.remove_creator(self.current)
            slugs = self.lib.creator_slugs()
            self.select_creator(slugs[0] if slugs else None)

    def reload_creator(self):
        if self.busy or not self.current:
            return
        slug = self.current
        url = self.lib.creator(slug)["list_url"]
        self.begin_task("Loading collections...", indeterminate=True)

        def work():
            try:
                result = scraper.fetch_collections(url, log=self.say)
                self.lib.update_creator(slug, result["name"], result["collections"])
                self.post(lambda: self.after_reload(slug))
            except Exception as e:
                self.say(f"ERROR while loading collections: {e!r}")
            finally:
                self.post(self.end_task)

        self.run(work)

    def after_reload(self, slug):
        self.rebuild_creator_list()
        if slug == self.current:
            self.build_rows()

    # ------------------------------------------------ collection list
    def build_rows(self):
        for row in self.rows.values():
            row.destroy()
        self.rows = {}
        self.empty_label.grid_remove()
        slug = self.current
        creator = self.lib.creator(slug) if slug else None
        if not creator:
            self.title_label.configure(text="No creator yet")
            self.subtitle_label.configure(text="")
            self.show_empty(
                "Add a creator in the sidebar to get started.\n"
                "Paste any link to their Patreon page."
            )
        else:
            self.title_label.configure(text=creator["name"])
            ordered = sorted(
                creator["collections"],
                key=lambda c: natural_key(self.lib.display_title(c["id"], c["title"])),
            )
            for c in ordered:
                self.rows[c["id"]] = CollectionRow(self.list_frame, self, c)
            if not creator["collections"]:
                self.show_empty(
                    'No collections loaded yet.\nClick "Reload collections".'
                )
        self.apply_filter()
        self.update_subtitle()
        self.on_selection_changed()

    def show_empty(self, text):
        self.empty_label.configure(text=text)
        self.empty_label.grid(row=0, column=0, pady=70)

    def update_subtitle(self):
        total = len(self.rows)
        done = sum(1 for cid in self.rows if self.lib.record(cid))
        self.subtitle_label.configure(
            text=f"{total} collections   |   {done} downloaded" if self.current else ""
        )

    def sort_rows(self):
        self.rows = dict(
            sorted(self.rows.items(), key=lambda kv: natural_key(kv[1].title_text))
        )

    def apply_filter(self):
        text = self.filter_entry.get().strip().lower()
        mode = self.mode_buttons.get()
        shown = 0
        for i, (cid, row) in enumerate(self.rows.items()):
            downloaded = self.lib.record(cid) is not None
            matches = text in row.title_text.lower() or text in row.real_title.lower()
            ok = matches and (
                mode == "All"
                or (mode == "Downloaded" and downloaded)
                or (mode == "Not downloaded" and not downloaded)
            )
            row.visible = ok
            if ok:
                row.grid(row=i, column=0, sticky="ew", padx=8, pady=4)
                shown += 1
            else:
                row.grid_remove()
        if self.rows:
            if shown == 0:
                self.show_empty("No collections match the filter.")
            else:
                self.empty_label.grid_remove()
        self.on_selection_changed()

    def refresh_row(self, cid):
        row = self.rows.get(cid)
        if row:
            row.refresh()
        self.update_subtitle()

    def selected_ids(self):
        return [cid for cid, row in self.rows.items() if row.visible and row.var.get()]

    def on_selection_changed(self):
        n = len(self.selected_ids())
        self.selection_label.configure(text=f"{n} selected" if n else "")
        state = "normal" if (n and not self.busy) else "disabled"
        self.btn_scrape.configure(state=state)
        self.btn_rebuild.configure(state=state)

    def select_all(self):
        for row in self.rows.values():
            if row.visible:
                row.var.set(True)
        self.on_selection_changed()

    def clear_selection(self):
        for row in self.rows.values():
            row.var.set(False)
        self.on_selection_changed()

    # ------------------------------------------------ scraping
    def scrape_selected(self):
        if self.busy:
            return
        selected = self.selected_ids()
        todo = [c for c in selected if not self.lib.is_manual(c)]
        if len(todo) < len(selected):
            self.status_label.configure(
                text="Imported PDFs are skipped: they cannot be scraped."
            )
        if not todo:
            return
        already = [c for c in todo if self.lib.record(c)]
        if already:
            choice = self.ask(
                "Already downloaded",
                f"{len(already)} of the {len(todo)} selected collection(s) were already "
                "downloaded.\n\nScraping them again overwrites their files.",
                [
                    ("Cancel", None, SECONDARY),
                    ("Only new ones", "new", SECONDARY),
                    ("Scrape all", "all", None),
                ],
            )
            if choice is None:
                return
            if choice == "new":
                todo = [c for c in todo if not self.lib.record(c)]
        if not todo:
            return

        by_id = {c["id"]: c for c in self.lib.collections(self.current)}
        author = self.lib.creator(self.current)["name"]
        self.begin_task("Starting...")
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

        def work():
            try:
                for k, cid in enumerate(todo, 1):
                    info = by_id[cid]
                    prefix = f"[{k}/{len(todo)}] {info['title']}"
                    self.say(f"=== {info['title']} ===")

                    def progress(i, n, prefix=prefix):
                        frac = i / n if n else 0
                        self.post(
                            lambda: self.set_progress(
                                frac, f"{prefix}  -  post {i}/{n}"
                            )
                        )

                    try:
                        res = scraper.scrape_collection(
                            info["url"],
                            title=info["title"],
                            author=author,
                            cover_path=self.lib.cover_path(cid),
                            log=self.say,
                            progress=progress,
                        )
                    except Exception as e:
                        self.say(f"ERROR in {info['title']}: {e!r}")
                        continue
                    self.lib.set_record(cid, res)
                    self.post(lambda c=cid: self.refresh_row(c))
                self.say("All done.")
            finally:
                self.post(lambda: self.end_task("Finished"))

        self.run(work)

    def rebuild_selected(self):
        if not self.busy:
            self.rebuild(self.selected_ids())

    def rebuild(self, cids):
        todo = [c for c in cids if self.lib.record(c) and not self.lib.is_manual(c)]
        if not todo:
            self.status_label.configure(
                text="Nothing to rebuild: only downloaded Patreon collections can be rebuilt."
            )
            return
        author = self.lib.creator(self.current)["name"]
        self.begin_task("Rebuilding PDF...", indeterminate=True)

        def work():
            try:
                for cid in todo:
                    rec = self.lib.record(cid)
                    try:
                        pdf = scraper.rebuild_pdf(
                            rec["folder"],
                            rec["title"],
                            author=author,
                            cover_path=self.lib.cover_path(cid),
                            log=self.say,
                        )
                        self.lib.set_pdf(cid, pdf)
                        self.post(lambda c=cid: self.refresh_row(c))
                    except Exception as e:
                        self.say(f"ERROR rebuilding {rec['title']}: {e!r}")
            finally:
                self.post(lambda: self.end_task("Finished"))

        self.run(work)

    # ------------------------------------------------ per-row actions
    def set_cover(self, cid):
        if self.busy:
            self.status_label.configure(
                text="Please wait until the current task has finished."
            )
            return
        path = filedialog.askopenfilename(
            title="Choose cover image",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.bmp *.svg"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        try:
            self.lib.set_cover(cid, path)
        except Exception as e:
            self.tell("Cover not usable", f"The image could not be read:\n{e}")
            return
        self.refresh_row(cid)
        if self.lib.record(cid) and not self.lib.is_manual(cid):
            self.rebuild([cid])  # apply it to the existing PDF right away

    def open_pdf(self, cid):
        p = self.lib.pdf_path(cid)
        if p:
            open_path(p)

    def open_folder(self, cid):
        p = self.lib.folder_path(cid)
        if p:
            open_path(p)

    def rename_collection(self, cid):
        row = self.rows.get(cid)
        if not row:
            return
        real = row.real_title
        current = self.lib.display_title(cid, real)
        new = self.ask_text(
            "Rename",
            f"Display name for:\n{real}\n\nOnly the name shown in this list changes. "
            "Files, PDFs and scraping keep using the original name.",
            initial=current,
            extra=("Use original", "") if current != real else None,
        )
        if new is None:
            return
        self.lib.set_display_title(cid, new, real)
        row.update_title()
        row.refresh()
        self.sort_rows()
        self.apply_filter()

    def add_pdf_book(self):
        if self.busy or not self.current:
            return
        path = filedialog.askopenfilename(
            title="Choose a PDF book",
            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "rb") as fh:
                if fh.read(5) != b"%PDF-":
                    raise ValueError("This does not look like a PDF file.")
        except Exception as e:
            self.tell("Not a PDF", str(e))
            return
        dialog = ctk.CTkInputDialog(text="Title of the book:", title="Add PDF book")
        title = (dialog.get_input() or "").strip()
        if not title:
            return
        try:
            self.lib.add_manual_book(self.current, title, path)
        except Exception as e:
            self.tell("Could not add the book", f"The PDF could not be copied:\n{e}")
            return
        self.build_rows()
        self.status_label.configure(text=f"Added '{title}' (imported PDF).")

    def remove_manual(self, cid):
        if self.busy:
            return
        title = self.lib.record(cid)["title"]
        if self.ask(
            "Remove imported book",
            f"Remove '{title}' from the list?\n\nThe PDF copy stays in the books folder.",
            [("Cancel", False, SECONDARY), ("Remove", True, DANGER)],
        ):
            self.lib.remove_manual_book(self.current, cid)
            self.build_rows()

    # ------------------------------------------------ login
    def update_login_status(self):
        age = self.lib.login_age_days()
        if age is None:
            text, color = "Not logged in", RED
        elif age < 1:
            text, color = "Session saved today", GREEN
        elif age < 14:
            text, color = f"Session saved {int(age)} day(s) ago", GREEN
        else:
            text, color = f"Session is {int(age)} days old", ORANGE
        self.login_label.configure(text=text)
        self.login_dot.configure(fg_color=color)

    def refresh_login(self):
        if self.busy:
            return
        decision = {"ok": False}
        answered = threading.Event()

        def show_popup():  # GUI thread
            Popup(
                self,
                "Refresh login",
                "A browser window has opened.\n\nLog in to Patreon there. When the site "
                'shows you as logged in, click "Save session".',
                [("Cancel", False, SECONDARY), ("Save session", True, None)],
                on_choice=lambda v: (decision.update(ok=v), answered.set()),
            )

        def wait_for_user():  # worker thread
            self.post(show_popup)
            answered.wait()
            return decision["ok"]

        self.begin_task("Waiting for login...", indeterminate=True)

        def work():
            try:
                scraper.refresh_login(wait_for_user, log=self.say)
            except Exception as e:
                self.say(f"ERROR during login refresh: {e!r}")
            finally:
                self.post(self.update_login_status)
                self.post(self.end_task)

        self.run(work)

    # ------------------------------------------------ appearance
    def toggle_appearance(self):
        mode = "dark" if self.dark_switch.get() else "light"
        ctk.set_appearance_mode(mode)
        self.lib.appearance = mode


if __name__ == "__main__":
    App().mainloop()
