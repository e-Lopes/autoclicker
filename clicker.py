import ctypes
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
from PIL import Image
import win32api
import win32con
import win32gui
import win32process
import win32ui


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
APP_NAME = "AutoClicker"
AUTO_TARGET_HINT = "masterduel"


def get_process_path(pid: int) -> str:
    try:
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_uint(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            if not ok:
                return ""
            return buffer.value
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return ""


def get_process_name(pid: int) -> str:
    path = get_process_path(pid)
    if path:
        return os.path.basename(path)
    return f"PID {pid}"


def _is_taskbar_like_window(hwnd: int) -> bool:
    if not win32gui.IsWindowVisible(hwnd):
        return False
    title = win32gui.GetWindowText(hwnd)
    if not title.strip():
        return False
    if win32gui.GetWindow(hwnd, win32con.GW_OWNER) != 0:
        return False
    exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    if exstyle & win32con.WS_EX_TOOLWINDOW:
        return False
    return True


def list_selectable_windows():
    windows = []

    def enum_cb(hwnd, _):
        if not _is_taskbar_like_window(hwnd):
            return
        title = win32gui.GetWindowText(hwnd).strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = get_process_name(pid)
        proc_path = get_process_path(pid)
        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
                "process": proc_name,
                "path": proc_path,
            }
        )

    win32gui.EnumWindows(enum_cb, None)
    windows.sort(key=lambda w: (w["process"].lower(), w["pid"]))
    return windows


def find_window_by_pid(target_pid: int):
    if not target_pid:
        return 0, ""

    matches = []

    def enum_cb(hwnd, _):
        if not _is_taskbar_like_window(hwnd):
            return
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid == target_pid:
            title = win32gui.GetWindowText(hwnd) or f"hwnd={hwnd}"
            matches.append((hwnd, title))

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return 0, ""
    return matches[0]


def _hicon_to_image(hicon, size: int):
    screen_dc = win32gui.GetDC(0)
    hdc = win32ui.CreateDCFromHandle(screen_dc)
    memdc = hdc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(hdc, size, size)
    old_obj = memdc.SelectObject(bmp)
    try:
        win32gui.DrawIconEx(memdc.GetSafeHdc(), 0, 0, hicon, size, size, 0, 0, win32con.DI_NORMAL)
        bmp_info = bmp.GetInfo()
        bmp_str = bmp.GetBitmapBits(True)
        image = Image.frombuffer(
            "RGB",
            (bmp_info["bmWidth"], bmp_info["bmHeight"]),
            bmp_str,
            "raw",
            "BGRX",
            0,
            1,
        )
        return image.convert("RGBA")
    finally:
        memdc.SelectObject(old_obj)
        win32gui.DeleteObject(bmp.GetHandle())
        memdc.DeleteDC()
        hdc.DeleteDC()
        win32gui.ReleaseDC(0, screen_dc)


def extract_process_icon_image(process_path: str, size: int = 48):
    if not process_path or not os.path.exists(process_path):
        return None

    large = ctypes.c_void_p()
    small = ctypes.c_void_p()
    try:
        count = ctypes.windll.shell32.ExtractIconExW(
            process_path,
            0,
            ctypes.byref(large),
            ctypes.byref(small),
            1,
        )
        if count <= 0:
            return None
        hicon = large.value or small.value
        if not hicon:
            return None
        return _hicon_to_image(hicon, size)
    except Exception:
        return None
    finally:
        if large.value:
            ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(large.value))
        if small.value:
            ctypes.windll.user32.DestroyIcon(ctypes.c_void_p(small.value))


