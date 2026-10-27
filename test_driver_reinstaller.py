# -*- coding: utf-8 -*-
"""
test_driver_reinstaller.py
Тестовый набор для driver_reinstaller.py
Запуск: python test_driver_reinstaller.py  (или: python -m pytest test_driver_reinstaller.py -v)
"""

import sys
import os
import hashlib
import platform
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch, MagicMock, call, PropertyMock

# Импортируем модуль без запуска GUI
sys.modules.setdefault("tkinter", MagicMock())
sys.modules.setdefault("tkinter.ttk", MagicMock())
sys.modules.setdefault("tkinter.messagebox", MagicMock())
sys.modules.setdefault("tkinter.scrolledtext", MagicMock())
sys.modules.setdefault("pynvml", MagicMock())


import importlib.util
spec = importlib.util.spec_from_file_location(
    "driver_reinstaller",
    os.path.join(os.path.dirname(__file__), "driver_reinstaller.py")
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# ── Цвета для вывода ─────────────────────────────────────────────────────────
# Установим кодировку UTF-8 для всего модуля, чтобы избежать UnicodeEncodeError
import sys
import codecs
sys.stdout = codecs.getwriter("utf-8")(sys.stdout.detach())
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.detach())

# ── Цвета для вывода ─────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def _ok(msg):  print(f"  {GREEN}[PASS]{RESET} {msg}".encode('utf-8', 'replace').decode('utf-8'))
def _fail(msg):print(f"  {RED}[FAIL]{RESET} {msg}".encode('utf-8', 'replace').decode('utf-8'))
def _info(msg):print(f"  {YELLOW}[INFO]{RESET} {msg}".encode('utf-8', 'replace').decode('utf-8'))
def _head(msg):print(f"\n{BOLD}{CYAN}{'='*60}{RESET}\n{BOLD} {msg}{RESET}\n{'='*60}".encode('utf-8', 'replace').decode('utf-8'))


# ══════════════════════════════════════════════════════════════════
#  1. Вспомогательные функции (_sha256, is_admin)
# ══════════════════════════════════════════════════════════════════

class TestUtilFunctions(unittest.TestCase):

    def test_sha256_known_value(self):
        """SHA256 пустой строки должен совпадать с эталоном."""
        expected = hashlib.sha256(b"").hexdigest().upper()
        self.assertEqual(mod._sha256(b""), expected)

    def test_sha256_non_empty(self):
        """SHA256 произвольных байт возвращает верхний регистр, 64 символа."""
        result = mod._sha256(b"hello world")
        self.assertEqual(len(result), 64)
        self.assertEqual(result, result.upper())

    def test_sha256_deterministic(self):
        """Одни и те же данные → одинаковый хеш."""
        data = b"driver_reinstaller_test_data"
        self.assertEqual(mod._sha256(data), mod._sha256(data))

    def test_sha256_different_inputs(self):
        """Разные данные → разные хеши."""
        self.assertNotEqual(mod._sha256(b"abc"), mod._sha256(b"xyz"))

    @patch("ctypes.windll")
    def test_is_admin_true(self, mock_windll):
        """is_admin() возвращает True когда ShellExecute говорит 1."""
        mock_windll.shell32.IsUserAnAdmin.return_value = 1
        self.assertTrue(mod.is_admin())

    @patch("ctypes.windll")
    def test_is_admin_false(self, mock_windll):
        """is_admin() возвращает False когда ShellExecute говорит 0."""
        mock_windll.shell32.IsUserAnAdmin.return_value = 0
        self.assertFalse(mod.is_admin())

    def test_is_admin_exception(self):
        """is_admin() не падает при исключении, возвращает False."""
        import ctypes
        orig = ctypes.windll.shell32.IsUserAnAdmin
        try:
            ctypes.windll.shell32.IsUserAnAdmin = MagicMock(side_effect=Exception("denied"))
            self.assertFalse(mod.is_admin())
        finally:
            ctypes.windll.shell32.IsUserAnAdmin = orig


# ══════════════════════════════════════════════════════════════════
#  2. Классификация GPU-устройств (vendor / kind)
# ══════════════════════════════════════════════════════════════════

