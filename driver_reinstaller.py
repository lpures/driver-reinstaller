# -*- coding: utf-8 -*-
"""
Driver Reinstaller — утилита для переустановки/обновления драйверов GPU/iGPU.
• Автоматически обнаруживает видеоадаптеры через WMI
• Предлагает скачать devcon.exe из официального WDK (Microsoft CDN)
• Открывает страницу последних драйверов от производителя (NVIDIA / AMD / Intel)
• Точка восстановления Windows в 1 клик
• Бэкап драйверов через pnputil /export-driver
• Перезапуск видеоподсистемы (Win + Ctrl + Shift + B)
• Очистка шейдерного кэша (NVIDIA / AMD / DirectX)
• Требует прав администратора для работы с драйверами
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
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

            device_id   = gpu.PNPDeviceID or ""
            status      = gpu.Status or "Unknown"
            driver_ver  = gpu.DriverVersion or "N/A"
            driver_date = str(gpu.DriverDate or "N/A")[:10]

            # Дополнительные свойства дисплея
            res_h = gpu.CurrentHorizontalResolution
            res_v = gpu.CurrentVerticalResolution
            refresh = gpu.CurrentRefreshRate
            if res_h and res_v:
                resolution = f"{res_h}x{res_v}"
                if refresh:
                    resolution += f" @ {refresh} Гц"
            else:
                resolution = "Не активен"

            # Память адаптера
            raw_ram = gpu.AdapterRAM
            if raw_ram:
                try:
                    ram_mb = int(raw_ram) // (1024 * 1024)
                    if ram_mb >= 1024:
                        ram_str = f"{ram_mb / 1024:.1f} ГБ"
                    else:
                        ram_str = f"{ram_mb} МБ"
                except Exception:
                    ram_str = "N/A"
            else:
                ram_str = "Динамическая"

            name_low = name.lower()
            if "nvidia" in name_low:
                vendor = "nvidia"
                kind   = "dGPU (дискретная NVIDIA)"
            elif "amd" in name_low or "radeon" in name_low:
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
                "resolution":  resolution,
                "ram":         ram_str,
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
            "resolution":  "—",
            "ram":         "—",
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
            return path
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def download_devcon(log_cb, progress_cb=None) -> str | None:
    """Загружает devcon.exe с официального Microsoft CDN через список источников."""
    arch = "X64" if platform.machine().endswith("64") else "X86"
    log_cb(f"🌐 Получение списка источников WDK (архитектура: {arch})...")

    try:
        r = requests.get(DEVCON_SOURCES_URL, timeout=15)
        r.raise_for_status()
        sources_list = r.json()
    except Exception as e:
        log_cb(f"❌ Не удалось получить список источников: {e}")
        return None

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

    log_cb("🔐 Проверка контрольной суммы SHA-256...")
    actual_sha = _sha256(cab_data)
    if actual_sha != expected_sha:
        log_cb(f"❌ Контрольная сумма не совпадает!")
        log_cb(f"   Ожидалось: {expected_sha}")
        log_cb(f"   Получено:  {actual_sha}")
        return None
    log_cb("   ✔ Контрольная сумма верна.")

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


def open_driver_page(vendor: str):
    url = DRIVER_PAGES.get(vendor, "https://google.com/search?q=gpu+driver+update")
    webbrowser.open(url)


# ══════════════════════════════════════════════════════════════════
#  Новые системные инструменты: Точка восстановления, Бэкап,
#  Сброс видеодрайвера и Очистка шейдеров
# ══════════════════════════════════════════════════════════════════

def create_restore_point(log_cb) -> bool:
    """Создает контрольную точку восстановления Windows перед работой с драйверами."""
    log_cb("\n🛡️ Создание точки восстановления Windows...")
    ps_cmd = (
        "try { "
        "  Enable-ComputerRestore -Drive 'C:\\' -ErrorAction SilentlyContinue; "
        "  Checkpoint-Computer -Description 'Driver Reinstaller Safety Backup' "
        "  -RestorePointType 'DEVICE_DRIVER_INSTALL' -ErrorAction Stop; "
        "  Write-Host 'SUCCESS' "
        "} catch { "
        "  Write-Host ('ERROR: ' + $_.Exception.Message) "
        "}"
    )
    code, out = _run_cmd(["powershell", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd], log_cb, timeout=120)
    if "SUCCESS" in out:
        log_cb("✅ Точка восстановления успешно создана!")
        return True
    else:
        log_cb("⚠️ Не удалось создать точку восстановления (возможно, отключена защита системы).")
        return False


def export_driver_backup(target_dir: str, log_cb) -> bool:
    """Экспортирует все установленные OEM-драйверы в указанную папку."""
    os.makedirs(target_dir, exist_ok=True)
    log_cb(f"\n💾 Резервное копирование драйверов в: {target_dir}")
    code, out = _run_cmd(["pnputil", "/export-driver", "*", target_dir], log_cb, timeout=180)
    if code == 0 or "успешно" in out.lower() or "exported" in out.lower():
        log_cb(f"✅ Бэкап драйверов успешно сохранен в: {target_dir}")
        return True
    else:
        log_cb("⚠️ Бэкап завершился с предупреждениями.")
        return False


def restart_gpu_driver(log_cb):
    """
    Эмулирует нажатие Win + Ctrl + Shift + B (перезапуск видеодрайвера Windows).
    Помогает при зависании картинки, черном экране или артефактах.
    """
    log_cb("\n⚡ Перезапуск графической подсистемы (Win + Ctrl + Shift + B)...")
    VK_LWIN    = 0x5B
    VK_CONTROL = 0x11
    VK_SHIFT   = 0x10
    VK_B       = 0x42
    KEYEVENTF_KEYUP = 0x0002

    user32 = ctypes.windll.user32
    try:
        # Нажатие клавиш
        user32.keybd_event(VK_LWIN, 0, 0, 0)
        user32.keybd_event(VK_CONTROL, 0, 0, 0)
        user32.keybd_event(VK_SHIFT, 0, 0, 0)
        user32.keybd_event(VK_B, 0, 0, 0)
        # Отпускание клавиш
        user32.keybd_event(VK_B, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)
        user32.keybd_event(VK_LWIN, 0, KEYEVENTF_KEYUP, 0)
        log_cb("✅ Сигнал сброса графического драйвера отправлен (экран моргнет).")
    except Exception as e:
        log_cb(f"❌ Ошибка вызова Win+Ctrl+Shift+B: {e}")


def clean_shader_caches(log_cb) -> tuple[int, int]:
    """
    Очищает кэши шейдеров DirectX, NVIDIA, AMD и Intel.
    Устраняет микрофризы и статтеры в играх после смены драйвера.
    Возвращает (кол-во удаленных файлов, освобождено байт).
    """
    log_cb("\n🧹 Очистка кэша шейдеров GPU (DirectX / Vulkan / Driver Cache)...")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    app_data = os.environ.get("APPDATA", "")

    cache_dirs = [
        os.path.join(local_app_data, "NVIDIA", "DXCache"),
        os.path.join(local_app_data, "NVIDIA", "GLCache"),
        os.path.join(app_data, "NVIDIA", "ComputeCache"),
        os.path.join(local_app_data, "AMD", "DxCache"),
        os.path.join(local_app_data, "AMD", "GLCache"),
        os.path.join(local_app_data, "D3DSCache"),
        os.path.join(local_app_data, "Intel", "ShaderCache"),
    ]

    total_files = 0
    total_bytes = 0

    for d in cache_dirs:
        if not os.path.exists(d):
            continue
        try:
            for root, dirs, files in os.walk(d):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        sz = os.path.getsize(fp)
                        os.remove(fp)
                        total_files += 1
                        total_bytes += sz
                    except Exception:
                        pass
        except Exception:
            pass

    mb_freed = total_bytes / (1024 * 1024)
    log_cb(f"✅ Очистка завершена: удалено {total_files} файлов кэша ({mb_freed:.2f} МБ освобождено).")
    return total_files, total_bytes


# ══════════════════════════════════════════════════════════════════
#  Современный стильный графический интерфейс (Fluent Dark UI)
# ══════════════════════════════════════════════════════════════════

class App(tk.Tk):
    # Премиальная палитра глубокой темной темы
    BG_MAIN       = "#0D1117"  # Главный фон (GitHub/Modern Dark)
    BG_HEADER     = "#161B22"  # Верхняя панель
    BG_SIDEBAR    = "#161B22"  # Боковая панель
    BG_CARD       = "#21262D"  # Карточка
    BG_CARD_HOVER = "#30363D"  # Ховер карточки
    BG_CARD_SEL   = "#1F293D"  # Выбранная карточка
    BG_TERMINAL   = "#090D12"  # Фон консоли

    BORDER        = "#30363D"  # Граница разделителей
    BORDER_ACTIVE = "#58A6FF"  # Активная рамка

    # Акценты
    PRIMARY       = "#6366F1"  # Индиго / Фиолетовый
    PRIMARY_HOVER = "#4F46E5"
    SUCCESS       = "#238636"  # Зеленый
    DANGER        = "#DA3633"  # Красный
    WARNING       = "#D29922"  # Оранжевый / Янтарный
    CYAN          = "#38BDF8"  # Голубой

    # Цвета текста
    TEXT_MAIN     = "#F0F6FC"  # Яркий белый
    TEXT_MUTED    = "#8B949E"  # Серый второстепенный

    # Шрифты
    FONT_TITLE = ("Segoe UI", 13, "bold")
    FONT_SUB   = ("Segoe UI", 9)
    FONT_REG   = ("Segoe UI", 10)
    FONT_BOLD  = ("Segoe UI", 10, "bold")
    FONT_CARD  = ("Segoe UI", 10, "bold")
    FONT_SM    = ("Segoe UI", 8)
    FONT_CODE  = ("Cascadia Code", 9)

    def __init__(self):
        super().__init__()
        hide_console()
        self.title("Driver Reinstaller — GPU & iGPU Maintenance")
        self.configure(bg=self.BG_MAIN)
        self.resizable(True, True)
        self.minsize(920, 680)

        self._devices: list[dict] = []
        self._sel: int | None = None
        self._busy = False
        self._devcon_path: str | None = find_devcon()
        self._card_frames: list[tk.Frame] = []

        self._build_ui()
        self._refresh_devices()

        self.update_idletasks()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = 980, 720
        self.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")

    # ── Построение интерфейса ────────────────────────────────────

    def _build_ui(self):
        # ── 1. Верхний бар (Header) ──────────────────────────────
        header = tk.Frame(self, bg=self.BG_HEADER, height=64)
        header.pack(fill="x", side="top")
        header.pack_propagate(False)

        title_box = tk.Frame(header, bg=self.BG_HEADER)
        title_box.pack(side="left", padx=20, pady=10)

        logo_lbl = tk.Label(
            title_box, text="⚡", bg=self.BG_HEADER, fg="#FBBF24",
            font=("Segoe UI", 18, "bold")
        )
        logo_lbl.pack(side="left", padx=(0, 10))

        text_sub_box = tk.Frame(title_box, bg=self.BG_HEADER)
        text_sub_box.pack(side="left")

        tk.Label(
            text_sub_box, text="DRIVER REINSTALLER",
            bg=self.BG_HEADER, fg=self.TEXT_MAIN, font=self.FONT_TITLE
        ).pack(anchor="w")

        tk.Label(
            text_sub_box, text="Быстрая диагностика, переустановка драйверов и обслуживание GPU",
            bg=self.BG_HEADER, fg=self.TEXT_MUTED, font=self.FONT_SM
        ).pack(anchor="w")

        # Правый блок шапки — статус прав и devcon
        badges_box = tk.Frame(header, bg=self.BG_HEADER)
        badges_box.pack(side="right", padx=20, pady=12)

        is_adm = is_admin()
        adm_bg = "#1B4729" if is_adm else "#5C1D24"
        adm_fg = "#7EE787" if is_adm else "#FFA198"
        adm_icon = "🛡️ " if is_adm else "⚠️ "
        adm_text = f"{adm_icon}Администратор" if is_adm else f"{adm_icon}Без прав админа"

        adm_badge = tk.Label(
            badges_box, text=adm_text, bg=adm_bg, fg=adm_fg,
            font=("Segoe UI", 9, "bold"), padx=10, pady=4, relief="flat"
        )
        adm_badge.pack(side="right", padx=(8, 0))

        self._devcon_badge = tk.Label(
            badges_box, text=self._devcon_status_badge_text(),
            bg="#16293D" if self._devcon_path else "#3D2B16",
            fg=self.CYAN if self._devcon_path else self.WARNING,
            font=("Segoe UI", 9, "bold"), padx=10, pady=4,
            relief="flat", cursor="hand2"
        )
        self._devcon_badge.pack(side="right")
        self._devcon_badge.bind("<Button-1>", lambda _: self._prompt_devcon_download())

        tk.Frame(self, bg=self.BORDER, height=1).pack(fill="x", side="top")

        # ── 2. Основная рабочая область ──────────────────────────
        main_panes = tk.Frame(self, bg=self.BG_MAIN)
        main_panes.pack(fill="both", expand=True, padx=16, pady=14)

        # ── 2.1 Левая колонка — Список видеоадаптеров ─────────────
        left_col = tk.Frame(main_panes, bg=self.BG_SIDEBAR, width=320,
                            highlightbackground=self.BORDER, highlightthickness=1)
        left_col.pack(side="left", fill="y", padx=(0, 14))
        left_col.pack_propagate(False)

        side_head = tk.Frame(left_col, bg=self.BG_SIDEBAR)
        side_head.pack(fill="x", padx=14, pady=(12, 6))

        tk.Label(
            side_head, text="ОБНАРУЖЕННЫЕ АДАПТЕРЫ",
            bg=self.BG_SIDEBAR, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")
        ).pack(side="left")

        self._btn_refresh = tk.Button(
            side_head, text="↻ Обновить", command=self._refresh_devices,
            bg=self.BG_CARD, fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            cursor="hand2", padx=8, pady=2, bd=0
        )
        self._btn_refresh.pack(side="right")

        card_canvas_container = tk.Frame(left_col, bg=self.BG_SIDEBAR)
        card_canvas_container.pack(fill="both", expand=True, padx=8, pady=(4, 8))

        self._canvas = tk.Canvas(card_canvas_container, bg=self.BG_SIDEBAR,
                                  bd=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(card_canvas_container, orient="vertical",
                                  command=self._canvas.yview)

        self._cards_inner = tk.Frame(self._canvas, bg=self.BG_SIDEBAR)
        self._cards_inner.bind(
            "<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        )

        self._canvas_win = self._canvas.create_window(
            (0, 0), window=self._cards_inner, anchor="nw", width=290
        )
        self._canvas.configure(yscrollcommand=scrollbar.set)

        self._canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # ── 2.2 Правая область — Детали, действия, консоль ───────
        right_col = tk.Frame(main_panes, bg=self.BG_MAIN)
        right_col.pack(side="left", fill="both", expand=True)

        # Карточка выбранного адаптера
        self._card = tk.Frame(right_col, bg=self.BG_CARD,
                              highlightbackground=self.BORDER, highlightthickness=1)
        self._card.pack(fill="x", pady=(0, 10))

        card_padding = tk.Frame(self._card, bg=self.BG_CARD)
        card_padding.pack(fill="x", padx=16, pady=12)

        name_row = tk.Frame(card_padding, bg=self.BG_CARD)
        name_row.pack(fill="x", pady=(0, 2))

        self._lbl_icon = tk.Label(
            name_row, text="🖥️", bg=self.BG_CARD, fg=self.TEXT_MAIN,
            font=("Segoe UI", 16)
        )
        self._lbl_icon.pack(side="left", padx=(0, 8))

        self._lbl_name = tk.Label(
            name_row, text="Выберите видеоадаптер из списка слева",
            bg=self.BG_CARD, fg=self.TEXT_MAIN, font=self.FONT_TITLE,
            wraplength=520, justify="left"
        )
        self._lbl_name.pack(side="left", fill="x", expand=True)

        self._lbl_kind_tag = tk.Label(
            card_padding, text="", bg=self.BG_CARD, fg=self.CYAN,
            font=self.FONT_BOLD
        )
        self._lbl_kind_tag.pack(anchor="w", pady=(0, 8))

        # Сетка параметров: 4 виджета-плитки
        self._info_grid = tk.Frame(card_padding, bg=self.BG_CARD)
        self._info_grid.pack(fill="x", pady=(0, 6))

        self._tile_ver = self._create_tile(self._info_grid, "ВЕРСИЯ ДРАЙВЕРА", "—")
        self._tile_ver.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self._tile_date = self._create_tile(self._info_grid, "ДАТА ВЫПУСКА", "—")
        self._tile_date.pack(side="left", fill="x", expand=True, padx=2)

        self._tile_res = self._create_tile(self._info_grid, "РАЗРЕШЕНИЕ И ГЦ", "—")
        self._tile_res.pack(side="left", fill="x", expand=True, padx=2)

        self._tile_stat = self._create_tile(self._info_grid, "СТАТУС WINDOWS", "—")
        self._tile_stat.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # Строка с PNP Hardware ID
        self._pnp_box = tk.Frame(card_padding, bg="#161B22", padx=8, pady=4)
        self._pnp_box.pack(fill="x", pady=(6, 0))

        tk.Label(
            self._pnp_box, text="PNP ID:", bg="#161B22", fg=self.TEXT_MUTED,
            font=self.FONT_SM
        ).pack(side="left")

        self._lbl_pnp = tk.Label(
            self._pnp_box, text="—", bg="#161B22", fg="#A5D6FF",
            font=self.FONT_CODE, wraplength=460, justify="left"
        )
        self._lbl_pnp.pack(side="left", padx=6, fill="x", expand=True)

        self._btn_copy_pnp = tk.Button(
            self._pnp_box, text="📋 Копировать", command=self._copy_pnp,
            bg="#21262D", fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=2
        )
        self._btn_copy_pnp.pack(side="right")

        # ── Панель основных действий (Action Buttons) ────────────
        btn_bar = tk.Frame(right_col, bg=self.BG_MAIN)
        btn_bar.pack(fill="x", pady=(0, 8))

        self._btn_reinstall = tk.Button(
            btn_bar, text="⚡  Переустановить драйвер", command=self._start_reinstall,
            bg=self.PRIMARY, fg="white", activebackground=self.PRIMARY_HOVER,
            activeforeground="white", font=("Segoe UI", 10, "bold"),
            relief="flat", cursor="hand2", pady=8, bd=0
        )
        self._btn_reinstall.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._btn_reinstall.config(state="disabled")

        self._btn_driver_page = tk.Button(
            btn_bar, text="🌐  Новый драйвер", command=self._open_driver_page,
            bg=self.BG_CARD, fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_BOLD,
            relief="flat", cursor="hand2", pady=8, bd=0
        )
        self._btn_driver_page.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._btn_driver_page.config(state="disabled")

        self._btn_devmgmt = tk.Button(
            btn_bar, text="🛠️  Диспетчер устройств", command=self._open_devmgmt,
            bg=self.BG_CARD, fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_BOLD,
            relief="flat", cursor="hand2", pady=8, bd=0
        )
        self._btn_devmgmt.pack(side="left", fill="x", expand=True)

        # ── 3. Тулбар расширенных инструментов (Инструменты безопасности и очистки) ──
        tools_card = tk.Frame(right_col, bg=self.BG_CARD,
                              highlightbackground=self.BORDER, highlightthickness=1)
        tools_card.pack(fill="x", pady=(0, 8))

        tools_box = tk.Frame(tools_card, bg=self.BG_CARD, padx=10, pady=6)
        tools_box.pack(fill="x")

        tk.Label(
            tools_box, text="СИСТЕМНЫЕ ИНСТРУМЕНТЫ:",
            bg=self.BG_CARD, fg=self.TEXT_MUTED, font=("Segoe UI", 7, "bold")
        ).pack(side="left", padx=(0, 6))

        # Кнопка: Точка восстановления
        btn_restore = tk.Button(
            tools_box, text="🛡️ Точка восстановления", command=self._action_restore_point,
            bg="#161B22", fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=3
        )
        btn_restore.pack(side="left", padx=2)

        # Кнопка: Бэкап драйверов
        btn_backup = tk.Button(
            tools_box, text="💾 Бэкап драйверов", command=self._action_backup_drivers,
            bg="#161B22", fg=self.TEXT_MAIN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=3
        )
        btn_backup.pack(side="left", padx=2)

        # Кнопка: Сброс видео (Win+Ctrl+Shift+B)
        btn_restart_video = tk.Button(
            tools_box, text="⚡ Сброс видео (Win+Ctrl+Shift+B)", command=self._action_restart_video,
            bg="#161B22", fg=self.CYAN, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=3
        )
        btn_restart_video.pack(side="left", padx=2)

        # Кнопка: Очистить шейдеры
        btn_clean_shaders = tk.Button(
            tools_box, text="🧹 Очистить шейдеры", command=self._action_clean_shaders,
            bg="#161B22", fg="#7EE787", activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=3
        )
        btn_clean_shaders.pack(side="left", padx=2)

        # Кнопка: dxdiag
        btn_dxdiag = tk.Button(
            tools_box, text="🔍 dxdiag", command=self._action_dxdiag,
            bg="#161B22", fg=self.TEXT_MUTED, activebackground=self.BG_CARD_HOVER,
            activeforeground="white", font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=3
        )
        btn_dxdiag.pack(side="right")

        # ── Информационный блок devcon ───────────────────────────
        self._devcon_frame = tk.Frame(right_col, bg=self.BG_CARD,
                                      highlightbackground=self.BORDER, highlightthickness=1)
        self._devcon_frame.pack(fill="x", pady=(0, 8))
        self._rebuild_devcon_block()

        self._progress_var = tk.DoubleVar(value=0)
        self._progressbar = ttk.Progressbar(
            right_col, variable=self._progress_var,
            maximum=100, mode="determinate"
        )
        self._progress_label = tk.Label(
            right_col, text="", bg=self.BG_MAIN, fg=self.TEXT_MUTED, font=self.FONT_SM
        )

        # ── Журнал операций (Console Log) ────────────────────────
        log_header = tk.Frame(right_col, bg=self.BG_MAIN)
        log_header.pack(fill="x", pady=(0, 4))

        tk.Label(
            log_header, text="ЖУРНАЛ ОПЕРАЦИЙ",
            bg=self.BG_MAIN, fg=self.TEXT_MUTED, font=("Segoe UI", 8, "bold")
        ).pack(side="left")

        btn_copy_log = tk.Button(
            log_header, text="Копировать лог", command=self._copy_log,
            bg=self.BG_CARD, fg=self.TEXT_MUTED, activebackground=self.BG_CARD_HOVER,
            activeforeground=self.TEXT_MAIN, font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=1
        )
        btn_copy_log.pack(side="right", padx=(4, 0))

        btn_clear_log = tk.Button(
            log_header, text="Очистить", command=self._clear_log,
            bg=self.BG_CARD, fg=self.TEXT_MUTED, activebackground=self.BG_CARD_HOVER,
            activeforeground=self.TEXT_MAIN, font=self.FONT_SM, relief="flat",
            bd=0, cursor="hand2", padx=6, pady=1
        )
        btn_clear_log.pack(side="right")

        log_container = tk.Frame(right_col, bg=self.BG_TERMINAL,
                                 highlightbackground=self.BORDER, highlightthickness=1)
        log_container.pack(fill="both", expand=True)

        self._log = scrolledtext.ScrolledText(
            log_container,
            bg=self.BG_TERMINAL, fg=self.TEXT_MAIN,
            font=self.FONT_CODE, bd=0, relief="flat",
            state="disabled", height=7, insertbackground="white"
        )
        self._log.pack(fill="both", expand=True, padx=6, pady=6)

        self._log.tag_config("ok",    foreground="#7EE787")  # Зеленый
        self._log.tag_config("err",   foreground="#FFA198")  # Красный
        self._log.tag_config("warn",  foreground="#F1E05A")  # Желтый
        self._log.tag_config("cmd",   foreground="#A5D6FF")  # Синий
        self._log.tag_config("gray",  foreground="#8B949E")  # Серый
        self._log.tag_config("blue",  foreground="#79C0FF")  # Голубой

    # ── Вспомогательные методы создания UI ───────────────────────

    def _create_tile(self, parent, title: str, val: str) -> tk.Frame:
        """Создает стильную плитку-параметр."""
        tile = tk.Frame(parent, bg="#161B22", padx=8, pady=6,
                        highlightbackground=self.BORDER, highlightthickness=1)
        tk.Label(
            tile, text=title, bg="#161B22", fg=self.TEXT_MUTED,
            font=("Segoe UI", 7, "bold")
        ).pack(anchor="w")

        lbl_val = tk.Label(
            tile, text=val, bg="#161B22", fg=self.TEXT_MAIN,
            font=("Segoe UI", 10, "bold")
        )
        lbl_val.pack(anchor="w", pady=(2, 0))
        tile._val_lbl = lbl_val
        return tile

    def _rebuild_devcon_block(self):
        for w in self._devcon_frame.winfo_children():
            w.destroy()

        box = tk.Frame(self._devcon_frame, bg=self.BG_CARD, padx=12, pady=6)
        box.pack(fill="x")

        if self._devcon_path:
            tk.Label(
                box, text="✔  devcon.exe активен (глубокая переустановка и удаление доступны)",
                bg=self.BG_CARD, fg="#7EE787", font=self.FONT_SM
            ).pack(side="left")
            tk.Label(
                box, text=self._devcon_path,
                bg=self.BG_CARD, fg=self.TEXT_MUTED, font=self.FONT_SM
            ).pack(side="right")
        else:
            tk.Label(
                box, text="💡 Скачайте devcon.exe для аппаратного сброса шины при переустановке:",
                bg=self.BG_CARD, fg=self.TEXT_MUTED, font=self.FONT_SM
            ).pack(side="left")

            btn_get = tk.Button(
                box, text="⬇ Скачать devcon (~6 MB)", command=self._prompt_devcon_download,
                bg=self.PRIMARY, fg="white", activebackground=self.PRIMARY_HOVER,
                activeforeground="white", font=self.FONT_SM, relief="flat",
                bd=0, cursor="hand2", padx=8, pady=2
            )
            btn_get.pack(side="right")

    def _devcon_status_badge_text(self) -> str:
        if self._devcon_path:
            return "⚙️ devcon: Готов"
        return "⬇ Скачать devcon"

    def _open_devmgmt(self):
        try:
            subprocess.Popen("devmgmt.msc", shell=True)
            self._log_write("🛠️ Открыт Диспетчер устройств Windows.")
        except Exception as e:
            self._log_write(f"❌ Не удалось открыть devmgmt.msc: {e}")

    def _action_dxdiag(self):
        try:
            subprocess.Popen("dxdiag.exe", shell=True)
            self._log_write("🔍 Запущена утилита диагностики DirectX (dxdiag).")
        except Exception as e:
            self._log_write(f"❌ Ошибка запуска dxdiag: {e}")

    def _copy_pnp(self):
        if self._sel is not None and self._devices:
            dev = self._devices[self._sel]
            pnp = dev.get("device_id", "")
            if pnp:
                self.clipboard_clear()
                self.clipboard_append(pnp)
                self._log_write(f"📋 Скопирован Hardware ID: {pnp}")

    def _copy_log(self):
        log_content = self._log.get("1.0", "end-1c")
        if log_content:
            self.clipboard_clear()
            self.clipboard_append(log_content)
            messagebox.showinfo("Скопировано", "Журнал операций скопирован в буфер обмена.")

    def _clear_log(self):
        self._log.config(state="normal")
        self._log.delete("1.0", "end")
        self._log.config(state="disabled")

    def _log_write(self, msg: str):
        def _do():
            self._log.config(state="normal")
            tag = ""
            if any(msg.startswith(p) for p in ("✅", "✔")):
                tag = "ok"
            elif any(msg.startswith(p) for p in ("❌",)):
                tag = "err"
            elif any(msg.startswith(p) for p in ("⚠️", "⚠")):
                tag = "warn"
            elif msg.startswith("▶"):
                tag = "cmd"
            elif msg.startswith("  "):
                tag = "gray"
            elif any(msg.startswith(p) for p in ("🌐", "📥", "📦", "🔐", "📋", "🛠️", "🛡️", "💾", "⚡", "🧹", "🔍")):
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

    # ── Вызов системных инструментов ─────────────────────────────

    def _action_restore_point(self):
        if not is_admin():
            messagebox.showerror(
                "Требуются права администратора",
                "Создание точки восстановления требует прав администратора.\n"
                "Перезапустите утилиту от имени администратора."
            )
            return

        if not messagebox.askyesno(
            "Точка восстановления",
            "Создать контрольную точку восстановления Windows прямо сейчас?\n\n"
            "Это защитит систему на случай любых непредвиденных сбоев при установке драйверов."
        ):
            return

        self._log_write("\n" + "═" * 50)
        def _worker():
            ok = create_restore_point(self._log_write)
            if ok:
                self.after(0, lambda: messagebox.showinfo(
                    "Точка восстановления", "Контрольная точка восстановления Windows успешно создана!"
                ))
            else:
                self.after(0, lambda: messagebox.showwarning(
                    "Внимание", "Не удалось создать точку восстановления. Проверьте журнал."
                ))

        threading.Thread(target=_worker, daemon=True).start()

    def _action_backup_drivers(self):
        default_dir = os.path.join(os.getcwd(), "DriverBackups")
        target_dir = filedialog.askdirectory(
            title="Выберите папку для сохранения бэкапа драйверов",
            initialdir=os.getcwd()
        )
        if not target_dir:
            return

        def _worker():
            export_driver_backup(target_dir, self._log_write)
            self.after(0, lambda: messagebox.showinfo(
                "Бэкап драйверов", f"Резервная копия драйверов сохранена в:\n{target_dir}"
            ))

        threading.Thread(target=_worker, daemon=True).start()

    def _action_restart_video(self):
        restart_gpu_driver(self._log_write)

    def _action_clean_shaders(self):
        if not messagebox.askyesno(
            "Очистка шейдерного кэша",
            "Очистить кэш шейдеров DirectX, NVIDIA, AMD и Intel?\n\n"
            "Это безопасно освободит место на диске и устранит микрофризы в играх после смены драйвера."
        ):
            return

        def _worker():
            files, bytes_cnt = clean_shader_caches(self._log_write)
            mb = bytes_cnt / (1024 * 1024)
            self.after(0, lambda: messagebox.showinfo(
                "Кэш очищен",
                f"Успешно удалено {files} файлов кэша ({mb:.1f} МБ освобождено).\n\n"
                "При следующем запуске игр шейдеры скомпилируются заново без старых ошибок."
            ))

        threading.Thread(target=_worker, daemon=True).start()

    # ── Логика работы с устройствами ─────────────────────────────

    def _refresh_devices(self):
        self._log_write("🔍 Обнаружение видеоадаптеров...")
        for c in self._cards_inner.winfo_children():
            c.destroy()
        self._card_frames.clear()
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
        for idx, d in enumerate(devs):
            card = self._create_device_card(idx, d)
            self._card_frames.append(card)

        if devs:
            self._log_write(f"✅ Обнаружено видеоадаптеров: {len(devs)}")
            for d in devs:
                self._log_write(
                    f"  • {d['name']} | {d['kind']} | drv {d['driver_ver']}"
                )
            self._select_device(0)
        else:
            self._log_write("⚠ Видеоадаптеры не обнаружены.")

    def _create_device_card(self, idx: int, dev: dict) -> tk.Frame:
        """Создает стильную карточку видеоадаптера в боковом списке."""
        vendor = dev.get("vendor", "unknown")
        if vendor == "nvidia":
            badge_text = "NVIDIA"
            badge_bg   = "#1A3319"
            badge_fg   = "#76B900"
            icon_char  = "🟢"
        elif vendor == "amd":
            badge_text = "AMD"
            badge_bg   = "#3B1B1E"
            badge_fg   = "#FF7B72"
            icon_char  = "🔴"
        elif vendor == "intel":
            badge_text = "INTEL"
            badge_bg   = "#162842"
            badge_fg   = "#58A6FF"
            icon_char  = "🔵"
        else:
            badge_text = "GPU"
            badge_bg   = "#21262D"
            badge_fg   = "#8B949E"
            icon_char  = "⚪"

        card = tk.Frame(
            self._cards_inner, bg=self.BG_CARD,
            highlightbackground=self.BORDER, highlightthickness=1,
            cursor="hand2"
        )
        card.pack(fill="x", pady=4, padx=4)

        inner = tk.Frame(card, bg=self.BG_CARD, padx=10, pady=8)
        inner.pack(fill="both", expand=True)

        top_row = tk.Frame(inner, bg=self.BG_CARD)
        top_row.pack(fill="x")

        b_lbl = tk.Label(
            top_row, text=f" {icon_char} {badge_text} ", bg=badge_bg, fg=badge_fg,
            font=("Segoe UI", 7, "bold"), relief="flat"
        )
        b_lbl.pack(side="left")

        kind_short = "Дискретная" if "дискретная" in dev["kind"].lower() else "Встроенная"
        k_lbl = tk.Label(
            top_row, text=kind_short, bg=self.BG_CARD, fg=self.TEXT_MUTED,
            font=self.FONT_SM
        )
        k_lbl.pack(side="right")

        name_lbl = tk.Label(
            inner, text=dev["name"], bg=self.BG_CARD, fg=self.TEXT_MAIN,
            font=self.FONT_CARD, wraplength=250, justify="left"
        )
        name_lbl.pack(anchor="w", pady=(4, 2))

        drv_lbl = tk.Label(
            inner, text=f"Драйвер: {dev['driver_ver']}", bg=self.BG_CARD,
            fg=self.TEXT_MUTED, font=self.FONT_SM
        )
        drv_lbl.pack(anchor="w")

        def _on_click(_=None):
            self._select_device(idx)

        for w in (card, inner, top_row, b_lbl, k_lbl, name_lbl, drv_lbl):
            w.bind("<Button-1>", _on_click)
            w.configure(cursor="hand2")

        return card

    def _select_device(self, idx: int):
        if self._busy:
            return
        self._sel = idx
        for i, f in enumerate(self._card_frames):
            if i == idx:
                f.config(highlightbackground=self.BORDER_ACTIVE, highlightthickness=2)
            else:
                f.config(highlightbackground=self.BORDER, highlightthickness=1)

        dev = self._devices[idx]
        self._update_card(dev)
        self._btn_reinstall.config(state="normal")
        self._btn_driver_page.config(state="normal")

    def _update_card(self, dev: dict | None):
        if dev is None:
            self._lbl_name.config(text="Выберите видеоадаптер из списка слева")
            self._lbl_kind_tag.config(text="")
            self._lbl_icon.config(text="🖥️")
            self._tile_ver._val_lbl.config(text="—")
            self._tile_date._val_lbl.config(text="—")
            self._tile_res._val_lbl.config(text="—")
            self._tile_stat._val_lbl.config(text="—")
            self._lbl_pnp.config(text="—")
            return

        vendor = dev.get("vendor", "unknown")
        if vendor == "nvidia":
            self._lbl_icon.config(text="🟢", fg="#76B900")
        elif vendor == "amd":
            self._lbl_icon.config(text="🔴", fg="#FF7B72")
        elif vendor == "intel":
            self._lbl_icon.config(text="🔵", fg="#58A6FF")
        else:
            self._lbl_icon.config(text="🖥️", fg=self.TEXT_MAIN)

        self._lbl_name.config(text=dev["name"])
        self._lbl_kind_tag.config(text=f"● {dev['kind']} • Память: {dev.get('ram', 'N/A')}")

        self._tile_ver._val_lbl.config(text=dev.get("driver_ver", "N/A"))
        self._tile_date._val_lbl.config(text=dev.get("driver_date", "N/A"))
        self._tile_res._val_lbl.config(text=dev.get("resolution", "N/A"))

        stat = dev.get("status", "OK")
        stat_color = "#7EE787" if stat.lower() == "ok" else "#FFA198"
        self._tile_stat._val_lbl.config(text=f"● {stat}", fg=stat_color)

        self._lbl_pnp.config(text=dev.get("device_id", "N/A"))

    def _open_driver_page(self):
        if self._sel is None:
            return
        dev = self._devices[self._sel]
        url = DRIVER_PAGES.get(dev["vendor"], "https://google.com/search?q=gpu+driver+download")
        self._log_write(f"🌐 Открываем страницу драйверов: {url}")
        webbrowser.open(url)

    # ── Скачивание devcon ─────────────────────────────────────────

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
            "(~6 МБ, официальный CAB-файл WDK) и установлен в:\n\n"
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
            self._devcon_badge.config(
                text=self._devcon_status_badge_text(),
                bg="#16293D", fg=self.CYAN
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

    # ── Переустановка драйвера ────────────────────────────────────

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
            f"Переустановить драйвер для:\n\n"
            f"  • Устройство: {dev['name']}\n"
            f"  • Тип:        {dev['kind']}\n"
            f"  • Версия:     {dev['driver_ver']}\n\n"
            "⚠️ Экран может кратковременно погаснуть (1-3 сек).\n\n"
            "Продолжить?"
        )
        if not confirm:
            return

        self._busy = True
        self._btn_reinstall.config(state="disabled", text="⏳ Выполняется...", bg=self.WARNING)
        self._btn_refresh.config(state="disabled")
        self._btn_driver_page.config(state="disabled")

        self._log_write("\n" + "═" * 50)
        self._log_write(f"🚀 Переустановка драйвера: {dev['name']}")
        self._log_write(f"   PNP ID: {dev['device_id']}")
        self._log_write("═" * 50)

        def _worker():
            ok = reinstall_driver(dev["device_id"], self._log_write)
            self.after(0, lambda: self._on_reinstall_done(ok))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_reinstall_done(self, ok: bool):
        self._busy = False
        self._btn_reinstall.config(
            state="normal", text="⚡  Переустановить драйвер", bg=self.PRIMARY
        )
        self._btn_refresh.config(state="normal")
        self._btn_driver_page.config(state="normal")

        if ok:
            messagebox.showinfo(
                "Готово",
                "Переустановка завершена!\n\n"
                "💡 Рекомендуется перезагрузить компьютер.\n"
                "💡 Для загрузки самых свежих драйверов нажмите «🌐 Новый драйвер»."
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

    app = App()
    app.mainloop()
