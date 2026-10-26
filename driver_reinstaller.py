# -*- coding: utf-8 -*-
"""
Driver Reinstaller — утилита для переустановки/обновления драйверов GPU/iGPU.
• Автоматически обнаруживает видеоадаптеры через WMI
• Предлагает скачать devcon.exe из официального WDK (Microsoft CDN)
• Открывает страницу последних драйверов от производителя (NVIDIA / AMD / Intel)
• Требует прав администратора для работы с драйверами
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import subprocess
import ctypes
import sys
import os
import shutil
import tempfile
import hashlib
import platform
import webbrowser
import wmi
import pythoncom
import requests


# ══════════════════════════════════════════════════════════════════
#  Константы
# ══════════════════════════════════════════════════════════════════

# devcon sources.json (поддерживается сообществом, но кабинеты — официальный Microsoft CDN)
DEVCON_SOURCES_URL = (
    "https://raw.githubusercontent.com/Drawbackz/DevCon-Installer/"
    "master/devcon_sources.json"
)
DEVCON_DEST = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "devcon.exe")

IGNORED_KEYWORDS = ["Microsoft", "Basic Display", "RDP", "Virtual", "Remote"]

# Страницы загрузки драйверов по производителям
DRIVER_PAGES = {
    "nvidia": "https://www.nvidia.com/ru-ru/drivers/",
    "amd":    "https://www.amd.com/ru/support/download/drivers.html",
    "intel":  "https://www.intel.com/content/www/us/en/support/detect.html",
}

# NVIDIA API для получения последней версии драйвера
# psid/pfid подбираются по обнаруженному GPU (упрощённая таблица)
NVIDIA_DRIVER_API = (
    "https://www.nvidia.com/Download/processFind.aspx"
    "?psid={psid}&pfid={pfid}&osid=57&lid=1&whql=1&lang=ru&ctk=0"
)


# ══════════════════════════════════════════════════════════════════
#  Права администратора и скрытие консоли
# ══════════════════════════════════════════════════════════════════

def hide_console():
    """Скрывает окно консоли при открытии графического интерфейса."""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # 0 = SW_HIDE
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin():
    # По возможности используем pythonw.exe для запуска без консольного окна
    python_exe = sys.executable
    dirname = os.path.dirname(python_exe)
    pythonw = os.path.join(dirname, "pythonw.exe")
    exe_to_use = pythonw if os.path.exists(pythonw) else python_exe

    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", exe_to_use, f'"{os.path.abspath(__file__)}"', None, 1
    )
    sys.exit()


# ══════════════════════════════════════════════════════════════════
#  Обнаружение устройств
# ══════════════════════════════════════════════════════════════════

def get_gpu_devices() -> list[dict]:
    # CoInitialize нужен при вызове из фонового потока (threading.Thread)
    pythoncom.CoInitialize()
    devices = []
    try:
        c = wmi.WMI()
        for gpu in c.Win32_VideoController():
            name = gpu.Name or "Unknown"
            if any(kw.lower() in name.lower() for kw in IGNORED_KEYWORDS):
                continue

            device_id  = gpu.PNPDeviceID or ""
            status     = gpu.Status or "Unknown"
            driver_ver = gpu.DriverVersion or "N/A"
            driver_date = str(gpu.DriverDate or "N/A")[:10]

            name_low = name.lower()
            if "nvidia" in name_low:
                vendor = "nvidia"
                kind   = "dGPU (дискретная NVIDIA)"
            elif "amd" in name_low or "radeon" in name_low:
                # Встроенная или дискретная AMD
                if any(x in name_low for x in ["vega", "780m", "760m", "680m", "integrated"]):
                    vendor = "amd"
                    kind   = "iGPU (встроенная AMD)"
                else:
                    vendor = "amd"
                    kind   = "dGPU (дискретная AMD)"
            elif "intel" in name_low:
                vendor = "intel"
                kind   = "iGPU (встроенная Intel)"
            else:
                vendor = "unknown"
                kind   = "GPU"

            devices.append({
                "name":        name,
                "kind":        kind,
                "vendor":      vendor,
                "device_id":   device_id,
                "status":      status,
                "driver_ver":  driver_ver,
                "driver_date": driver_date,
            })
    except Exception as e:
        devices.append({
            "name":        f"Ошибка WMI: {e}",
            "kind":        "—",
            "vendor":      "unknown",
            "device_id":   "",
            "status":      "Error",
            "driver_ver":  "N/A",
            "driver_date": "N/A",
        })
    finally:
        pythoncom.CoUninitialize()
    return devices


# ══════════════════════════════════════════════════════════════════
#  devcon.exe — обнаружение и загрузка
# ══════════════════════════════════════════════════════════════════

def find_devcon() -> str | None:
    candidates = [
        DEVCON_DEST,
        r"C:\Program Files (x86)\Windows Kits\10\Tools\x64\devcon.exe",
        r"C:\Program Files (x86)\Windows Kits\10\Tools\x86\devcon.exe",
        "devcon.exe",
    ]
    for path in candidates:
        try:
            res = subprocess.run(
                [path], capture_output=True, timeout=3
            )
            return path          # devcon без аргументов всегда выходит с ненулевым кодом
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def download_devcon(log_cb, progress_cb=None) -> str | None:
    """
    Загружает devcon.exe с официального Microsoft CDN через список источников.
    Возвращает путь к установленному файлу или None при ошибке.
    """
    arch = "X64" if platform.machine().endswith("64") else "X86"
    log_cb(f"🌐 Получение списка источников WDK (архитектура: {arch})...")

    try:
        r = requests.get(DEVCON_SOURCES_URL, timeout=15)
        r.raise_for_status()
        sources_list = r.json()
    except Exception as e:
        log_cb(f"❌ Не удалось получить список источников: {e}")
        return None

    # Берём первый (самый свежий) источник
    entry = sources_list[0]
    log_cb(f"   Версия WDK: {entry['Name']}")

    src = next((s for s in entry["Sources"] if s["Architecture"] == arch), None)
    if not src:
        log_cb(f"❌ Нет источника для архитектуры {arch}")
        return None

    cab_url        = src["Url"]
    expected_sha   = src["Sha256"]
    extract_name   = src["ExtractionName"]

    log_cb(f"📥 Загрузка CAB-файла WDK с Microsoft CDN...")
    log_cb(f"   {cab_url}")

    try:
        resp = requests.get(cab_url, timeout=120, stream=True)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        cab_data = bytearray()
        for chunk in resp.iter_content(65536):
            cab_data += chunk
            downloaded += len(chunk)
            if progress_cb and total:
                progress_cb(downloaded / total * 100)
        cab_data = bytes(cab_data)
    except Exception as e:
        log_cb(f"❌ Ошибка загрузки: {e}")
        return None

    # Проверка SHA-256
    log_cb("🔐 Проверка контрольной суммы SHA-256...")
    actual_sha = _sha256(cab_data)
    if actual_sha != expected_sha:
        log_cb(f"❌ Контрольная сумма не совпадает!")
        log_cb(f"   Ожидалось: {expected_sha}")
        log_cb(f"   Получено:  {actual_sha}")
        return None
    log_cb("   ✔ Контрольная сумма верна.")

    # Распаковка CAB
    tmp_dir = tempfile.mkdtemp(prefix="devcon_")
    cab_path = os.path.join(tmp_dir, "devcon.cab")
    try:
        with open(cab_path, "wb") as f:
            f.write(cab_data)

        log_cb("📦 Распаковка CAB-файла...")
        res = subprocess.run(
            ["expand", cab_path, "-F:*", tmp_dir],
            capture_output=True, text=True, timeout=60
        )
        if res.returncode != 0:
            log_cb(f"❌ expand.exe вернул код {res.returncode}: {res.stderr.strip()}")
            return None

        extracted = os.path.join(tmp_dir, extract_name)
        if not os.path.exists(extracted):
            log_cb(f"❌ Файл {extract_name} не найден после распаковки.")
            log_cb(f"   Файлы: {os.listdir(tmp_dir)[:10]}")
            return None

        log_cb(f"📋 Установка devcon.exe → {DEVCON_DEST}")
        shutil.copy2(extracted, DEVCON_DEST)
        log_cb(f"✅ devcon.exe успешно установлен: {DEVCON_DEST}")
        return DEVCON_DEST

    except PermissionError:
        log_cb("❌ Отказано в доступе при копировании в System32/Windows.")
        log_cb("   Убедитесь, что приложение запущено от администратора.")
        return None
    except Exception as e:
        log_cb(f"❌ Ошибка при установке: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
#  Переустановка драйвера
# ══════════════════════════════════════════════════════════════════

def _run_cmd(args: list, log_cb, timeout=90) -> tuple[int, str]:
    try:
        log_cb(f"▶ {' '.join(str(a) for a in args)}")
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="cp866",
            errors="replace",
            timeout=timeout,
        )
        out = (result.stdout + result.stderr).strip()
        for line in out.splitlines():
            if line.strip():
                log_cb(f"  {line}")
        return result.returncode, out
    except subprocess.TimeoutExpired:
        log_cb("⚠ Команда превысила таймаут.")
        return -1, "timeout"
    except Exception as e:
        log_cb(f"⚠ Ошибка запуска: {e}")
        return -1, str(e)


def reinstall_driver(device_id: str, log_cb) -> bool:
    if not device_id:
        log_cb("❌ PNP Device ID не найден — невозможно переустановить.")
        return False

    devcon = find_devcon()
    if devcon:
        log_cb(f"🔧 devcon найден: {devcon}")
        return _reinstall_with_devcon(devcon, device_id, log_cb)
    else:
        log_cb("⚠ devcon.exe не найден. Используем pnputil + PowerShell.")
        return _reinstall_with_pnputil(device_id, log_cb)


def _reinstall_with_devcon(devcon: str, device_id: str, log_cb) -> bool:
    log_cb("\n[1/2] Удаление устройства через devcon...")
    code, _ = _run_cmd([devcon, "remove", f"@{device_id}"], log_cb)

    log_cb("\n[2/2] Пересканирование оборудования (переустановка)...")
    _run_cmd([devcon, "rescan"], log_cb)

    log_cb("\n✅ Переустановка через devcon завершена.")
    return True


def _reinstall_with_pnputil(device_id: str, log_cb) -> bool:
    log_cb("\n[1/3] Отключение устройства (pnputil)...")
    _run_cmd(["pnputil", "/disable-device", device_id], log_cb)

    log_cb("\n[2/3] Включение устройства — Windows переинициализирует драйвер...")
    _run_cmd(["pnputil", "/enable-device", device_id], log_cb)

    log_cb("\n[3/3] PowerShell Disable/Enable-PnpDevice...")
    ps = (
        f"$d = Get-PnpDevice -InstanceId '{device_id}' -EA SilentlyContinue; "
        "if ($d) { Disable-PnpDevice -InputObject $d -Confirm:$false; "
        "Start-Sleep -s 2; Enable-PnpDevice -InputObject $d -Confirm:$false; "
        "Write-Host 'OK' } else { Write-Host 'Device not found' }"
    )
    _run_cmd(["powershell", "-NonInteractive", "-Command", ps], log_cb, timeout=30)
    log_cb("\n✅ Переустановка завершена. Рекомендуется перезагрузить ПК.")
    return True


# ══════════════════════════════════════════════════════════════════
#  Проверка актуальности драйвера (открываем страницу производителя)
# ══════════════════════════════════════════════════════════════════

def open_driver_page(vendor: str):
    url = DRIVER_PAGES.get(vendor, "https://google.com/search?q=driver+update")
    webbrowser.open(url)


# ══════════════════════════════════════════════════════════════════
#  GUI
# ══════════════════════════════════════════════════════════════════

class App(tk.Tk):
    # ─── Цвета ───────────────────────────────────────────────────
    BG      = "#1e1e2e"
    PANEL   = "#2a2a3e"
    ACCENT  = "#7c6fe0"
    ACC_HO  = "#9d8ff0"
    TEXT    = "#cdd6f4"
    SUB     = "#6c7086"
    GREEN   = "#a6e3a1"
    RED     = "#f38ba8"
    YELLOW  = "#f9e2af"
    BLUE    = "#89b4fa"

    FONT    = ("Segoe UI", 10)
    FONTSM  = ("Segoe UI", 9)
    FONTBD  = ("Segoe UI", 10, "bold")
    FONTBIG = ("Segoe UI", 13, "bold")

    def __init__(self):
        super().__init__()
        hide_console()
        self.title("Driver Reinstaller")
        self.configure(bg=self.BG)
        self.resizable(True, True)
        self.minsize(780, 560)

        self._devices: list[dict] = []
        self._sel: int | None = None
        self._busy = False
        self._devcon_path: str | None = find_devcon()

        self._build_ui()
        self._refresh_devices()

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 840, 620
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── Построение интерфейса ────────────────────────────────────

    def _build_ui(self):
        # ── Шапка
        hdr = tk.Frame(self, bg=self.PANEL, pady=12)
        hdr.pack(fill="x")

        tk.Label(
            hdr, text="🖥  Driver Reinstaller",
            bg=self.PANEL, fg=self.TEXT, font=("Segoe UI", 15, "bold")
        ).pack(side="left", padx=20)

        # Статус прав
        adm_color = self.GREEN if is_admin() else self.RED
        adm_text  = "✔ Администратор" if is_admin() else "✘ Без прав администратора"
        tk.Label(
            hdr, text=adm_text, bg=self.PANEL, fg=adm_color, font=self.FONTSM
        ).pack(side="right", padx=20)

        # Статус devcon
        self._lbl_devcon_status = tk.Label(
            hdr, text=self._devcon_status_text(),
            bg=self.PANEL, fg=self.GREEN if self._devcon_path else self.YELLOW,
            font=self.FONTSM, cursor="hand2"
        )
        self._lbl_devcon_status.pack(side="right", padx=(0, 4))
        self._lbl_devcon_status.bind("<Button-1>", lambda _: self._prompt_devcon_download())

        # ── Тело
        body = tk.Frame(self, bg=self.BG)
        body.pack(fill="both", expand=True, padx=14, pady=10)

        # ── Левая панель — список устройств
        left = tk.Frame(body, bg=self.BG)
        left.pack(side="left", fill="y", padx=(0, 12))

        tk.Label(
            left, text="Видеоадаптеры",
            bg=self.BG, fg=self.SUB, font=self.FONTSM
        ).pack(anchor="w", pady=(0, 4))

        lbox_frame = tk.Frame(left, bg=self.PANEL, highlightthickness=1,
                               highlightbackground=self.ACCENT)
        lbox_frame.pack(fill="both", expand=True)

        self._listbox = tk.Listbox(
            lbox_frame,
            bg=self.PANEL, fg=self.TEXT,
            selectbackground=self.ACCENT, selectforeground="white",
            font=self.FONT, width=30, bd=0,
            activestyle="none",
        )
        self._listbox.pack(fill="both", expand=True, padx=3, pady=3)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)

        self._btn_refresh = self._btn(
            left, "↻  Обновить список", self._refresh_devices,
            bg=self.PANEL, width=28
        )
        self._btn_refresh.pack(fill="x", pady=(6, 0))

        # ── Правая панель
        right = tk.Frame(body, bg=self.BG)
        right.pack(side="left", fill="both", expand=True)

        # Карточка устройства
        self._card = tk.Frame(right, bg=self.PANEL, pady=12, padx=14)
        self._card.pack(fill="x", pady=(0, 8))

        self._lbl_name = tk.Label(
            self._card, text="← Выберите устройство",
            bg=self.PANEL, fg=self.TEXT,
            font=self.FONTBIG, wraplength=450, justify="left"
        )
        self._lbl_name.pack(anchor="w")

        self._lbl_kind = tk.Label(
            self._card, text="", bg=self.PANEL, fg=self.ACCENT, font=self.FONT
        )
        self._lbl_kind.pack(anchor="w", pady=(2, 0))

        self._lbl_info = tk.Label(
            self._card, text="", bg=self.PANEL, fg=self.SUB,
            font=self.FONTSM, justify="left"
        )
        self._lbl_info.pack(anchor="w", pady=(4, 0))

        # Кнопки действий
        btn_row = tk.Frame(right, bg=self.BG)
        btn_row.pack(fill="x", pady=(0, 8))

        self._btn_reinstall = self._btn(
            btn_row, "⟳  Переустановить драйвер", self._start_reinstall,
            bg=self.ACCENT, fg="white", font=("Segoe UI", 11, "bold"), pady=9
        )
        self._btn_reinstall.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._btn_reinstall.config(state="disabled")

        self._btn_driver_page = self._btn(
            btn_row, "🌐 Новый драйвер", self._open_driver_page,
            bg=self.PANEL, pady=9
        )
        self._btn_driver_page.pack(side="left", fill="x", expand=True)
        self._btn_driver_page.config(state="disabled")

        # devcon-блок
        self._devcon_frame = tk.Frame(right, bg=self.PANEL, pady=8, padx=12)
        self._devcon_frame.pack(fill="x", pady=(0, 8))
        self._rebuild_devcon_block()

        # Прогресс-бар
        self._progress_var = tk.DoubleVar(value=0)
        self._progressbar = ttk.Progressbar(
            right, variable=self._progress_var,
            maximum=100, mode="determinate", length=400
        )
        self._progress_label = tk.Label(
            right, text="", bg=self.BG, fg=self.SUB, font=self.FONTSM
        )

        # Лог
        tk.Label(
            right, text="Журнал операций",
            bg=self.BG, fg=self.SUB, font=self.FONTSM
        ).pack(anchor="w")

        self._log = scrolledtext.ScrolledText(
            right,
            bg="#12121c", fg=self.TEXT,
            font=("Consolas", 9), bd=0, relief="flat",
            state="disabled", height=10,
        )
        self._log.pack(fill="both", expand=True, pady=(4, 0))
        self._log.tag_config("ok",   foreground=self.GREEN)
        self._log.tag_config("err",  foreground=self.RED)
        self._log.tag_config("warn", foreground=self.YELLOW)
        self._log.tag_config("cmd",  foreground=self.ACCENT)
        self._log.tag_config("gray", foreground=self.SUB)
        self._log.tag_config("blue", foreground=self.BLUE)

    def _rebuild_devcon_block(self):
        for w in self._devcon_frame.winfo_children():
            w.destroy()

        if self._devcon_path:
            tk.Label(
                self._devcon_frame,
                text=f"✔ devcon.exe: {self._devcon_path}",
                bg=self.PANEL, fg=self.GREEN, font=self.FONTSM
            ).pack(side="left")
        else:
            tk.Label(
                self._devcon_frame,
                text="⚠ devcon.exe не найден  —  переустановка будет менее полной.",
                bg=self.PANEL, fg=self.YELLOW, font=self.FONTSM
            ).pack(side="left")
            self._btn_get_devcon = self._btn(
                self._devcon_frame,
                "⬇ Скачать devcon.exe", self._prompt_devcon_download,
                bg=self.ACCENT, fg="white", pady=3
            )
            self._btn_get_devcon.pack(side="right", padx=(10, 0))

    def _devcon_status_text(self) -> str:
        if self._devcon_path:
            return "devcon ✔"
        return "devcon ✘ (нажмите для скачивания)"

    # ── Вспомогательные ─────────────────────────────────────────

    def _btn(self, parent, text, cmd, *,
             bg=None, fg=None, font=None, width=None, pady=5) -> tk.Button:
        kw = dict(
            text=text, command=cmd,
            bg=bg or self.PANEL, fg=fg or self.TEXT,
            activebackground=self.ACC_HO, activeforeground="white",
            font=font or self.FONTBD, relief="flat", cursor="hand2",
            pady=pady, bd=0,
        )
        if width:
            kw["width"] = width
        b = tk.Button(parent, **kw)
        orig_bg = bg or self.PANEL
        orig_fg = fg or self.TEXT
        b.bind("<Enter>", lambda _: b.config(bg=self.ACC_HO, fg="white"))
        b.bind("<Leave>", lambda _: b.config(bg=orig_bg, fg=orig_fg))
        return b

    def _log_write(self, msg: str):
        def _do():
            self._log.config(state="normal")
            tag = ""
            if any(msg.startswith(p) for p in ("✅", "✔")):
                tag = "ok"
            elif any(msg.startswith(p) for p in ("❌",)):
                tag = "err"
            elif any(msg.startswith(p) for p in ("⚠",)):
                tag = "warn"
            elif msg.startswith("▶"):
                tag = "cmd"
            elif msg.startswith("  "):
                tag = "gray"
            elif any(msg.startswith(p) for p in ("🌐", "📥", "📦", "🔐", "📋")):
                tag = "blue"
            self._log.insert("end", msg + "\n", tag)
            self._log.see("end")
            self._log.config(state="disabled")
        self.after(0, _do)

    def _set_progress(self, val: float, label: str = ""):
        def _do():
            self._progress_var.set(val)
            self._progress_label.config(text=label)
            if val > 0:
                self._progressbar.pack(fill="x", pady=(0, 2), before=self._log)
                self._progress_label.pack(anchor="w", before=self._log)
            else:
                self._progressbar.pack_forget()
                self._progress_label.pack_forget()
        self.after(0, _do)

    # ── Логика ───────────────────────────────────────────────────

    def _refresh_devices(self):
        self._log_write("🔍 Обнаружение видеоадаптеров...")
        self._listbox.delete(0, "end")
        self._devices = []
        self._sel = None
        self._btn_reinstall.config(state="disabled")
        self._btn_driver_page.config(state="disabled")
        self._update_card(None)

        def _do():
            devs = get_gpu_devices()
            self.after(0, lambda: self._populate_list(devs))

        threading.Thread(target=_do, daemon=True).start()

    def _populate_list(self, devs: list[dict]):
        self._devices = devs
        for d in devs:
            prefix = "●  "
            self._listbox.insert("end", f"  {prefix}{d['name']}")
        if devs:
            self._log_write(f"✅ Обнаружено адаптеров: {len(devs)}")
            for d in devs:
                self._log_write(
                    f"  • {d['name']} | {d['kind']} | drv {d['driver_ver']}"
                )
        else:
            self._log_write("⚠ Видеоадаптеры не обнаружены.")

    def _on_select(self, _=None):
        sel = self._listbox.curselection()
        if not sel or self._busy:
            return
        self._sel = sel[0]
        dev = self._devices[self._sel]
        self._update_card(dev)
        self._btn_reinstall.config(state="normal")
        self._btn_driver_page.config(state="normal")

    def _update_card(self, dev: dict | None):
        if dev is None:
            self._lbl_name.config(text="← Выберите устройство")
            self._lbl_kind.config(text="")
            self._lbl_info.config(text="")
            return
        self._lbl_name.config(text=dev["name"])
        self._lbl_kind.config(text=dev["kind"])
        self._lbl_info.config(
            text=(
                f"Версия драйвера: {dev['driver_ver']}   "
                f"Дата: {dev['driver_date']}   "
                f"Статус: {dev['status']}\n"
                f"PNP ID: {dev['device_id']}"
            )
        )

    def _open_driver_page(self):
        if self._sel is None:
            return
        dev = self._devices[self._sel]
        url = DRIVER_PAGES.get(dev["vendor"], "https://google.com/search?q=gpu+driver+download")
        self._log_write(f"🌐 Открываем страницу драйверов: {url}")
        webbrowser.open(url)

    # ── devcon скачивание ─────────────────────────────────────────

    def _prompt_devcon_download(self):
        if not is_admin():
            messagebox.showerror(
                "Нет прав",
                "Для установки devcon.exe необходимы права администратора.\n"
                "Перезапустите приложение от имени администратора."
            )
            return

        if self._busy:
            return

        if self._devcon_path:
            if not messagebox.askyesno(
                "devcon.exe",
                f"devcon.exe уже установлен:\n{self._devcon_path}\n\n"
                "Переустановить/обновить?"
            ):
                return

        if not messagebox.askyesno(
            "Скачать devcon.exe",
            "devcon.exe будет загружен с официального Microsoft CDN\n"
            "(~6 МБ, WDK CAB-файл) и установлен в:\n\n"
            f"{DEVCON_DEST}\n\n"
            "Продолжить?"
        ):
            return

        self._busy = True
        self._btn_reinstall.config(state="disabled")
        self._log_write("\n" + "─" * 50)
        self._log_write("⬇ Начало загрузки devcon.exe из Microsoft CDN...")

        def _worker():
            def _prog(pct):
                self._set_progress(pct, f"Загрузка devcon: {pct:.0f}%")

            path = download_devcon(self._log_write, _prog)
            self.after(0, lambda: self._on_devcon_done(path))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_devcon_done(self, path: str | None):
        self._busy = False
        self._set_progress(0)
        if self._sel is not None:
            self._btn_reinstall.config(state="normal")
        if path:
            self._devcon_path = path
            self._lbl_devcon_status.config(
                text=self._devcon_status_text(), fg=self.GREEN
            )
            self._rebuild_devcon_block()
            messagebox.showinfo(
                "devcon.exe установлен",
                f"devcon.exe успешно установлен:\n{path}\n\n"
                "Теперь переустановка драйверов будет более глубокой."
            )
        else:
            messagebox.showerror(
                "Ошибка",
                "Не удалось установить devcon.exe.\nСм. журнал операций."
            )

    # ── Переустановка ─────────────────────────────────────────────

    def _start_reinstall(self):
        if self._busy or self._sel is None:
            return

        if not is_admin():
            messagebox.showerror(
                "Нет прав администратора",
                "Для переустановки драйверов необходимы права администратора.\n"
                "Перезапустите приложение от имени администратора."
            )
            return

        dev = self._devices[self._sel]
        confirm = messagebox.askyesno(
            "Подтверждение переустановки",
            f"Переустановить драйвер:\n\n"
            f"  Устройство: {dev['name']}\n"
            f"  Тип:        {dev['kind']}\n"
            f"  Версия:     {dev['driver_ver']}\n\n"
            "⚠ Экран может кратковременно погаснуть.\n"
            "Продолжить?"
        )
        if not confirm:
            return

        self._busy = True
        self._btn_reinstall.config(state="disabled", text="⏳ Выполняется...")
        self._btn_refresh.config(state="disabled")
        self._btn_driver_page.config(state="disabled")

        self._log_write("\n" + "─" * 50)
        self._log_write(f"🚀 Переустановка: {dev['name']}")
        self._log_write(f"   PNP ID: {dev['device_id']}")
        self._log_write("─" * 50)

        def _worker():
            ok = reinstall_driver(dev["device_id"], self._log_write)
            self.after(0, lambda: self._on_reinstall_done(ok))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_reinstall_done(self, ok: bool):
        self._busy = False
        self._btn_reinstall.config(state="normal", text="⟳  Переустановить драйвер")
        self._btn_refresh.config(state="normal")
        self._btn_driver_page.config(state="normal")

        if ok:
            messagebox.showinfo(
                "Готово",
                "Переустановка завершена.\n\n"
                "💡 Рекомендуется перезагрузить компьютер.\n"
                "💡 Для установки новейших драйверов нажмите «🌐 Новый драйвер»."
            )
        else:
            messagebox.showwarning(
                "Предупреждение",
                "Операция завершена с ошибками.\nСм. журнал операций."
            )


# ══════════════════════════════════════════════════════════════════
#  Точка входа
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    hide_console()
    if not is_admin():
        try:
            root_tmp = tk.Tk()
            root_tmp.withdraw()
            ans = messagebox.askyesno(
                "Требуются права администратора",
                "Для управления драйверами нужны права администратора.\n\n"
                "Перезапустить от имени администратора?",
                icon="warning",
            )
            root_tmp.destroy()
        except Exception:
            ans = True

        if ans:
            run_as_admin()
        # Иначе — запускаем в режиме просмотра (без переустановки)

    app = App()
    app.mainloop()
