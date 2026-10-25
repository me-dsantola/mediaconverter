import os
import re
import threading
import urllib.request
import html
from datetime import datetime
import flet as ft
import yt_dlp


class MobileDownloaderService:
    """
    Motor de download otimizado para Android.
    Não depende de binários externos (FFmpeg) e resolve faixas do Spotify nativamente.
    """

    def __init__(self, progress_cb, status_cb, preview_cb, complete_cb, error_cb):
        self.progress_cb = progress_cb
        self.status_cb = status_cb
        self.preview_cb = preview_cb
        self.complete_cb = complete_cb
        self.error_cb = error_cb
        self._cancel_requested = False

    def get_android_download_dir(self) -> str:
        """Retorna a pasta pública de Downloads do Android (/storage/emulated/0/Download)."""
        android_dir = "/storage/emulated/0/Download"
        if os.path.exists("/storage/emulated/0"):
            try:
                os.makedirs(android_dir, exist_ok=True)
                return android_dir
            except Exception:
                pass
        fallback = os.path.join(os.path.expanduser("~"), "Downloads")
        os.makedirs(fallback, exist_ok=True)
        return fallback

    @staticmethod
    def identify_source(url: str) -> str:
        u = url.lower().strip()
        if "youtube.com" in u or "youtu.be" in u:
            return "youtube"
        elif "instagram.com" in u:
            return "instagram"
        elif "spotify.com" in u:
            return "spotify"
        return "unknown"

    def resolve_spotify_metadata(self, spotify_url: str) -> dict:
        """Extrai título, artista e capa diretamente da página do Spotify."""
        req = urllib.request.Request(
            spotify_url,
            headers={'User-Agent': 'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36'}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            page_html = response.read().decode('utf-8', errors='ignore')

        title_match = re.search(r'<title>(.*?)</title>', page_html, re.IGNORECASE)
        thumb_match = re.search(r'<meta\s+property="og:image"\s+content="(.*?)"', page_html, re.IGNORECASE)

        if not title_match:
            raise ValueError("Não foi possível ler os dados deste link do Spotify.")

        raw_title = html.unescape(title_match.group(1))
        clean_query = re.sub(r'\s*\|\s*Spotify.*$', '', raw_title, flags=re.IGNORECASE)
        clean_query = re.sub(r'-\s*song and lyrics by\s*', ' - ', clean_query, flags=re.IGNORECASE)
        clean_query = re.sub(r'-\s*música e letra de\s*', ' - ', clean_query, flags=re.IGNORECASE)

        thumbnail = thumb_match.group(1) if thumb_match else None
        return {
            "title": clean_query.strip(),
            "uploader": "Spotify Music",
            "thumbnail": thumbnail,
            "search_query": clean_query.replace(" - ", " ").strip()
        }

    def fetch_info_async(self, url: str):
        threading.Thread(target=self._fetch_info_worker, args=(url,), daemon=True).start()

    def _fetch_info_worker(self, url: str):
        try:
            source = self.identify_source(url)
            if source == "unknown":
                self.error_cb("Link não suportado. Use YouTube, Instagram ou Spotify.")
                return

            self.status_cb("Buscando informações da mídia...")
            if source == "spotify":
                meta = self.resolve_spotify_metadata(url)
                self.preview_cb(meta["title"], meta["uploader"], meta["thumbnail"], "spotify")
                self.status_cb("Música do Spotify identificada! Pronto para baixar.")
                return

            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'skip_download': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                title = info.get('title') or "Mídia sem título"
                uploader = info.get('uploader') or source.capitalize()
                thumbnail = info.get('thumbnail')
                self.preview_cb(title, uploader, thumbnail, source)
                self.status_cb("Mídia pronta para download!")
        except Exception as e:
            clean_err = re.sub(r'\x1b\[[0-9;]*m', '', str(e))
            self.error_cb(f"Não foi possível carregar a prévia: {clean_err[:80]}")

    def start_download(self, url: str, fmt: str, quality: str):
        self._cancel_requested = False
        threading.Thread(target=self._download_worker, args=(url, fmt, quality), daemon=True).start()

    def _download_worker(self, url: str, fmt: str, quality: str):
        try:
            source = self.identify_source(url)
            if source == "unknown":
                raise ValueError("Link inválido! Cole um link do YouTube, Instagram ou Spotify.")

            output_dir = self.get_android_download_dir()
            target_url = url
            media_title = "Mídia Baixada"

            if source == "spotify":
                self.status_cb("Extraindo dados do Spotify...")
                self.progress_cb(0.10, "Conectando ao Spotify...")
                meta = self.resolve_spotify_metadata(url)
                media_title = meta["title"]
                self.preview_cb(meta["title"], meta["uploader"], meta["thumbnail"], "spotify")
                target_url = f"ytsearch1:{meta['search_query']} official audio"
                fmt = "AUDIO"

            self.status_cb("Conectando ao servidor de mídia...")
            self.progress_cb(0.20, "Iniciando transferência...")

            # Seletores de formato pré-mesclados que NÃO exigem FFmpeg no Android
            if fmt == "AUDIO":
                ext_label = "M4A (Áudio)"
                if quality == "ECONOMY":
                    format_selector = "worstaudio[ext=m4a]/worstaudio/bestaudio[ext=m4a]/bestaudio"
                else:
                    format_selector = "bestaudio[ext=m4a]/bestaudio/best"
            else:
                ext_label = "MP4 (Vídeo)"
                if quality == "ECONOMY":
                    format_selector = " best[height<=480][ext=mp4]/best[ext=mp4]/best"
                else:
                    format_selector = "best[ext=mp4]/best"

            ydl_opts = {
                'format': format_selector,
                'outtmpl': os.path.join(output_dir, '%(title).75s [%(id)s].%(ext)s'),
                'progress_hooks': [self._hook],
                'quiet': True,
                'no_warnings': True,
                'noplaylist': True,
                'http_headers': {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                }
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=True)
                if info:
                    if 'entries' in info and info['entries']:
                        info = info['entries'][0]
                    media_title = info.get('title') or media_title
                    if source != "spotify":
                        self.preview_cb(
                            media_title,
                            info.get('uploader') or source.capitalize(),
                            info.get('thumbnail'),
                            source
                        )

            self.progress_cb(1.0, "Concluído!")
            self.complete_cb(media_title, ext_label, output_dir, source)

        except Exception as e:
            clean_err = re.sub(r'\x1b\[[0-9;]*m', '', str(e))
            self.error_cb(clean_err)

    def _hook(self, d: dict):
        if d['status'] == 'downloading':
            total = d.get('total_bytes') or d.get('total_bytes_estimate')
            downloaded = d.get('downloaded_bytes', 0)
            speed_raw = d.get('_speed_str', '').strip()
            speed = re.sub(r'\x1b\[[0-9;]*m', '', speed_raw)

            if total and total > 0:
                p = downloaded / total
                mb_down = downloaded / (1024 * 1024)
                mb_total = total / (1024 * 1024)
                detail = f"{mb_down:.1f} MB / {mb_total:.1f} MB ({speed})" if speed else f"{mb_down:.1f} MB / {mb_total:.1f} MB"
                self.progress_cb(p, detail)
                self.status_cb(f"Baixando... {p * 100:.1f}%")
        elif d['status'] == 'finished':
            self.progress_cb(0.96, "Salvando no armazenamento interno...")
            self.status_cb("Finalizando arquivo...")


def main(page: ft.Page):
    page.title = "Media Downloader Pro"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#0F172A"  # Slate 900 moderno
    page.padding = 0
    page.scroll = ft.ScrollMode.ADAPTIVE

    # Estado da UI
    selected_format = "VIDEO"   # "VIDEO" ou "AUDIO"
    selected_quality = "HIGH"   # "HIGH" ou "ECONOMY"

    def show_snack(message: str, is_error: bool = False):
        snack = ft.SnackBar(
            content=ft.Text(message, color="white", weight=ft.FontWeight.W_500),
            bgcolor="#EF4444" if is_error else "#10B981",
            duration=3500,
        )
        page.overlay.append(snack)
        snack.open = True
        page.update()

    # --- CABEÇALHO GRADIENTE ---
    header = ft.Container(
        padding=ft.padding.only(left=20, right=20, top=36, bottom=22),
        gradient=ft.LinearGradient(
            begin=ft.alignment.top_left,
            end=ft.alignment.bottom_right,
            colors=["#1E293B", "#0F172A"],
        ),
        border=ft.border.only(bottom=ft.BorderSide(1, "#334155")),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            controls=[
                ft.Row(
                    spacing=12,
                    controls=[
                        ft.Container(
                            padding=10,
                            bgcolor="#2563EB",
                            border_radius=14,
                            content=ft.Icon(ft.icons.CLOUD_DOWNLOAD_ROUNDED, color="white", size=28),
                        ),
                        ft.Column(
                            spacing=2,
                            controls=[
                                ft.Text("MediaGrab Pro", size=20, weight=ft.FontWeight.BOLD, color="white"),
                                ft.Text("YouTube • Instagram • Spotify", size=12, color="#94A3B8"),
                            ],
                        ),
                    ],
                ),
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=10, vertical=5),
                    bgcolor="#1E293B",
                    border_radius=20,
                    border=ft.border.all(1, "#334155"),
                    content=ft.Text("Android", size=11, color="#38BDF8", weight=ft.FontWeight.W_600),
                ),
            ],
        ),
    )

    # --- BADGE DE DETECÇÃO DE PLATAFORMA ---
    platform_badge_icon = ft.Icon(ft.icons.LINK_ROUNDED, size=15, color="#94A3B8")
    platform_badge_text = ft.Text("Cole um link para identificar", size=12, color="#94A3B8", weight=ft.FontWeight.W_500)
    platform_badge = ft.Container(
        padding=ft.padding.symmetric(horizontal=12, vertical=6),
        bgcolor="#1E293B",
        border_radius=20,
        content=ft.Row(
            spacing=6,
            tight=True,
            controls=[platform_badge_icon, platform_badge_text],
        ),
    )

    def update_platform_badge(url: str):
        src = MobileDownloaderService.identify_source(url)
        if src == "youtube":
            platform_badge.bgcolor = "#3B181E"
            platform_badge_icon.name = ft.icons.PLAY_CIRCLE_FILL_ROUNDED
            platform_badge_icon.color = "#F87171"
            platform_badge_text.value = "YouTube Detectado"
            platform_badge_text.color = "#FCA5A5"
        elif src == "instagram":
            platform_badge.bgcolor = "#3B1D38"
            platform_badge_icon.name = ft.icons.CAMERA_ALT_ROUNDED
            platform_badge_icon.color = "#F472B6"
            platform_badge_text.value = "Instagram Reel / Post Detectado"
            platform_badge_text.color = "#F9A8D4"
        elif src == "spotify":
            platform_badge.bgcolor = "#133225"
            platform_badge_icon.name = ft.icons.AUDIOTRACK_ROUNDED
            platform_badge_icon.color = "#4ADE80"
            platform_badge_text.value = "Spotify Music (Conversão Automática Áudio)"
            platform_badge_text.color = "#86EFAC"
            select_format("AUDIO")
        else:
            platform_badge.bgcolor = "#1E293B"
            platform_badge_icon.name = ft.icons.LINK_ROUNDED
            platform_badge_icon.color = "#94A3B8"
            platform_badge_text.value = "Suporta YouTube, Instagram e Spotify"
            platform_badge_text.color = "#94A3B8"
        page.update()

    # --- CAMPO DE URL E BOTÕES DE AÇÃO RÁPIDA ---
    def on_url_change(e):
        update_platform_badge(url_input.value or "")

    url_input = ft.TextField(
        hint_text="https://...",
        border_radius=12,
        bgcolor="#0F172A",
        border_color="#334155",
        focused_border_color="#3B82F6",
        text_size=14,
        color="white",
        hint_style=ft.TextStyle(color="#64748B"),
        content_padding=ft.padding.symmetric(horizontal=14, vertical=14),
        expand=True,
        on_change=on_url_change,
    )

    def on_paste_click(e):
        try:
            clip = page.get_clipboard()
            if clip:
                url_input.value = clip.strip()
                update_platform_badge(url_input.value)
                service.fetch_info_async(url_input.value)
        except Exception:
            show_snack("Não foi possível ler a área de transferência.", is_error=True)

    def on_clear_click(e):
        url_input.value = ""
        preview_card.visible = False
        progress_card.visible = False
        update_platform_badge("")
        page.update()

    def on_analyze_click(e):
        if not url_input.value or not url_input.value.strip():
            show_snack("Cole um link primeiro para analisar!", is_error=True)
            return
        service.fetch_info_async(url_input.value.strip())

    # --- CARD DE PRÉ-VISUALIZAÇÃO DA MÍDIA ---
    preview_img = ft.Image(
        src="https://via.placeholder.com/120x90.png?text=Media",
        width=88,
        height=68,
        fit=ft.ImageFit.COVER,
        border_radius=10,
    )
    preview_title = ft.Text("Título da Mídia", size=14, weight=ft.FontWeight.BOLD, color="white", max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    preview_sub = ft.Text("Canal / Artista", size=12, color="#94A3B8", max_lines=1)

    preview_card = ft.Container(
        visible=False,
        padding=12,
        bgcolor="#1E293B",
        border_radius=14,
        border=ft.border.all(1, "#334155"),
        content=ft.Row(
            spacing=12,
            controls=[
                preview_img,
                ft.Column(
                    spacing=4,
                    expand=True,
                    controls=[preview_title, preview_sub],
                ),
            ],
        ),
    )

    # --- SELETORES DE FORMATO E QUALIDADE ---
    btn_fmt_video = ft.Container(
        expand=True,
        padding=12,
        bgcolor="#2563EB",
        border_radius=12,
        alignment=ft.alignment.center,
        on_click=lambda _: select_format("VIDEO"),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
            controls=[
                ft.Icon(ft.icons.VIDEOCAM_ROUNDED, color="white", size=18),
                ft.Text("Vídeo (MP4)", color="white", weight=ft.FontWeight.W_600, size=13),
            ],
        ),
    )

    btn_fmt_audio = ft.Container(
        expand=True,
        padding=12,
        bgcolor="#0F172A",
        border_radius=12,
        alignment=ft.alignment.center,
        on_click=lambda _: select_format("AUDIO"),
        content=ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
            controls=[
                ft.Icon(ft.icons.HEADPHONES_ROUNDED, color="#94A3B8", size=18),
                ft.Text("Áudio (M4A)", color="#94A3B8", weight=ft.FontWeight.W_600, size=13),
            ],
        ),
    )

    def select_format(fmt: str):
        nonlocal selected_format
        selected_format = fmt
        if fmt == "VIDEO":
            btn_fmt_video.bgcolor = "#2563EB"
            btn_fmt_video.content.controls[0].color = "white"
            btn_fmt_video.content.controls[1].color = "white"
            btn_fmt_audio.bgcolor = "#0F172A"
            btn_fmt_audio.content.controls[0].color = "#94A3B8"
            btn_fmt_audio.content.controls[1].color = "#94A3B8"
        else:
            btn_fmt_audio.bgcolor = "#2563EB"
            btn_fmt_audio.content.controls[0].color = "white"
            btn_fmt_audio.content.controls[1].color = "white"
            btn_fmt_video.bgcolor = "#0F172A"
            btn_fmt_video.content.controls[0].color = "#94A3B8"
            btn_fmt_video.content.controls[1].color = "#94A3B8"
        page.update()

    quality_dropdown = ft.Dropdown(
        label="Qualidade do Arquivo",
        value="HIGH",
        bgcolor="#0F172A",
        border_color="#334155",
        focused_border_color="#3B82F6",
        border_radius=12,
        text_size=13,
        color="white",
        content_padding=ft.padding.symmetric(horizontal=14, vertical=10),
        options=[
            ft.dropdown.Option("HIGH", "Alta Qualidade (Recomendado)"),
            ft.dropdown.Option("ECONOMY", "Economia de Dados (Arquivo Menor)"),
        ],
    )

    # --- PROGRESSO E STATUS ---
    progress_bar = ft.ProgressBar(value=0, color="#3B82F6", bgcolor="#0F172A", height=10, border_radius=6)
    status_label = ft.Text("Pronto para iniciar.", size=13, weight=ft.FontWeight.W_600, color="white")
    detail_label = ft.Text("", size=11, color="#94A3B8")

    progress_card = ft.Container(
        visible=False,
        padding=16,
        bgcolor="#1E293B",
        border_radius=14,
        border=ft.border.all(1, "#334155"),
        content=ft.Column(
            spacing=8,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[status_label, detail_label],
                ),
                progress_bar,
            ],
        ),
    )

    # --- HISTÓRICO DE DOWNLOADS ---
    history_list = ft.Column(spacing=10)
    empty_history_text = ft.Container(
        padding=20,
        alignment=ft.alignment.center,
        content=ft.Text("Nenhum download realizado nesta sessão.", size=12, color="#64748B"),
    )

    # --- CALLBACKS DO SERVIÇO ---
    def update_progress_ui(val: float, detail: str):
        progress_card.visible = True
        progress_bar.value = max(0.0, min(1.0, val))
        detail_label.value = detail
        page.update()

    def update_status_ui(msg: str):
        progress_card.visible = True
        status_label.value = msg
        status_label.color = "#93C5FD"
        page.update()

    def update_preview_ui(title: str, uploader: str, thumb_url: str | None, source: str):
        preview_title.value = title
        preview_sub.value = f"{uploader} • {source.upper()}"
        if thumb_url:
            preview_img.src = thumb_url
        preview_card.visible = True
        page.update()

    def on_complete_ui(title: str, fmt_label: str, folder: str, source: str):
        download_btn.disabled = False
        download_btn_text.value = "BAIXAR AGORA"
        progress_bar.value = 1.0
        status_label.value = "✔ Download Concluído!"
        status_label.color = "#4ADE80"
        detail_label.value = f"Salvo em: {folder}"

        empty_history_text.visible = False
        now_str = datetime.now().strftime("%H:%M")
        icon_map = {
            "youtube": (ft.icons.PLAY_CIRCLE_FILL_ROUNDED, "#F87171"),
            "instagram": (ft.icons.CAMERA_ALT_ROUNDED, "#F472B6"),
            "spotify": (ft.icons.AUDIOTRACK_ROUNDED, "#4ADE80"),
        }
        ic_name, ic_color = icon_map.get(source, (ft.icons.CHECK_CIRCLE_ROUNDED, "#38BDF8"))

        history_item = ft.Container(
            padding=12,
            bgcolor="#1E293B",
            border_radius=12,
            border=ft.border.all(1, "#334155"),
            content=ft.Row(
                spacing=12,
                controls=[
                    ft.Icon(ic_name, color=ic_color, size=26),
                    ft.Column(
                        spacing=2,
                        expand=True,
                        controls=[
                            ft.Text(title, size=13, weight=ft.FontWeight.W_600, color="white", max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(f"{fmt_label} • Concluído às {now_str}", size=11, color="#94A3B8"),
                        ],
                    ),
                    ft.Icon(ft.icons.FOLDER_DONE_ROUNDED, color="#4ADE80", size=20),
                ],
            ),
        )
        history_list.controls.insert(0, history_item)
        show_snack("Mídia salva na pasta Downloads do celular!")
        page.update()

    def on_error_ui(err_msg: str):
        download_btn.disabled = False
        download_btn_text.value = "BAIXAR AGORA"
        progress_card.visible = True
        progress_bar.value = 0
        status_label.value = f"✖ Erro: {err_msg}"
        status_label.color = "#F87171"
        detail_label.value = ""
        show_snack("Falha no download. Verifique o link.", is_error=True)
        page.update()

    service = MobileDownloaderService(
        progress_cb=update_progress_ui,
        status_cb=update_status_ui,
        preview_cb=update_preview_ui,
        complete_cb=on_complete_ui,
        error_cb=on_error_ui,
    )

    # --- BOTÃO PRINCIPAL DE DOWNLOAD ---
    def on_download_click(e):
        url = (url_input.value or "").strip()
        if not url:
            show_snack("Cole um link válido antes de baixar!", is_error=True)
            return

        download_btn.disabled = True
        download_btn_text.value = "PROCESSANDO..."
        progress_card.visible = True
        progress_bar.value = 0.05
        status_label.value = "Preparando download..."
        status_label.color = "#38BDF8"
        detail_label.value = "Aguarde..."
        page.update()

        service.start_download(url, selected_format, quality_dropdown.value)

    download_btn_text = ft.Text("BAIXAR AGORA", size=15, weight=ft.FontWeight.BOLD, color="white")
    download_btn = ft.ElevatedButton(
        height=54,
        style=ft.ButtonStyle(
            bgcolor={"": "#2563EB", "disabled": "#334155"},
            shape=ft.RoundedRectangleBorder(radius=14),
        ),
        on_click=on_download_click,
        content=ft.Row(
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=10,
            controls=[
                ft.Icon(ft.icons.DOWNLOAD_ROUNDED, color="white", size=22),
                download_btn_text,
            ],
        ),
    )

    # --- MONTAGEM DO CARD PRINCIPAL ---
    main_card = ft.Container(
        padding=18,
        bgcolor="#1E293B",
        border_radius=18,
        border=ft.border.all(1, "#334155"),
        content=ft.Column(
            spacing=14,
            controls=[
                ft.Row(
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    controls=[
                        ft.Text("Link da Mídia", size=13, weight=ft.FontWeight.BOLD, color="#E2E8F0"),
                        platform_badge,
                    ],
                ),
                ft.Row(
                    spacing=8,
                    controls=[
                        url_input,
                        ft.IconButton(
                            icon=ft.icons.CONTENT_PASTE_ROUNDED,
                            bgcolor="#334155",
                            icon_color="white",
                            tooltip="Colar e Analisar",
                            on_click=on_paste_click,
                        ),
                        ft.IconButton(
                            icon=ft.icons.SEARCH_ROUNDED,
                            bgcolor="#2563EB",
                            icon_color="white",
                            tooltip="Carregar Prévia",
                            on_click=on_analyze_click,
                        ),
                        ft.IconButton(
                            icon=ft.icons.CLEAR_ROUNDED,
                            bgcolor="#0F172A",
                            icon_color="#94A3B8",
                            tooltip="Limpar",
                            on_click=on_clear_click,
                        ),
                    ],
                ),
                ft.Text("Formato de Saída", size=13, weight=ft.FontWeight.BOLD, color="#E2E8F0"),
                ft.Row(spacing=10, controls=[btn_fmt_video, btn_fmt_audio]),
                quality_dropdown,
                ft.Container(height=4),
                download_btn,
            ],
        ),
    )

    # --- LAYOUT RESPONSIVO NA PÁGINA ---
    body = ft.Container(
        padding=16,
        content=ft.Column(
            spacing=16,
            controls=[
                main_card,
                preview_card,
                progress_card,
                ft.Container(
                    padding=ft.padding.only(top=8),
                    content=ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Text("Downloads Recentes", size=14, weight=ft.FontWeight.BOLD, color="#E2E8F0"),
                            ft.Text("Pasta: /Download", size=11, color="#64748B"),
                        ],
                    ),
                ),
                empty_history_text,
                history_list,
            ],
        ),
    )

    page.add(header, body)


if __name__ == "__main__":
    ft.app(target=main)