class WindowClicker:
    def __init__(self):
        self._running = False
        self._exit_requested = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._click_loop, daemon=True)
        self._thread.start()

        self.target_hwnd = 0
        self.target_pid = 0
        self.target_title_exact = ""
        self.click_x = 500
        self.click_y = 350
        self.interval_seconds = 0.2

        self.on_log = None
        self.on_state_change = None
        self._last_logged_hwnd = 0

    def _log(self, msg: str):
        if self.on_log:
            self.on_log(msg)

    def _set_state(self, running: bool):
        if self.on_state_change:
            self.on_state_change(running)

    def update_config(
        self,
        click_x: int,
        click_y: int,
        interval_seconds: float,
        target_hwnd: int,
        target_pid: int,
        target_title_exact: str,
    ):
        self.click_x = click_x
        self.click_y = click_y
        self.interval_seconds = interval_seconds
        self.target_hwnd = target_hwnd
        self.target_pid = target_pid
        self.target_title_exact = target_title_exact or ""

    def start(self):
        with self._lock:
            self._running = True
        self._set_state(True)
        self._log("Autoclick iniciado.")

    def stop(self):
        with self._lock:
            self._running = False
        self._set_state(False)
        self._log("Autoclick parado.")

    def shutdown(self):
        self._exit_requested = True
        self.stop()
        self._thread.join(timeout=1)

    def _find_window(self):
        if self.target_pid:
            hwnd, title = find_window_by_pid(self.target_pid)
            if hwnd:
                self.target_hwnd = hwnd
                return hwnd, title

        if self.target_hwnd and win32gui.IsWindow(self.target_hwnd):
            title = win32gui.GetWindowText(self.target_hwnd) or f"hwnd={self.target_hwnd}"
            return self.target_hwnd, title
        return 0, ""

    def _post_click_messages(self, hwnd: int, x: int, y: int):
        lparam = win32api.MAKELONG(x, y)
        win32gui.PostMessage(hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, lparam)

    def _send_click_background(self, hwnd: int, x: int, y: int):
        # Sempre em background: nunca usa o mouse real do usuario.
        self._post_click_messages(hwnd, x, y)

        # Tenta também a subjanela no ponto para apps com render em child window.
        if win32gui.IsIconic(hwnd):
            return
        try:
            child = win32gui.ChildWindowFromPointEx(
                hwnd,
                (x, y),
                win32con.CWP_SKIPDISABLED | win32con.CWP_SKIPINVISIBLE,
            )
            if child and child != hwnd:
                sx, sy = win32gui.ClientToScreen(hwnd, (x, y))
                cx, cy = win32gui.ScreenToClient(child, (sx, sy))
                self._post_click_messages(child, cx, cy)
        except Exception:
            pass

    def _click_loop(self):
        while not self._exit_requested:
            with self._lock:
                running = self._running

            if not running:
                time.sleep(0.05)
                continue

            hwnd, title = self._find_window()
            if hwnd == 0:
                self._log("Janela alvo indisponivel. Parando.")
                self.stop()
                continue

            if hwnd != self._last_logged_hwnd:
                self._last_logged_hwnd = hwnd
                self._log(f"Janela alvo: {title}")

            try:
                self._send_click_background(hwnd, self.click_x, self.click_y)
            except Exception as exc:
                self._log(f"Falha ao clicar: {exc}")
                self.stop()

            time.sleep(self.interval_seconds)


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x720")
        self.root.resizable(False, False)

        self.clicker = WindowClicker()
        self.clicker.on_log = self._append_log
        self.clicker.on_state_change = self._on_state_change

        self.process_options = []
        self.selected_pid = 0
        self.selected_hwnd = 0
        self.selected_path = ""
        self._capturing = False
        self._test_after_capture = False

        self.process_display_var = tk.StringVar(value="")
        self.target_var = tk.StringVar(value="Alvo: nenhum")
        self.step_var = tk.StringVar(value="Selecione o processo")
        self.status_var = tk.StringVar(value="PARADO")

        self.process_icon_image = None
        self._build_ui()
        self._refresh_processes()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=14)

        header = ctk.CTkFrame(main, corner_radius=14)
        header.pack(fill="x", pady=(0, 10))

        icon_box = ctk.CTkFrame(header, width=64, height=64, corner_radius=12, fg_color="#1c2433")
        icon_box.pack(side="left", padx=(12, 10), pady=12)
        icon_box.pack_propagate(False)
        self.icon_label = ctk.CTkLabel(icon_box, text="APP", font=ctk.CTkFont(size=14, weight="bold"))
        self.icon_label.pack(expand=True)

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.pack(side="left", fill="x", expand=True, pady=12)
        ctk.CTkLabel(title_block, text=APP_NAME, font=ctk.CTkFont(size=30, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            title_block,
            text="Clica em background no ponto salvo",
            text_color="#8a8f9d",
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(title_block, textvariable=self.target_var, text_color="#8bb8ff", font=ctk.CTkFont(size=13)).pack(
            anchor="w", pady=(6, 0)
        )

        self.status_chip = ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            fg_color="#2b3344",
            corner_radius=10,
            width=130,
            height=44,
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.status_chip.pack(side="right", padx=12, pady=12)

        self.step_chip = ctk.CTkLabel(
            main,
            textvariable=self.step_var,
            fg_color="#1b1f2b",
            corner_radius=10,
            anchor="w",
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.step_chip.pack(fill="x", pady=(0, 10), padx=1, ipady=7)

        process_card = ctk.CTkFrame(main, corner_radius=14)
        process_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(process_card, text="PROCESSO", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=14, pady=(10, 8)
        )

        process_row = ctk.CTkFrame(process_card, fg_color="transparent")
        process_row.pack(fill="x", padx=14, pady=(0, 12))
        self.cmb_process = ctk.CTkComboBox(
            process_row,
            variable=self.process_display_var,
            values=[""],
            state="readonly",
            command=lambda _x: self._on_process_selected(),
            height=40,
        )
        self.cmb_process.pack(side="left", fill="x", expand=True)
        self.btn_refresh = ctk.CTkButton(process_row, text="Atualizar", width=150, height=40, command=self._refresh_processes)
        self.btn_refresh.pack(side="left", padx=(8, 0))

        point_card = ctk.CTkFrame(main, corner_radius=14)
        point_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(point_card, text="PONTO", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=14, pady=(10, 8)
        )
        row = ctk.CTkFrame(point_card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 10))
        ctk.CTkLabel(row, text="X").pack(side="left")
        self.entry_x = ctk.CTkEntry(row, width=90)
        self.entry_x.insert(0, "500")
        self.entry_x.pack(side="left", padx=(6, 14))
        ctk.CTkLabel(row, text="Y").pack(side="left")
        self.entry_y = ctk.CTkEntry(row, width=90)
        self.entry_y.insert(0, "350")
        self.entry_y.pack(side="left", padx=(6, 14))
        ctk.CTkLabel(row, text="Intervalo").pack(side="left")
        self.entry_interval = ctk.CTkEntry(row, width=100)
        self.entry_interval.insert(0, "0.2")
        self.entry_interval.pack(side="left", padx=(8, 0))

        btn_card = ctk.CTkFrame(main, corner_radius=14)
        btn_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(btn_card, text="ACOES", font=ctk.CTkFont(size=16, weight="bold")).pack(
            anchor="w", padx=14, pady=(10, 8)
        )

        buttons = ctk.CTkFrame(btn_card, fg_color="transparent")
        buttons.pack(fill="x", padx=14, pady=(0, 12))
        self.btn_validate = ctk.CTkButton(
            buttons,
            text="VALIDAR",
            width=180,
            height=82,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=self._validate_target,
        )
        self.btn_validate.pack(side="left")
        self.btn_capture = ctk.CTkButton(
            buttons,
            text="CAPTURAR",
            width=180,
            height=82,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._capture_point_interactive,
        )
        self.btn_capture.pack(side="left", padx=(8, 0))
        self.btn_test = ctk.CTkButton(
            buttons,
            text="TESTE",
            width=180,
            height=82,
            font=ctk.CTkFont(size=18, weight="bold"),
            command=self._test_single_click,
        )
        self.btn_test.pack(side="left", padx=(8, 0))
        self.btn_start = ctk.CTkButton(
            buttons,
            text="INICIAR",
            width=180,
            height=82,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#0f9d58",
            hover_color="#0b8248",
            command=self._start,
        )
        self.btn_start.pack(side="left", padx=(8, 0))
        self.btn_stop = ctk.CTkButton(
            buttons,
            text="PARAR",
            width=180,
            height=82,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#e53935",
            hover_color="#c62828",
            state="disabled",
            command=self._stop,
        )
        self.btn_stop.pack(side="left", padx=(8, 0))

        log_card = ctk.CTkFrame(main, corner_radius=14)
        log_card.pack(fill="both", expand=True)
        top_log = ctk.CTkFrame(log_card, fg_color="transparent")
        top_log.pack(fill="x", padx=14, pady=(10, 8))
        ctk.CTkLabel(top_log, text="LOG", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")
        self.btn_clear_log = ctk.CTkButton(top_log, text="Limpar", width=110, height=34, command=self._clear_log)
        self.btn_clear_log.pack(side="right")
        self.log = tk.Text(
            log_card,
            height=10,
            state="disabled",
            bg="#111827",
            fg="#d1d5db",
            insertbackground="#d1d5db",
            relief="flat",
            font=("Consolas", 10),
        )
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self._append_log("Atalhos: F6 teste | F7 iniciar/parar | F8 capturar")
        self._append_log("Modo unico: background seguro (sem mouse real).")
        self._update_action_availability()

    def _bind_shortcuts(self):
        self.root.bind("<F6>", lambda _e: self._test_single_click())
        self.root.bind("<F7>", lambda _e: self._toggle_run())
        self.root.bind("<F8>", lambda _e: self._capture_point_interactive())

    def _append_log(self, text: str):
        self.root.after(0, self._append_log_ui, text)

    def _append_log_ui(self, text: str):
        self.log.config(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} - {text}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _set_step(self, text: str):
        self.step_var.set(text)

    def _set_icon_from_path(self, process_path: str):
        image = extract_process_icon_image(process_path, size=48)
        if image is None:
            self.process_icon_image = None
            self.icon_label.configure(image=None, text="APP")
            return
        self.process_icon_image = ctk.CTkImage(light_image=image, dark_image=image, size=(44, 44))
        self.icon_label.configure(image=self.process_icon_image, text="")

    def _on_state_change(self, running: bool):
        self.root.after(0, self._set_buttons_state, running)

    def _set_buttons_state(self, running: bool):
        self.status_var.set("RODANDO" if running else "PARADO")
        self.status_chip.configure(fg_color="#166534" if running else "#2b3344")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self._update_action_availability()

    def _update_action_availability(self):
        has_target = bool(self.selected_pid or self.selected_hwnd)
        running = self.status_var.get() == "RODANDO"
        enabled = "normal" if has_target and not running else "disabled"
        capture_state = "disabled" if running or self._capturing or not has_target else "normal"

        self.btn_validate.configure(state="disabled" if running else ("normal" if has_target else "disabled"))
        self.btn_capture.configure(state=capture_state)
        self.btn_test.configure(state=enabled)
        self.btn_start.configure(state=enabled)
        self.btn_refresh.configure(state="disabled" if running else "normal")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._append_log("Log limpo.")

    def _toggle_run(self):
        if self.status_var.get() == "RODANDO":
            self._stop()
        else:
            self._start()

    def _refresh_processes(self):
        previous_pid = self.selected_pid
        windows = list_selectable_windows()
        by_pid = {}
        for w in windows:
            pid = w["pid"]
            if pid not in by_pid:
                by_pid[pid] = {
                    "pid": pid,
                    "process": w["process"],
                    "title": w["title"],
                    "path": w["path"],
                    "hwnd": w["hwnd"],
                    "display": f"{w['process']} | PID {pid}",
                }

        self.process_options = sorted(by_pid.values(), key=lambda p: p["display"].lower())
        values = [p["display"] for p in self.process_options] or [""]
        self.cmb_process.configure(values=values)

        if not self.process_options:
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.selected_path = ""
            self.process_display_var.set("")
            self.target_var.set("Alvo: nenhum")
            self._set_icon_from_path("")
            self._set_step("Abra o jogo e clique em Atualizar")
            self._append_log("Nenhum processo selecionavel encontrado.")
            self._update_action_availability()
            return

        idx = 0
        if previous_pid:
            for i, opt in enumerate(self.process_options):
                if opt["pid"] == previous_pid:
                    idx = i
                    break
        else:
            for i, opt in enumerate(self.process_options):
                joined = f"{opt['process']} {opt['title']}".lower()
                if AUTO_TARGET_HINT in joined:
                    idx = i
                    break

        selected = self.process_options[idx]
        self.process_display_var.set(selected["display"])
        self._on_process_selected()
        self._append_log(f"Lista atualizada: {len(self.process_options)} processo(s).")

    def _on_process_selected(self, _event=None):
        selected_display = self.process_display_var.get().strip()
        selected = None
        for opt in self.process_options:
            if opt["display"] == selected_display:
                selected = opt
                break
        if not selected:
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.selected_path = ""
            self.target_var.set("Alvo: nenhum")
            self._set_icon_from_path("")
            self._update_action_availability()
            return

        self.selected_pid = selected["pid"]
        self.selected_hwnd = selected["hwnd"]
        self.selected_path = selected["path"]
        self.target_var.set(f"Alvo: {selected['process']} [PID {selected['pid']}]")
        self._set_icon_from_path(self.selected_path)
        self._set_step("Valide e capture o ponto")
        self._append_log(f"Selecionado: {selected['process']} | PID {selected['pid']}")
        self._update_action_availability()

    def _resolve_target_window(self):
        if self.selected_pid:
            hwnd, title = find_window_by_pid(self.selected_pid)
            if hwnd:
                return hwnd, title
        if self.selected_hwnd and win32gui.IsWindow(self.selected_hwnd):
            title = win32gui.GetWindowText(self.selected_hwnd) or f"hwnd={self.selected_hwnd}"
            return self.selected_hwnd, title
        return 0, ""

    def _read_form_values(self):
        try:
            x = int(self.entry_x.get().strip())
            y = int(self.entry_y.get().strip())
            interval = float(self.entry_interval.get().strip())
            return True, x, y, interval
        except ValueError:
            messagebox.showerror("Erro", "X, Y e Intervalo precisam ser numeros validos.")
            return False, 0, 0, 0.0

    def _validate_target(self):
        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo.")
            self._set_step("Corrija o processo alvo e valide novamente")
            return
        self.selected_hwnd = target_hwnd
        _, pid = win32process.GetWindowThreadProcessId(target_hwnd)
        self.target_var.set(f"Alvo: {target_title} [PID {pid}]")
        self._append_log(f"Alvo validado: '{target_title}' (HWND={target_hwnd}, PID={pid}).")
        self._set_step("Capture e teste o ponto")
        self._update_action_availability()

    def _capture_point_interactive(self):
        if self._capturing:
            return
        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Selecione um alvo valido antes de capturar.")
            return

        self._capturing = True
        self._update_action_availability()
        self._append_log(f"Captura iniciada para: {target_title}")
        self._set_step("Clique no ponto dentro do jogo")

        try:
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            self._append_log("Nao foi possivel focar a janela. Continue mesmo assim.")

        threading.Thread(target=self._capture_click_worker, args=(target_hwnd,), daemon=True).start()

    def _capture_click_worker(self, target_hwnd: int):
        try:
            timeout_seconds = 20
            deadline = time.time() + timeout_seconds
            was_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)

            while time.time() < deadline:
                is_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
                if is_down and not was_down:
                    pos = win32api.GetCursorPos()
                    clicked_hwnd = win32gui.WindowFromPoint(pos)
                    inside = clicked_hwnd == target_hwnd or win32gui.IsChild(target_hwnd, clicked_hwnd)
                    if inside:
                        client_x, client_y = win32gui.ScreenToClient(target_hwnd, pos)
                        self.root.after(0, self._finish_capture, True, client_x, client_y, "")
                        return
                    self._append_log("Clique fora da janela alvo. Aguardando...")
                was_down = is_down
                time.sleep(0.01)

            self.root.after(0, self._finish_capture, False, 0, 0, "Tempo de captura esgotado.")
        except Exception as exc:
            self.root.after(0, self._finish_capture, False, 0, 0, f"Falha na captura: {exc}")

    def _finish_capture(self, ok: bool, x: int, y: int, error: str):
        self._capturing = False
        self._update_action_availability()
        if not ok:
            self._append_log(error)
            messagebox.showerror("Erro", error)
            self._set_step("Capture novamente")
            self._test_after_capture = False
            return

        self.entry_x.delete(0, "end")
        self.entry_x.insert(0, str(x))
        self.entry_y.delete(0, "end")
        self.entry_y.insert(0, str(y))
        self._append_log(f"Ponto capturado: X={x}, Y={y}")
        self._set_step("Teste o clique e inicie")
        if self._test_after_capture:
            self._test_after_capture = False
            self._test_single_click()

    def _test_single_click(self):
        read_ok, x, y, interval = self._read_form_values()
        if not read_ok:
            return
        if interval <= 0:
            messagebox.showerror("Erro", "Intervalo precisa ser maior que 0.")
            return

        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo para teste.")
            return

        try:
            _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
            self.clicker.update_config(x, y, interval, target_hwnd, target_pid, target_title)
            self.clicker._send_click_background(target_hwnd, x, y)
            self._append_log(f"Teste enviado para '{target_title}' em X={x}, Y={y}.")
            self._set_step("Se funcionou, clique em INICIAR")
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha no teste de clique: {exc}")

    def _start(self):
        read_ok, x, y, interval = self._read_form_values()
        if not read_ok:
            return
        if interval <= 0:
            messagebox.showerror("Erro", "Intervalo precisa ser maior que 0.")
            return

        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo.")
            return

        _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
        self.clicker.update_config(x, y, interval, target_hwnd, target_pid, target_title)
        self._append_log(f"Autoclick travado em '{target_title}' (PID {target_pid}).")
        self.clicker.start()
        self._set_step("Rodando em background")

    def _stop(self):
        self.clicker.stop()
        self._set_step("Parado")

    def _on_close(self):
        self.clicker.shutdown()
        self.root.destroy()


def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