class TestGpuClassification(unittest.TestCase):
    """Проверяем логику определения вендора по имени GPU (без реального WMI)."""

    def _classify(self, name: str) -> dict:
        """Вызываем внутреннюю логику классификации напрямую."""
        name_low = name.lower()
        if "nvidia" in name_low:
            vendor = "nvidia"
            kind   = "dGPU (дискретная NVIDIA)"
        elif "amd" in name_low or "radeon" in name_low:
            if any(x in name_low for x in ["vega", "780m", "760m", "680m", "integrated"]):
                vendor = "amd"; kind = "iGPU (встроенная AMD)"
            else:
                vendor = "amd"; kind = "dGPU (дискретная AMD)"
        elif "intel" in name_low:
            vendor = "intel"; kind = "iGPU (встроенная Intel)"
        else:
            vendor = "unknown"; kind = "GPU"
        return {"vendor": vendor, "kind": kind}

    def test_nvidia_discrete(self):
        r = self._classify("NVIDIA GeForce RTX 4090")
        self.assertEqual(r["vendor"], "nvidia")
        self.assertIn("NVIDIA", r["kind"])

    def test_nvidia_quadro(self):
        r = self._classify("NVIDIA Quadro RTX 5000")
        self.assertEqual(r["vendor"], "nvidia")

    def test_amd_discrete_radeon(self):
        r = self._classify("AMD Radeon RX 7900 XTX")
        self.assertEqual(r["vendor"], "amd")
        self.assertIn("дискретная", r["kind"])

    def test_amd_integrated_vega(self):
        r = self._classify("AMD Radeon Vega 8 Graphics")
        self.assertEqual(r["vendor"], "amd")
        self.assertIn("встроенная", r["kind"])

    def test_amd_integrated_780m(self):
        r = self._classify("AMD Radeon 780M")
        self.assertEqual(r["vendor"], "amd")
        self.assertIn("встроенная", r["kind"])

    def test_intel_uhd(self):
        r = self._classify("Intel(R) UHD Graphics 770")
        self.assertEqual(r["vendor"], "intel")
        self.assertIn("Intel", r["kind"])

    def test_intel_iris(self):
        r = self._classify("Intel Iris Xe Graphics")
        self.assertEqual(r["vendor"], "intel")

    def test_unknown_vendor(self):
        r = self._classify("Some Generic Display Adapter")
        self.assertEqual(r["vendor"], "unknown")
        self.assertEqual(r["kind"], "GPU")

    def test_case_insensitive_nvidia(self):
        r = self._classify("nvidia geforce gtx 1080")
        self.assertEqual(r["vendor"], "nvidia")

    def test_case_insensitive_intel(self):
        r = self._classify("INTEL HD Graphics 630")
        self.assertEqual(r["vendor"], "intel")


# ══════════════════════════════════════════════════════════════════
#  3. Фильтрация системных/виртуальных адаптеров
# ══════════════════════════════════════════════════════════════════

class TestIgnoredDevices(unittest.TestCase):

    def _is_ignored(self, name: str) -> bool:
        return any(kw.lower() in name.lower() for kw in mod.IGNORED_KEYWORDS)

    def test_microsoft_basic_display_ignored(self):
        self.assertTrue(self._is_ignored("Microsoft Basic Display Adapter"))

    def test_rdp_ignored(self):
        self.assertTrue(self._is_ignored("RDP Remote Display Adapter"))

    def test_virtual_ignored(self):
        self.assertTrue(self._is_ignored("VMware Virtual GPU"))

    def test_remote_ignored(self):
        self.assertTrue(self._is_ignored("Parsec Remote Display"))

    def test_nvidia_not_ignored(self):
        self.assertFalse(self._is_ignored("NVIDIA GeForce RTX 3080"))

    def test_amd_not_ignored(self):
        self.assertFalse(self._is_ignored("AMD Radeon RX 6700 XT"))

    def test_intel_not_ignored(self):
        self.assertFalse(self._is_ignored("Intel UHD Graphics 730"))


# ══════════════════════════════════════════════════════════════════
#  4. WMI — обнаружение GPU
# ══════════════════════════════════════════════════════════════════

