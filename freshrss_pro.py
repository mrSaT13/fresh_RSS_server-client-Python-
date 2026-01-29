import os
import sys
import json
import time
import threading
import requests
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from email.utils import parsedate_to_datetime
from io import BytesIO

import customtkinter as ctk
import pyttsx3
import feedparser
from bs4 import BeautifulSoup

# === Опциональные зависимости ===в разработке
#try:
#    from newspaper import Article as NewspaperArticle
#    NEWSPAPER_AVAILABLE = True
#except ImportError:
 #   NEWSPAPER_AVAILABLE = False
#    print("[!] newspaper3k не установлен — полный текст недоступен")

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("[!] Pillow не установлен — изображения не будут отображаться")

try:
    from plyer import notification
    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False
    print("[!] plyer не установлен — уведомления недоступны")

try:
    import pystray
    from pystray import MenuItem as item
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False
    print("[!] pystray не установлен — трей недоступен")

# === Конфигурация ===
APP_NAME = "FreshRSS Pro"
VERSION = "2.0.0.4"

CONFIG_DIR = Path.home() / ".config" / "freshrss_pro"
CONFIG_PATH = CONFIG_DIR / "config.json"
FAVORITES_PATH = CONFIG_DIR / "favorites.json"

DEFAULT_WEATHER_CITY = "Moscow"
DEFAULT_RSS_UPDATE_INTERVAL = 3600  # 1 час