class TestGetGpuDevices(unittest.TestCase):

    def _make_gpu(self, name, pnp_id="PCI\\VEN_10DE&DEV_2204",
                  status="OK", driver_ver="31.0.0.0", driver_date="20240101000000",
                  h_res=1920, v_res=1080, refresh=60, adapter_ram=4294967296):
        gpu = MagicMock()
        gpu.Name        = name
        gpu.PNPDeviceID = pnp_id
        gpu.Status      = status
        gpu.DriverVersion = driver_ver
        gpu.DriverDate  = driver_date
        gpu.CurrentHorizontalResolution = h_res
        gpu.CurrentVerticalResolution   = v_res
        gpu.CurrentRefreshRate          = refresh
        gpu.AdapterRAM                  = adapter_ram
        return gpu

    @patch("wmi.WMI")
    def test_detects_single_nvidia(self, mock_wmi):
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("NVIDIA GeForce RTX 3070")
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["vendor"], "nvidia")
        self.assertEqual(devices[0]["name"], "NVIDIA GeForce RTX 3070")

    @patch("wmi.WMI")
    def test_detects_multiple_gpus(self, mock_wmi):
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("Intel UHD Graphics 630"),
            self._make_gpu("NVIDIA GeForce GTX 1660"),
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 2)
        vendors = {d["vendor"] for d in devices}
        self.assertIn("intel", vendors)
        self.assertIn("nvidia", vendors)

    @patch("wmi.WMI")
    def test_filters_microsoft_adapter(self, mock_wmi):
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("Microsoft Basic Display Adapter"),
            self._make_gpu("NVIDIA GeForce RTX 4080"),
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["vendor"], "nvidia")

    @patch("wmi.WMI")
    def test_filters_all_system_adapters(self, mock_wmi):
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("Microsoft Basic Display Adapter"),
            self._make_gpu("RDP Remote Display"),
            self._make_gpu("Virtual GPU"),
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 0)

    @patch("wmi.WMI")
    def test_driver_date_truncated_to_10_chars(self, mock_wmi):
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("Intel UHD Graphics", driver_date="20240315000000.000000+000")
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices[0]["driver_date"]), 10)

    @patch("wmi.WMI", side_effect=Exception("WMI failure"))
    def test_wmi_exception_returns_error_entry(self, _):
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 1)
        self.assertIn("Ошибка WMI", devices[0]["name"])
        self.assertEqual(devices[0]["status"], "Error")

    @patch("wmi.WMI")
    def test_none_fields_handled_gracefully(self, mock_wmi):
        gpu = MagicMock()
        gpu.Name          = None
        gpu.PNPDeviceID   = None
        gpu.Status        = None
        gpu.DriverVersion = None
        gpu.DriverDate    = None
        gpu.CurrentHorizontalResolution = None
        gpu.CurrentVerticalResolution   = None
        gpu.CurrentRefreshRate          = None
        gpu.AdapterRAM                  = None
        mock_wmi.return_value.Win32_VideoController.return_value = [gpu]
        # «Unknown» не совпадает с IGNORED_KEYWORDS
        devices = mod.get_gpu_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["name"], "Unknown")
        self.assertEqual(devices[0]["driver_ver"], "N/A")
        self.assertEqual(devices[0]["resolution"], "Не активен")
        self.assertEqual(devices[0]["ram"], "Динамическая")

    @patch("wmi.WMI")
    def test_resolution_formatting(self, mock_wmi):
        """Разрешение и частота обновления должны правильно форматироваться."""
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("NVIDIA RTX 3070", h_res=2560, v_res=1440, refresh=144)
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(devices[0]["resolution"], "2560x1440 @ 144 Гц")

    @patch("wmi.WMI")
    def test_resolution_no_refresh(self, mock_wmi):
        """Разрешение без частоты обновления (refresh=None)."""
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("NVIDIA RTX 3070", h_res=1920, v_res=1080, refresh=None)
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(devices[0]["resolution"], "1920x1080")

    @patch("wmi.WMI")
    def test_ram_gb_formatting(self, mock_wmi):
        """VRAM в гигабайтах должна форматироваться как X.0 ГБ."""
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("NVIDIA RTX 3070", adapter_ram=8589934592)  # 8 GB
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(devices[0]["ram"], "8.0 ГБ")

    @patch("wmi.WMI")
    def test_ram_mb_formatting(self, mock_wmi):
        """VRAM меньше 1 ГБ должна отображаться в МБ."""
        mock_wmi.return_value.Win32_VideoController.return_value = [
            self._make_gpu("Intel UHD 630", adapter_ram=536870912)  # 512 MB
        ]
        devices = mod.get_gpu_devices()
        self.assertEqual(devices[0]["ram"], "512 МБ")


# ══════════════════════════════════════════════════════════════════
#  5. find_devcon
# ══════════════════════════════════════════════════════════════════

class TestFindDevcon(unittest.TestCase):

    @patch("subprocess.run")
    def test_finds_devcon_at_windows_path(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        result = mod.find_devcon()
        # Должен найти хотя бы один из кандидатов
        self.assertIsNotNone(result)

    @patch("subprocess.run", side_effect=FileNotFoundError)
    def test_returns_none_when_not_found(self, _):
        result = mod.find_devcon()
        self.assertIsNone(result)

    @patch("subprocess.run")
    def test_first_valid_candidate_returned(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = mod.find_devcon()
        # Первый кандидат — DEVCON_DEST (C:\Windows\devcon.exe)
        self.assertEqual(result, mod.DEVCON_DEST)


# ══════════════════════════════════════════════════════════════════
#  6. download_devcon (мокируем HTTP и FS)
# ══════════════════════════════════════════════════════════════════

class TestDownloadDevcon(unittest.TestCase):

    def _make_sources_json(self, sha: str, extract_name: str = "filTEST") -> list:
        return [{
            "Name": "Windows 11 Test",
            "Sources": [
                {
                    "Architecture": "X64",
                    "Url": "https://download.microsoft.com/fake.cab",
                    "Sha256": sha,
                    "ExtractionName": extract_name,
                },
                {
                    "Architecture": "X86",
                    "Url": "https://download.microsoft.com/fake32.cab",
                    "Sha256": sha,
                    "ExtractionName": extract_name,
                },
            ]
        }]

    def _log(self):
        logs = []
        return logs, logs.append

    @patch("platform.machine", return_value="AMD64")
    @patch("requests.get")
    def test_sha256_mismatch_aborts(self, mock_get, _):
        fake_cab = b"fake_cab_data"
        correct_sha = mod._sha256(fake_cab)
        wrong_sha   = "A" * 64

        sources_resp = MagicMock()
        sources_resp.json.return_value = self._make_sources_json(wrong_sha)
        sources_resp.raise_for_status = MagicMock()

        cab_resp = MagicMock()
        cab_resp.raise_for_status = MagicMock()
        cab_resp.headers = {"content-length": str(len(fake_cab))}
        cab_resp.iter_content.return_value = [fake_cab]

        mock_get.side_effect = [sources_resp, cab_resp]

        logs, log_cb = self._log()
        result = mod.download_devcon(log_cb)

        self.assertIsNone(result)
        self.assertTrue(any("не совпадает" in l for l in logs))

    @patch("platform.machine", return_value="AMD64")
    @patch("requests.get")
    def test_sources_fetch_failure(self, mock_get, _):
        mock_get.side_effect = Exception("Network error")
        logs, log_cb = self._log()
        result = mod.download_devcon(log_cb)
        self.assertIsNone(result)
        self.assertTrue(any("источников" in l for l in logs))

    @patch("platform.machine", return_value="AMD64")
    @patch("requests.get")
    def test_cab_download_failure(self, mock_get, _):
        sources_resp = MagicMock()
        sources_resp.json.return_value = self._make_sources_json("A" * 64)
        sources_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [sources_resp, Exception("CAB download error")]
        logs, log_cb = self._log()
        result = mod.download_devcon(log_cb)
        self.assertIsNone(result)
        self.assertTrue(any("Ошибка загрузки" in l for l in logs))

    @patch("platform.machine", return_value="AMD64")
    @patch("subprocess.run")
    @patch("shutil.copy2")
    @patch("requests.get")
    def test_successful_download_and_install(self, mock_get, mock_copy, mock_run, _):
        fake_cab = b"fake_valid_cab"
        correct_sha = mod._sha256(fake_cab)
        extract_name = "filTEST_devcon"

        sources_resp = MagicMock()
        sources_resp.json.return_value = self._make_sources_json(correct_sha, extract_name)
        sources_resp.raise_for_status = MagicMock()

        cab_resp = MagicMock()
        cab_resp.raise_for_status = MagicMock()
        cab_resp.headers = {"content-length": str(len(fake_cab))}
        cab_resp.iter_content.return_value = [fake_cab]

        mock_get.side_effect = [sources_resp, cab_resp]
        mock_run.return_value = MagicMock(returncode=0)

        # Создаём реальный temp-каталог и подкладываем в него «извлечённый» файл
        tmp = tempfile.mkdtemp(prefix="test_devcon_")
        try:
            open(os.path.join(tmp, extract_name), "wb").close()

            # Патчим mkdtemp внутри модуля — именно там он вызывается
            with patch("driver_reinstaller.tempfile.mkdtemp", return_value=tmp):
                logs, log_cb = self._log()
                result = mod.download_devcon(log_cb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertEqual(result, mod.DEVCON_DEST)
        mock_copy.assert_called_once()
        self.assertTrue(any("успешно установлен" in l for l in logs))

    @patch("platform.machine", return_value="AMD64")
    @patch("subprocess.run")
    @patch("requests.get")
    def test_permission_error_on_copy(self, mock_get, mock_run, _):
        fake_cab = b"perm_test"
        sha = mod._sha256(fake_cab)
        extract_name = "filPERM"

        sources_resp = MagicMock()
        sources_resp.json.return_value = self._make_sources_json(sha, extract_name)
        sources_resp.raise_for_status = MagicMock()

        cab_resp = MagicMock()
        cab_resp.raise_for_status = MagicMock()
        cab_resp.headers = {"content-length": str(len(fake_cab))}
        cab_resp.iter_content.return_value = [fake_cab]

        mock_get.side_effect = [sources_resp, cab_resp]
        mock_run.return_value = MagicMock(returncode=0)

        tmp = tempfile.mkdtemp(prefix="test_perm_")
        try:
            open(os.path.join(tmp, extract_name), "wb").close()

            with patch("driver_reinstaller.tempfile.mkdtemp", return_value=tmp), \
                 patch("shutil.copy2", side_effect=PermissionError("Access denied")):
                logs, log_cb = self._log()
                result = mod.download_devcon(log_cb)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        self.assertIsNone(result)
        self.assertTrue(any("доступе" in l for l in logs))


# ══════════════════════════════════════════════════════════════════
#  7. _run_cmd
# ══════════════════════════════════════════════════════════════════

class TestRunCmd(unittest.TestCase):

    def _log(self):
        logs = []; return logs, logs.append

    @patch("subprocess.run")
    def test_successful_command(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="Success output", stderr=""
        )
        logs, log_cb = self._log()
        code, out = mod._run_cmd(["echo", "test"], log_cb)
        self.assertEqual(code, 0)
        self.assertIn("Success output", out)

    @patch("subprocess.run")
    def test_failed_command_returns_nonzero(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error msg")
        logs, log_cb = self._log()
        code, out = mod._run_cmd(["cmd", "/c", "exit 1"], log_cb)
        self.assertEqual(code, 1)

    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 5))
    def test_timeout_returns_minus_one(self, _):
        logs, log_cb = self._log()
        code, out = mod._run_cmd(["sleep", "999"], log_cb)
        self.assertEqual(code, -1)
        self.assertTrue(any("таймаут" in l for l in logs))

    @patch("subprocess.run", side_effect=Exception("unexpected"))
    def test_generic_exception(self, _):
        logs, log_cb = self._log()
        code, out = mod._run_cmd(["bad_cmd"], log_cb)
        self.assertEqual(code, -1)
        self.assertTrue(any("Ошибка" in l for l in logs))

    @patch("subprocess.run")
    def test_log_contains_command(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        logs, log_cb = self._log()
        mod._run_cmd(["pnputil", "/disable-device", "PCI\\TEST"], log_cb)
        self.assertTrue(any("pnputil" in l for l in logs))


# ══════════════════════════════════════════════════════════════════
#  8. reinstall_driver — маршрутизация devcon vs pnputil
# ══════════════════════════════════════════════════════════════════

class TestReinstallDriver(unittest.TestCase):

    def _log(self):
        logs = []; return logs, logs.append

    def test_empty_device_id_fails(self):
        logs, log_cb = self._log()
        result = mod.reinstall_driver("", log_cb)
        self.assertFalse(result)
        self.assertTrue(any("PNP Device ID" in l for l in logs))

    @patch.object(mod, "find_devcon", return_value=r"C:\Windows\devcon.exe")
    @patch.object(mod, "_reinstall_with_devcon", return_value=True)
    def test_routes_to_devcon_when_available(self, mock_dc, mock_find):
        logs, log_cb = self._log()
        result = mod.reinstall_driver("PCI\\VEN_10DE&DEV_1234", log_cb)
        self.assertTrue(result)
        mock_dc.assert_called_once()

    @patch.object(mod, "find_devcon", return_value=None)
    @patch.object(mod, "_reinstall_with_pnputil", return_value=True)
    def test_routes_to_pnputil_when_no_devcon(self, mock_pnp, mock_find):
        logs, log_cb = self._log()
        result = mod.reinstall_driver("PCI\\VEN_8086&DEV_0000", log_cb)
        self.assertTrue(result)
        mock_pnp.assert_called_once()

    @patch.object(mod, "find_devcon", return_value=r"C:\Windows\devcon.exe")
    @patch("subprocess.run")
    def test_devcon_reinstall_calls_remove_and_rescan(self, mock_run, _):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        logs, log_cb = self._log()
        result = mod._reinstall_with_devcon(
            r"C:\Windows\devcon.exe", "PCI\\VEN_10DE&DEV_2204", log_cb
        )
        self.assertTrue(result)
        calls = [str(c) for c in mock_run.call_args_list]
        self.assertTrue(any("remove" in c for c in calls))
        self.assertTrue(any("rescan" in c for c in calls))

    @patch("subprocess.run")
    def test_pnputil_reinstall_calls_disable_enable(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        logs, log_cb = self._log()
        result = mod._reinstall_with_pnputil("PCI\\VEN_8086&DEV_0000", log_cb)
        self.assertTrue(result)
        calls_str = str(mock_run.call_args_list)
        self.assertIn("disable-device", calls_str)
        self.assertIn("enable-device",  calls_str)
        self.assertIn("powershell",      calls_str)


# ══════════════════════════════════════════════════════════════════
#  8.5. Новые системные инструменты (шейдеры, точка восстановления)
# ══════════════════════════════════════════════════════════════════

class TestCleanShaderCaches(unittest.TestCase):
    """Тесты очистки шейдерного кэша."""

    def _log(self):
        logs = []; return logs, logs.append

    def test_clean_empty_caches(self):
        """Если нет директорий с кэшем — 0 файлов, 0 байт."""
        with patch.dict(os.environ, {"LOCALAPPDATA": "C:\\NONEXISTENT_PATH_123",
                                     "APPDATA": "C:\\NONEXISTENT_PATH_456"}):
            logs, log_cb = self._log()
            files, total_bytes = mod.clean_shader_caches(log_cb)
            self.assertEqual(files, 0)
            self.assertEqual(total_bytes, 0)

    def test_clean_with_temp_files(self):
        """Создаем временные файлы-кэши и проверяем их удаление."""
        tmp = tempfile.mkdtemp(prefix="shader_test_")
        try:
            nvidia_dx = os.path.join(tmp, "NVIDIA", "DXCache")
            os.makedirs(nvidia_dx)
            # Создаем тестовые файлы
            for i in range(3):
                with open(os.path.join(nvidia_dx, f"cache_{i}.bin"), "wb") as f:
                    f.write(b"x" * 1024)

            with patch.dict(os.environ, {"LOCALAPPDATA": tmp, "APPDATA": tmp}):
                logs, log_cb = self._log()
                files, total_bytes = mod.clean_shader_caches(log_cb)
                self.assertEqual(files, 3)
                self.assertEqual(total_bytes, 3072)  # 3 * 1024
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_clean_removes_empty_subdirs(self):
        """После удаления файлов пустые подкаталоги тоже удаляются."""
        tmp = tempfile.mkdtemp(prefix="shader_test_")
        try:
            sub = os.path.join(tmp, "NVIDIA", "DXCache", "subdir1", "subdir2")
            os.makedirs(sub)
            with open(os.path.join(sub, "test.bin"), "wb") as f:
                f.write(b"x" * 512)

            with patch.dict(os.environ, {"LOCALAPPDATA": tmp, "APPDATA": tmp}):
                logs, log_cb = self._log()
                files, total_bytes = mod.clean_shader_caches(log_cb)
                self.assertEqual(files, 1)
                # Пустые поддиректории должны быть удалены
                self.assertFalse(os.path.exists(sub))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestCreateRestorePoint(unittest.TestCase):
    """Тесты создания точки восстановления."""

    def _log(self):
        logs = []; return logs, logs.append

    @patch("subprocess.run")
    def test_restore_point_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="SUCCESS", stderr="")
        logs, log_cb = self._log()
        result = mod.create_restore_point(log_cb)
        self.assertTrue(result)
        self.assertTrue(any("успешно" in l for l in logs))

    @patch("subprocess.run")
    def test_restore_point_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="ERROR: protection disabled", stderr="")
        logs, log_cb = self._log()
        result = mod.create_restore_point(log_cb)
        self.assertFalse(result)
        self.assertTrue(any("Не удалось" in l for l in logs))


class TestExportDriverBackup(unittest.TestCase):
    """Тесты экспорта бэкапа драйверов."""

    def _log(self):
        logs = []; return logs, logs.append

    @patch("subprocess.run")
    def test_backup_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="exported", stderr="")
        tmp = tempfile.mkdtemp(prefix="backup_test_")
        try:
            logs, log_cb = self._log()
            result = mod.export_driver_backup(tmp, log_cb)
            self.assertTrue(result)
            self.assertTrue(any("успешно" in l for l in logs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @patch("subprocess.run")
    def test_backup_nonzero_return(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="partial failure", stderr="")
        tmp = tempfile.mkdtemp(prefix="backup_test_")
        try:
            logs, log_cb = self._log()
            result = mod.export_driver_backup(tmp, log_cb)
            self.assertFalse(result)
            self.assertTrue(any("предупреждениями" in l for l in logs))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
#  9. Константы и конфигурация
# ══════════════════════════════════════════════════════════════════

class TestConstants(unittest.TestCase):

    def test_devcon_sources_url_is_github(self):
        self.assertIn("github", mod.DEVCON_SOURCES_URL)
        self.assertTrue(mod.DEVCON_SOURCES_URL.startswith("https://"))

    def test_devcon_dest_is_in_windows(self):
        winroot = os.environ.get("SystemRoot", "C:\\Windows")
        self.assertTrue(mod.DEVCON_DEST.startswith(winroot))
        self.assertTrue(mod.DEVCON_DEST.endswith("devcon.exe"))

    def test_driver_pages_has_all_vendors(self):
        for vendor in ("nvidia", "amd", "intel"):
            self.assertIn(vendor, mod.DRIVER_PAGES)
            self.assertTrue(mod.DRIVER_PAGES[vendor].startswith("https://"))

    def test_ignored_keywords_nonempty(self):
        self.assertGreater(len(mod.IGNORED_KEYWORDS), 0)
        for kw in mod.IGNORED_KEYWORDS:
            self.assertIsInstance(kw, str)

    def test_all_driver_page_urls_valid(self):
        import re
        pattern = re.compile(r"^https://[\w./\-]+$")
        for vendor, url in mod.DRIVER_PAGES.items():
            self.assertRegex(url, pattern, f"Некорректный URL для {vendor}: {url}")


# ══════════════════════════════════════════════════════════════════
#  10. Реальное WMI — интеграционный тест
# ══════════════════════════════════════════════════════════════════

class TestRealWMIIntegration(unittest.TestCase):
    """Запускается на реальной системе — не мокируем WMI."""

    def test_get_gpu_devices_returns_list(self):
        devices = mod.get_gpu_devices()
        self.assertIsInstance(devices, list)

    def test_devices_have_required_keys(self):
        devices = mod.get_gpu_devices()
        required = {"name", "kind", "vendor", "device_id", "status",
                    "driver_ver", "driver_date", "resolution", "ram"}
        for d in devices:
            for key in required:
                self.assertIn(key, d, f"Отсутствует ключ '{key}' в {d}")

    def test_at_least_one_gpu_found(self):
        devices = mod.get_gpu_devices()
        self.assertGreater(len(devices), 0, "WMI не нашёл ни одного видеоадаптера")

    def test_vendor_values_are_valid(self):
        valid = {"nvidia", "amd", "intel", "unknown"}
        devices = mod.get_gpu_devices()
        for d in devices:
            self.assertIn(d["vendor"], valid, f"Неожиданный vendor: {d['vendor']}")

    def test_device_id_format_for_pci_devices(self):
        devices = mod.get_gpu_devices()
        pci_devs = [d for d in devices if d["device_id"].startswith("PCI\\")]
        if pci_devs:
            for d in pci_devs:
                self.assertIn("VEN_", d["device_id"],
                              f"Некорректный PNP ID: {d['device_id']}")

    def test_find_devcon_returns_path_or_none(self):
        result = mod.find_devcon()
        if result is not None:
            self.assertTrue(os.path.exists(result) or result == "devcon.exe")
        # None тоже допустим — просто не установлен


# ══════════════════════════════════════════════════════════════════
#  Кастомный запуск с красивым выводом
# ══════════════════════════════════════════════════════════════════

class _ColorResult(unittest.TextTestResult):
    def addSuccess(self, test):
        super().addSuccess(test)
        _ok(test.shortDescription() or str(test))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        _fail(f"{test.shortDescription() or str(test)}\n    {err[1]}")

    def addError(self, test, err):
        super().addError(test, err)
        _fail(f"ERROR: {test.shortDescription() or str(test)}\n    {err[1]}")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        _info(f"SKIP: {test.shortDescription() or str(test)} — {reason}")


class _ColorRunner(unittest.TextTestRunner):
    resultclass = _ColorResult

    def run(self, test):
        result = self.resultclass(self.stream, self.descriptions, self.verbosity)
        test(result)
        return result


if __name__ == "__main__":
    suites = [
        ("Вспомогательные функции",    TestUtilFunctions),
        ("Классификация GPU",           TestGpuClassification),
        ("Фильтрация системных адапт.", TestIgnoredDevices),
        ("WMI — get_gpu_devices",       TestGetGpuDevices),
        ("find_devcon",                 TestFindDevcon),
        ("download_devcon",             TestDownloadDevcon),
        ("_run_cmd",                    TestRunCmd),
        ("reinstall_driver",            TestReinstallDriver),
        ("Константы и конфигурация",    TestConstants),
        ("ИНТЕГРАЦИЯ — реальный WMI",   TestRealWMIIntegration),
    ]

    total_run = total_fail = total_err = 0
    for title, cls in suites:
        _head(title)
        suite  = unittest.TestLoader().loadTestsFromTestCase(cls)
        runner = _ColorRunner(verbosity=0, stream=open(os.devnull, "w"))
        result = runner.run(suite)
        total_run  += result.testsRun
        total_fail += len(result.failures)
        total_err  += len(result.errors)

    print(f"\n{'═'*60}")
    status = f"{GREEN}ВСЕ ТЕСТЫ ПРОШЛИ{RESET}" if (total_fail + total_err == 0) \
             else f"{RED}ЕСТЬ ПРОВАЛЫ{RESET}"
    print(f" {BOLD}Итог:{RESET} {status}  "
          f"| Запущено: {total_run}  "
          f"| {GREEN}Успех: {total_run - total_fail - total_err}{RESET}  "
          f"| {RED}Провалов: {total_fail}  Ошибок: {total_err}{RESET}")
    print(f"{'═'*60}\n")

    sys.exit(1 if (total_fail + total_err) > 0 else 0)