class FreshRSSPro:
    def __init__(self):
        self.version = VERSION
        self.config = self.load_config()
        self.favorites = self.load_favorites()
        self.articles = []
        self.all_articles = []
        self.current_index = -1
        self.auto_advance = False
        self.auto_tts = False
        self.tts_engine = None
        self._init_tts()
        self.weather = "—"
        self.image_label = None
        self.image_cache = {}
        self.status_label = None
        self.last_article_hashes = set()
        self.rss_updater_thread = None
        self.stop_rss_updater = threading.Event()
        self.tray_icon = None
        self.root_hidden = False

        ctk.set_appearance_mode("Dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk()
        self.root.title(f"🎧 {APP_NAME} • v{self.version}")
        self.root.geometry("1100x800")
        self.root.minsize(900, 700)

        # Иконка приложения (если есть .ico рядом)
        icon_path = Path(__file__).with_suffix('.ico')
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception as e:
                print(f"[!] Не удалось загрузить иконку: {e}")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Горячие клавиши
        self.root.bind("<Left>", lambda e: self.prev_article())
        self.root.bind("<Right>", lambda e: self.next_article())
        self.root.bind("<space>", lambda e: self.toggle_auto_advance_switch())
        self.root.bind("<f>", lambda e: self.toggle_favorite())
        self.root.bind("<F>", lambda e: self.toggle_favorite())
        self.root.bind("<s>", lambda e: self.focus_search())
        self.root.bind("<S>", lambda e: self.focus_search())
        self.root.bind("<t>", lambda e: self.toggle_theme())
        self.root.bind("<T>", lambda e: self.toggle_theme())

        if not self.config.get("sources"):
            self.show_settings_window(first_run=True)
        else:
            self.create_main_ui()
            self.load_articles()
            self.start_weather_updater()
            self.start_rss_updater()
            self.update_status_bar()

        # Запуск трей-иконки
        if TRAY_AVAILABLE:
            self.setup_tray()

    def _init_tts(self):
        try:
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', 170)
            self.tts_engine.setProperty('volume', 0.9)
        except Exception as e:
            print(f"[TTS] Не удалось инициализировать: {e}")
            self.tts_engine = None

    def load_config(self):
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
                data.setdefault("weather_city", DEFAULT_WEATHER_CITY)
                data.setdefault("hide_log", False)
                data.setdefault("rss_update_interval", DEFAULT_RSS_UPDATE_INTERVAL)
                data.setdefault("minimize_to_tray", True)
                return data
            except Exception as e:
                print(f"[!] Ошибка загрузки конфига: {e}")
        return {
            "sources": [],
            "weather_city": DEFAULT_WEATHER_CITY,
            "hide_log": False,
            "rss_update_interval": DEFAULT_RSS_UPDATE_INTERVAL,
            "minimize_to_tray": True
        }

    def save_config(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self.config, indent=2, ensure_ascii=False), encoding='utf-8')

    def load_favorites(self):
        if FAVORITES_PATH.exists():
            try:
                return set(json.loads(FAVORITES_PATH.read_text(encoding='utf-8')))
            except:
                return set()
        return set()

    def save_favorites(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        FAVORITES_PATH.write_text(json.dumps(list(self.favorites), indent=2, ensure_ascii=False), encoding='utf-8')

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        full_msg = f"[{timestamp}] {msg}"
        print(full_msg)
        if hasattr(self, 'log_text'):
            self.root.after(0, lambda: self.log_text.configure(state="normal"))
            self.root.after(0, lambda: self.log_text.insert("end", full_msg + "\n"))
            self.root.after(0, lambda: self.log_text.configure(state="disabled"))
            self.root.after(0, lambda: self.log_text.see("end"))

    # ==================== НОВОЕ: ОКНО НАСТРОЕК (С СКРОЛЛОМ) ====================
    def show_settings_window(self, first_run=False):
        settings = ctk.CTkToplevel(self.root)
        settings.title("⚙️ Настройки")
        settings.geometry("700x600")
        settings.minsize(600, 500)
        settings.grab_set()

        # Основной скролл
        main_canvas = ctk.CTkCanvas(settings)
        scrollbar = ctk.CTkScrollbar(settings, orientation="vertical", command=main_canvas.yview)
        scrollable_frame = ctk.CTkFrame(main_canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: main_canvas.configure(scrollregion=main_canvas.bbox("all"))
        )
        main_canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        main_canvas.configure(yscrollcommand=scrollbar.set)

        main_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ---------- Подключение ----------
        conn_frame = ctk.CTkFrame(scrollable_frame)
        conn_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(conn_frame, text="---------- Подключение ----------", font=("Roboto", 12, "bold")).pack(anchor="w", padx=5, pady=(5, 10))
        ctk.CTkLabel(conn_frame, text="Город для погоды:", font=("Roboto", 13)).pack(anchor="w", padx=5)
        self.city_entry = ctk.CTkEntry(conn_frame, placeholder_text="Moscow, Paris, Tokyo")
        self.city_entry.pack(fill="x", padx=5, pady=5)
        self.city_entry.insert(0, self.config.get("weather_city", DEFAULT_WEATHER_CITY))

        # ---------- Источники ----------
        ctk.CTkLabel(scrollable_frame, text="---------- Источники новостей ----------", font=("Roboto", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 5))
        sources_frame = ctk.CTkFrame(scrollable_frame)
        sources_frame.pack(fill="x", padx=10, pady=5)

        entries = []

        def add_row(url="", src_type="rss", user="", token=""):
            row = ctk.CTkFrame(sources_frame)
            row.pack(fill="x", pady=3)

            url_e = ctk.CTkEntry(row, placeholder_text="https://example.com/feed.xml", width=350)
            url_e.pack(side="left", padx=2, fill="x", expand=True)
            if url:
                url_e.insert(0, url)

            type_var = ctk.StringVar(value=src_type)
            ctk.CTkRadioButton(row, text="RSS", variable=type_var, value="rss").pack(side="left", padx=2)
            ctk.CTkRadioButton(row, text="FreshRSS", variable=type_var, value="freshrss").pack(side="left", padx=2)

            user_e = ctk.CTkEntry(row, placeholder_text="user", width=90)
            token_e = ctk.CTkEntry(row, placeholder_text="token", width=90, show="•")

            if src_type == "freshrss":
                user_e.pack(side="left", padx=2)
                token_e.pack(side="left", padx=2)
                if user: user_e.insert(0, user)
                if token: token_e.insert(0, token)

            def toggle_fields(*_):
                if type_var.get() == "freshrss":
                    user_e.pack(side="left", padx=2)
                    token_e.pack(side="left", padx=2)
                else:
                    user_e.pack_forget()
                    token_e.pack_forget()

            type_var.trace_add("write", toggle_fields)
            entries.append((url_e, type_var, user_e, token_e))

        for src in self.config.get("sources", []):
            if src["type"] == "freshrss":
                add_row(src["url"], "freshrss", src["user"], src["token"])
            else:
                add_row(src["url"], "rss")

        ctk.CTkButton(scrollable_frame, text="+ Добавить источник", command=lambda: add_row()).pack(pady=5)

        # ---------- Обновления ----------
        upd_frame = ctk.CTkFrame(scrollable_frame)
        upd_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(upd_frame, text="---------- Обновления ----------", font=("Roboto", 12, "bold")).pack(anchor="w", padx=5, pady=(5, 10))

        interval = self.config.get("rss_update_interval", 3600)
        self.interval_var = ctk.StringVar(value=str(interval))
        ctk.CTkRadioButton(upd_frame, text="Каждые 30 минут", variable=self.interval_var, value="1800").pack(anchor="w", padx=5)
        ctk.CTkRadioButton(upd_frame, text="Каждый час", variable=self.interval_var, value="3600").pack(anchor="w", padx=5)
        ctk.CTkRadioButton(upd_frame, text="Каждые 2 часа", variable=self.interval_var, value="7200").pack(anchor="w", padx=5)

        # ---------- Доп. фишки ----------
        misc_frame = ctk.CTkFrame(scrollable_frame)
        misc_frame.pack(fill="x", padx=10, pady=10)
        ctk.CTkLabel(misc_frame, text="---------- Доп. фишки ----------", font=("Roboto", 12, "bold")).pack(anchor="w", padx=5, pady=(5, 10))

        self.hide_log_var = ctk.BooleanVar(value=self.config.get("hide_log", False))
        ctk.CTkCheckBox(misc_frame, text="Скрыть лог", variable=self.hide_log_var).pack(anchor="w", padx=5)

        self.minimize_to_tray_var = ctk.BooleanVar(value=self.config.get("minimize_to_tray", True))
        ctk.CTkCheckBox(misc_frame, text="Сворачивать в трей при закрытии", variable=self.minimize_to_tray_var).pack(anchor="w", padx=5)

        # Кнопки
        btn_frame = ctk.CTkFrame(scrollable_frame)
        btn_frame.pack(fill="x", padx=10, pady=10)

        def test_connection():
            city = self.city_entry.get().strip() or DEFAULT_WEATHER_CITY
            try:
                r = requests.get(f"https://wttr.in/{city}?format=4", timeout=10)
                if r.status_code == 200:
                    self.log("✅ Погода: соединение успешно")
                    from tkinter import messagebox
                    messagebox.showinfo("Тест", f"Погода для '{city}':\n{r.text.strip()}")
                else:
                    raise Exception(f"HTTP {r.status_code}")
            except Exception as e:
                self.log(f"❌ Погода: ошибка — {e}")
                from tkinter import messagebox
                messagebox.showerror("Тест", f"Не удалось получить погоду:\n{e}")

        def import_config():
            from tkinter import filedialog, messagebox
            path = filedialog.askopenfilename(filetypes=[("JSON", "*.json")])
            if path:
                try:
                    with open(path, encoding='utf-8') as f:
                        new_cfg = json.load(f)
                    self.config = new_cfg
                    self.save_config()
                    self.city_entry.delete(0, "end")
                    self.city_entry.insert(0, self.config.get("weather_city", DEFAULT_WEATHER_CITY))
                    for widget in sources_frame.winfo_children():
                        widget.destroy()
                    entries.clear()
                    for src in self.config.get("sources", []):
                        if src["type"] == "freshrss":
                            add_row(src["url"], "freshrss", src["user"], src["token"])
                        else:
                            add_row(src["url"], "rss")
                    self.log("📥 Конфигурация импортирована")
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось импортировать:\n{e}")

        def export_config():
            from tkinter import filedialog, messagebox
            path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON", "*.json")])
            if path:
                try:
                    with open(path, 'w', encoding='utf-8') as f:
                        json.dump(self.config, f, indent=2, ensure_ascii=False)
                    self.log("📤 Конфигурация экспортирована")
                    messagebox.showinfo("Экспорт", f"Сохранено: {Path(path).name}")
                except Exception as e:
                    messagebox.showerror("Ошибка", f"Не удалось экспортировать:\n{e}")

        ctk.CTkButton(btn_frame, text="🧪 Тест соединения", command=test_connection).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="📥 Импорт", command=import_config).pack(side="left", padx=5)
        ctk.CTkButton(btn_frame, text="📤 Экспорт", command=export_config).pack(side="left", padx=5)

        def save_and_close():
            city = self.city_entry.get().strip() or DEFAULT_WEATHER_CITY
            self.config["weather_city"] = city
            self.config["hide_log"] = bool(self.hide_log_var.get())
            self.config["minimize_to_tray"] = bool(self.minimize_to_tray_var.get())
            self.config["rss_update_interval"] = int(self.interval_var.get())

            sources = []
            for url_e, t_var, u_e, tok_e in entries:
                url = url_e.get().strip()
                if not url:
                    continue
                if t_var.get() == "freshrss":
                    user, token = u_e.get().strip(), tok_e.get().strip()
                    if url and user and token:
                        sources.append({
                            "type": "freshrss",
                            "url": url.rstrip('/'),
                            "user": user,
                            "token": token,
                            "name": urlparse(url).hostname or "FreshRSS"
                        })
                else:
                    sources.append({
                        "type": "rss",
                        "url": url,
                        "name": urlparse(url).hostname or "RSS"
                    })
            self.config["sources"] = sources
            self.save_config()
            settings.destroy()
            if first_run:
                self.create_main_ui()
                self.load_articles()
            else:
                self.load_articles()
                self.restart_rss_updater()

        ctk.CTkButton(scrollable_frame, text="💾 Сохранить и закрыть", command=save_and_close).pack(pady=10)

    # ==================== ОСНОВНОЙ ИНТЕРФЕЙС ====================
    def create_main_ui(self):
        top_frame = ctk.CTkFrame(self.root)
        top_frame.pack(fill="x", padx=10, pady=10)

        self.title_label = ctk.CTkLabel(top_frame, text="Ожидание данных...", font=("Roboto", 18, "bold"))
        self.title_label.pack(side="left")

        ctk.CTkButton(top_frame, text="⚙️ Настройки", command=self.show_settings_window).pack(side="right", padx=5)
        ctk.CTkButton(top_frame, text="🔄 Обновить", command=self.load_articles).pack(side="right", padx=5)
        self.theme_btn = ctk.CTkButton(top_frame, text="🌓 Тема", command=self.toggle_theme)
        self.theme_btn.pack(side="right", padx=5)

        search_frame = ctk.CTkFrame(self.root)
        search_frame.pack(fill="x", padx=10, pady=5)
        self.search_entry = ctk.CTkEntry(search_frame, placeholder_text="Поиск по статьям...")
        self.search_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.search_entry.bind("<Return>", lambda e: self.perform_search())
        ctk.CTkButton(search_frame, text="🔍", width=50, command=self.perform_search).pack(side="right", padx=5)

        ctrl_frame = ctk.CTkFrame(self.root)
        ctrl_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkButton(ctrl_frame, text="◀ Назад", width=100, command=self.prev_article).pack(side="left", padx=5)
        ctk.CTkButton(ctrl_frame, text="Вперёд ▶", width=100, command=self.next_article).pack(side="left", padx=5)
        self.favorite_btn = ctk.CTkButton(ctrl_frame, text="🤍 В избранное", width=120, command=self.toggle_favorite)
        self.favorite_btn.pack(side="left", padx=5)
        ctk.CTkButton(ctrl_frame, text="📤 Экспорт", width=100, command=self.export_article).pack(side="left", padx=5)
        self.auto_tts_switch = ctk.CTkSwitch(ctrl_frame, text="Авто-TTS", command=self.toggle_auto_tts)
        self.auto_tts_switch.pack(side="right", padx=10)
        self.auto_advance_switch = ctk.CTkSwitch(ctrl_frame, text="Авто-лист", command=self.toggle_auto_advance)
        self.auto_advance_switch.pack(side="right", padx=10)

        content_frame = ctk.CTkFrame(self.root)
        content_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.image_label = ctk.CTkLabel(content_frame, text="")
        self.image_label.pack(pady=(0, 10))
        self.content_text = ctk.CTkTextbox(content_frame, wrap="word", font=("Segoe UI", 13))
        self.content_text.pack(fill="both", expand=True)

        # Лог
        self.log_frame = ctk.CTkFrame(self.root, height=100)
        self.log_frame.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(self.log_frame, text="Лог:", font=("Roboto", 10), text_color="gray").pack(anchor="w", padx=5)
        self.log_text = ctk.CTkTextbox(self.log_frame, height=80, font=("Consolas", 10), text_color="lightgray")
        self.log_text.pack(fill="x", padx=5, pady=5)
        self.log_text.configure(state="disabled")

        # Статусная строка
        self.status_label = ctk.CTkLabel(self.root, text="Готово", anchor="w", height=20, text_color="gray")
        self.status_label.pack(side="bottom", fill="x", padx=10, pady=(0, 5))

        self.toggle_log_visibility()

    def toggle_log_visibility(self):
        hide = self.config.get("hide_log", False)
        if hide:
            self.log_frame.pack_forget()
        else:
            self.log_frame.pack(fill="x", padx=10, pady=5)

    def update_status_bar(self):
        now = datetime.now().strftime("%H:%M")
        self.status_label.configure(text=f"Сейчас: {now} | Погода: {self.weather}")
        self.root.after(60000, self.update_status_bar)

    def focus_search(self):
        self.search_entry.focus()

    def toggle_theme(self):
        mode = ctk.get_appearance_mode()
        new_mode = "Light" if mode == "Dark" else "Dark"
        ctk.set_appearance_mode(new_mode)
        self.log(f"🎨 Тема: {new_mode}")

    def toggle_auto_tts(self):
        self.auto_tts = bool(self.auto_tts_switch.get())

    def toggle_auto_advance(self):
        self.auto_advance = bool(self.auto_advance_switch.get())
        if self.auto_advance and self.articles:
            threading.Thread(target=self.auto_advance_loop, daemon=True).start()

    def toggle_auto_advance_switch(self):
        new_state = not self.auto_advance
        if new_state:
            self.auto_advance_switch.select()
        else:
            self.auto_advance_switch.deselect()
        self.toggle_auto_advance()

    def auto_advance_loop(self):
        while self.auto_advance and self.articles:
            time.sleep(30)
            self.root.after(0, self.next_article)

    def toggle_favorite(self):
        if 0 <= self.current_index < len(self.articles):
            art = self.articles[self.current_index]
            key = f"{art.get('link', '')}|{art.get('title', '')}"
            if key in self.favorites:
                self.favorites.discard(key)
                self.favorite_btn.configure(text="🤍 В избранное")
            else:
                self.favorites.add(key)
                self.favorite_btn.configure(text="❤️ В избранном")
            self.save_favorites()

    def export_article(self):
        if not (0 <= self.current_index < len(self.articles)):
            return
        art = self.articles[self.current_index]
        title = art.get("title", "Без названия").replace("/", "_")
        from tkinter import filedialog, messagebox
        path = filedialog.asksaveasfilename(
            initialfile=f"{title}.txt",
            defaultextension=".txt",
            filetypes=[("Text", "*.txt"), ("HTML", "*.html")]
        )
        if path:
            content = self._clean_text(art.get("full_text", art.get("summary", "")))
            try:
                with open(path, "w", encoding="utf-8") as f:
                    if path.endswith(".html"):
                        f.write(f"<h1>{art['title']}</h1><p>{art.get('origin', {}).get('title', '')}</p><hr>{content}")
                    else:
                        f.write(f"{art['title']}\n\n{content}")
                self.log(f"📤 Экспортировано: {Path(path).name}")
            except Exception as e:
                messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{e}")

    def _clean_text(self, html):
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style"]):
                tag.decompose()
            return soup.get_text(separator="\n", strip=True)
        except:
            return str(html)

    def load_articles(self):
        self.log("🔄 Загрузка всех источников...")
        self.all_articles = []
        threading.Thread(target=self._fetch_all_sources, daemon=True).start()

    def _fetch_all_sources(self):
        new_hashes = set()
        for src in self.config["sources"]:
            if src["type"] == "freshrss":
                articles = self._fetch_freshrss_rss(src)
            else:
                articles = self._fetch_generic_rss(src["url"])
            for art in articles:
                h = hash((art.get("title", ""), art.get("link", ""), art.get("published", 0)))
                new_hashes.add(h)
                self.all_articles.append(art)

        # Уведомление о новых статьях
        new_articles = new_hashes - self.last_article_hashes
        if new_articles and PLYER_AVAILABLE and new_hashes:
            notification.notify(
                title="FreshRSS Pro",
                message=f"Новых статей: {len(new_articles)}",
                app_name=APP_NAME,
                timeout=5
            )
        self.last_article_hashes = new_hashes

        self.root.after(0, self._finish_loading)

    def _fetch_freshrss_rss(self, src):
        url = f"{src['url']}/i/?a=rss&user={src['user']}&token={src['token']}&hours=168"
        self.log(f"📡 Запрос FreshRSS: {url}")
        return self._fetch_generic_rss(url, src.get("name", "FreshRSS"))

    def _fetch_generic_rss(self, feed_url, name="RSS"):
        articles = []
        try:
            d = feedparser.parse(feed_url)
            if not d.entries:
                self.log(f"⚠️ Нет статей в {feed_url}")
                return articles
            for entry in d.entries[:25]:
                pub_ts = 0
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    pub_ts = int(time.mktime(entry.published_parsed))
                elif hasattr(entry, 'published') and entry.published:
                    try:
                        dt = parsedate_to_datetime(entry.published)
                        pub_ts = int(dt.timestamp())
                    except:
                        pub_ts = 0

                articles.append({
                    "title": getattr(entry, 'title', 'Без заголовка'),
                    "summary": getattr(entry, 'summary', ''),
                    "content": getattr(entry, 'content', [{}])[0].get('value', ''),
                    "published": pub_ts,
                    "origin": {"title": d.feed.get("title", name)},
                    "link": getattr(entry, 'link', ''),
                    "image_url": self._extract_image(entry)
                })
        except Exception as e:
            self.log(f"💥 Ошибка загрузки {feed_url}: {e}")
        return articles

    def _extract_image(self, entry):
        try:
            if hasattr(entry, 'media_content') and entry.media_content:
                for media in entry.media_content:
                    if media.get('medium') == 'image':
                        return media.get('url')
            if hasattr(entry, 'enclosures'):
                for enc in entry.enclosures:
                    if 'image' in enc.get('type', ''):
                        return enc.href
        except:
            pass
        return ""

    def _finish_loading(self):
        if not self.all_articles:
            self.content_text.delete("0.0", "end")
            self.content_text.insert("0.0", "Нет статей.")
            self.log("⚠️ Ни одна статья не загружена")
            return
        self.all_articles.sort(key=lambda x: x.get("published", 0), reverse=True)
        self.articles = self.all_articles[:]
        self.current_index = 0
        self.show_article(0)
        self.log(f"✅ Загружено {len(self.all_articles)} статей")

    def perform_search(self):
        query = self.search_entry.get().strip().lower()
        if not query:
            self.articles = self.all_articles[:]
        else:
            self.articles = [
                a for a in self.all_articles
                if query in a.get("title", "").lower() or query in a.get("summary", "").lower()
            ]
        self.current_index = 0 if self.articles else -1
        if self.articles:
            self.show_article(0)
        else:
            self.content_text.delete("0.0", "end")
            self.content_text.insert("0.0", "Ничего не найдено.")

    def show_article(self, index):
        if not self.articles or index < 0 or index >= len(self.articles):
            return
        self.current_index = index

        art = self.articles[index]
        title = art.get("title", "Без заголовка")
        pub_time = datetime.fromtimestamp(art.get("published", 0)).strftime("%d %b %Y, %H:%M") if art.get("published") else "—"
        origin = art.get("origin", {}).get("title", "Источник")
        link = art.get("link", "")

        summary = art.get("summary", "")
        full_text = summary
        #if ("читать далее" in summary.lower() or "read more" in summary.lower()) and NEWSPAPER_AVAILABLE and link:
        #    self.log(f"🔍 Загрузка полной статьи: {link}")
        #    full_text = self._fetch_full_article(link) or summary

        display_text = f"{title}\n\n{origin} • {pub_time}\n\n{self._clean_text(full_text)}"
        self.title_label.configure(text=title)
        self.content_text.delete("0.0", "end")
        self.content_text.insert("0.0", display_text)

        img_url = art.get("image_url")
        if img_url and PIL_AVAILABLE:
            self._load_image_async(img_url)
        else:
            self.image_label.configure(image=None, text="")

        key = f"{link}|{title}"
        self.favorite_btn.configure(text="❤️ В избранном" if key in self.favorites else "🤍 В избранное")

        if self.auto_tts and self.tts_engine:
            try:
                self.tts_engine.stop()
            except:
                pass
            threading.Thread(target=lambda: self.speak_text(display_text), daemon=True).start()

    #def _fetch_full_article(self, url):
      #  try:
            #article = NewspaperArticle(url)
           # article.download()
           # article.parse()
          #  return article.text
      #  except Exception as e:
       #     self.log(f"⚠️ Не удалось загрузить полную статью: {e}")
       #     return None

    def _load_image_async(self, url):
        def worker():
            try:
                if url in self.image_cache:
                    img = self.image_cache[url]
                else:
                    response = requests.get(url, timeout=5)
                    img_data = response.content
                    pil_img = Image.open(BytesIO(img_data)).convert("RGBA")
                    max_size = (800, 400)
                    pil_img.thumbnail(max_size, Image.LANCZOS)
                    img = ImageTk.PhotoImage(pil_img)
                    self.image_cache[url] = img
                self.root.after(0, lambda: self.image_label.configure(image=img, text=""))
            except Exception as e:
                self.root.after(0, lambda: self.image_label.configure(image=None, text=""))
                self.log(f"🖼️ Ошибка загрузки изображения: {e}")
        threading.Thread(target=worker, daemon=True).start()

    def speak_text(self, text):
        if self.tts_engine:
            self.tts_engine.say(text)
            self.tts_engine.runAndWait()

    def next_article(self):
        if self.articles and self.current_index < len(self.articles) - 1:
            self.show_article(self.current_index + 1)

    def prev_article(self):
        if self.articles and self.current_index > 0:
            self.show_article(self.current_index - 1)

    def start_weather_updater(self):
        def update():
            while True:
                self._fetch_weather()
                time.sleep(60)
        threading.Thread(target=update, daemon=True).start()

    def _fetch_weather(self):
        city = self.config.get("weather_city", DEFAULT_WEATHER_CITY)
        try:
            r = requests.get(f"https://wttr.in/{city}?format=4", timeout=10)
            if r.status_code == 200:
                self.weather = r.text.strip()
            else:
                self.weather = "—"
        except Exception as e:
            self.log(f"🌦️ Ошибка погоды: {e}")
            self.weather = "—"

    def start_rss_updater(self):
        self.stop_rss_updater.clear()
        interval = self.config.get("rss_update_interval", 3600)
        def loop():
            while not self.stop_rss_updater.wait(interval):
                self.log("🔁 Автообновление RSS...")
                self.load_articles()
        self.rss_updater_thread = threading.Thread(target=loop, daemon=True)
        self.rss_updater_thread.start()

    def restart_rss_updater(self):
        self.stop_rss_updater.set()
        if self.rss_updater_thread:
            self.rss_updater_thread.join(timeout=1)
        self.start_rss_updater()

    def setup_tray(self):
        if not TRAY_AVAILABLE:
            return

        def on_open(icon, item):
            self.root.after(0, self.restore_from_tray)

        def on_exit(icon, item):
            self.stop_rss_updater.set()
            icon.stop()
            self.root.quit()

        def update_title(icon):
            count = len(self.all_articles)
            return f"Статей: {count}"

        menu = (
            pystray.MenuItem(update_title, on_open, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Открыть", on_open),
            pystray.MenuItem("Выход", on_exit)
        )

        # Иконка из файла или стандартная
        icon_image = None
        icon_path = Path(__file__).with_suffix('.ico')
        if icon_path.exists():
            from PIL import Image
            try:
                icon_image = Image.open(icon_path)
            except:
                pass

        if icon_image is None:
            # Создаём простую иконку
            from PIL import Image, ImageDraw
            icon_image = Image.new('RGB', (64, 64), color=(0, 120, 215))
            draw = ImageDraw.Draw(icon_image)
            draw.rectangle((16, 16, 48, 48), fill=(255, 255, 255))

        self.tray_icon = pystray.Icon(APP_NAME, icon_image, APP_NAME, menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def restore_from_tray(self):
        if self.root_hidden:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root_hidden = False

    def minimize_to_tray(self):
        self.root.withdraw()
        self.root_hidden = True

    def on_closing(self):
        if self.config.get("minimize_to_tray", True):
            self.minimize_to_tray()
        else:
            self.stop_rss_updater.set()
            if self.tray_icon:
                self.tray_icon.stop()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


# === Запуск ===
if __name__ == "__main__":
    app = FreshRSSPro()
    app.run()
