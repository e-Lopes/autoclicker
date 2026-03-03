import ctypes
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

import customtkinter as ctk
import win32api
import win32con
import win32gui
import win32process


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AUTO_TARGET_HINT = "merge tales"
CLICK_MODE_BACKGROUND = "background_postmessage"
CLICK_MODE_COMPAT = "compat_sendinput"


def get_process_name(pid: int) -> str:
    try:
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return f"PID {pid}"

        try:
            size = ctypes.c_uint(260)
            buffer = ctypes.create_unicode_buffer(size.value)
            ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size))
            if not ok:
                return f"PID {pid}"
            return os.path.basename(buffer.value)
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
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
        display = f"{title} [{proc_name} | PID {pid}]"
        windows.append({
            "hwnd": hwnd,
            "title": title,
            "pid": pid,
            "process": proc_name,
            "display": display,
        })

    win32gui.EnumWindows(enum_cb, None)
    windows.sort(key=lambda w: w["display"].lower())
    return windows


def find_window_by_title(title_part: str):
    target = title_part.lower().strip()
    if not target:
        return 0, ""

    matches = []

    def enum_cb(hwnd, _):
        if not _is_taskbar_like_window(hwnd):
            return
        title = win32gui.GetWindowText(hwnd)
        if target in title.lower():
            matches.append((hwnd, title))

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return 0, ""

    return matches[0]


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
        self.window_title_contains = ""
        self.click_x = 500
        self.click_y = 350
        self.interval_seconds = 0.2
        self.pause_if_minimized = False
        self.click_mode = CLICK_MODE_BACKGROUND
        self.force_foreground = True

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
        window_title_contains: str,
        click_x: int,
        click_y: int,
        interval_seconds: float,
        target_hwnd: int,
        target_pid: int,
        target_title_exact: str,
        pause_if_minimized: bool,
        click_mode: str,
        force_foreground: bool,
    ):
        self.window_title_contains = window_title_contains.strip()
        self.click_x = click_x
        self.click_y = click_y
        self.interval_seconds = interval_seconds
        self.target_hwnd = target_hwnd
        self.target_pid = target_pid
        self.target_title_exact = target_title_exact or ""
        self.pause_if_minimized = pause_if_minimized
        self.click_mode = click_mode
        self.force_foreground = force_foreground

    def start(self):
        with self._lock:
            self._running = True
        self._set_state(True)
        self._log("Autoclick iniciado.")

    def stop(self):
        with self._lock:
            self._running = False
        self._set_state(False)
        self._log("Autoclick pausado.")

    def shutdown(self):
        self._exit_requested = True
        self.stop()
        self._thread.join(timeout=1)

    def _find_window(self):
        if not self.target_hwnd or not win32gui.IsWindow(self.target_hwnd):
            return 0, ""

        _, pid = win32process.GetWindowThreadProcessId(self.target_hwnd)
        if self.target_pid and pid != self.target_pid:
            return 0, ""

        title = win32gui.GetWindowText(self.target_hwnd) or f"hwnd={self.target_hwnd}"
        if self.target_title_exact and self.target_title_exact.lower() not in title.lower():
            return 0, ""

        if self.pause_if_minimized and win32gui.IsIconic(self.target_hwnd):
            return -1, title

        return self.target_hwnd, title

    def _resolve_click_receiver(self, root_hwnd: int, x: int, y: int):
        screen_point = win32gui.ClientToScreen(root_hwnd, (x, y))
        receiver_hwnd = root_hwnd

        try:
            hit_hwnd = win32gui.WindowFromPoint(screen_point)
            if hit_hwnd and (hit_hwnd == root_hwnd or win32gui.IsChild(root_hwnd, hit_hwnd)):
                receiver_hwnd = hit_hwnd
        except Exception:
            receiver_hwnd = root_hwnd

        local_x, local_y = win32gui.ScreenToClient(receiver_hwnd, screen_point)
        return receiver_hwnd, local_x, local_y

    def _send_click_background(self, hwnd: int, x: int, y: int):
        receiver_hwnd, local_x, local_y = self._resolve_click_receiver(hwnd, x, y)
        lparam = win32api.MAKELONG(local_x, local_y)
        win32gui.PostMessage(receiver_hwnd, win32con.WM_MOUSEMOVE, 0, lparam)
        win32gui.PostMessage(receiver_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
        win32gui.PostMessage(receiver_hwnd, win32con.WM_LBUTTONUP, 0, lparam)

    def _send_click_compat(self, hwnd: int, x: int, y: int):
        screen_x, screen_y = win32gui.ClientToScreen(hwnd, (x, y))
        original_x, original_y = win32api.GetCursorPos()

        if self.force_foreground:
            try:
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.01)
            except Exception:
                pass

        win32api.SetCursorPos((screen_x, screen_y))
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        win32api.mouse_event(win32con.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        win32api.SetCursorPos((original_x, original_y))

    def _send_click(self, hwnd: int, x: int, y: int):
        if self.click_mode == CLICK_MODE_COMPAT:
            self._send_click_compat(hwnd, x, y)
            return
        self._send_click_background(hwnd, x, y)

    def _click_loop(self):
        while not self._exit_requested:
            with self._lock:
                running = self._running

            if not running:
                time.sleep(0.05)
                continue

            hwnd, title = self._find_window()
            if hwnd == -1:
                self._log("Janela alvo minimizada. Pausando autoclick por seguranca.")
                self.stop()
                continue

            if hwnd == 0:
                self._log("Janela alvo nao confere mais (HWND/PID/titulo). Pausando.")
                self.stop()
                continue

            if hwnd != self._last_logged_hwnd:
                self._last_logged_hwnd = hwnd
                self._log(f"Janela alvo: {title}")

            try:
                self._send_click(hwnd, self.click_x, self.click_y)
            except Exception as exc:
                self._log(f"Falha ao clicar: {exc}")
                self.stop()

            time.sleep(self.interval_seconds)


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title("AutoClicker DU")
        self.root.geometry("980x740")
        self.root.resizable(False, False)

        self.clicker = WindowClicker()
        self.clicker.on_log = self._append_log
        self.clicker.on_state_change = self._on_state_change

        self.process_options = []
        self.selected_hwnd = 0
        self.selected_pid = 0
        self._capturing = False
        self._test_after_capture = False

        self.topmost_var = tk.BooleanVar(value=True)
        self.pause_minimized_var = tk.BooleanVar(value=False)
        self.force_foreground_var = tk.BooleanVar(value=True)
        self.click_mode_var = tk.StringVar(value="Background (PostMessage)")
        self.process_display_var = tk.StringVar(value="")
        self.next_step_var = tk.StringVar(value="Passo atual: selecione o processo alvo.")
        self.target_var = tk.StringVar(value="Alvo: nenhum")
        self.status_var = tk.StringVar(value="Status: parado")

        self._build_ui()
        self._refresh_windows()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=14)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x", pady=(0, 10))

        title_block = ctk.CTkFrame(header, fg_color="transparent")
        title_block.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(title_block, text="AutoClicker DU", font=ctk.CTkFont(size=28, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(
            title_block,
            text="F6 Teste  |  F7 Iniciar/Parar  |  F8 Capturar",
            text_color="#8a8f9d",
            font=ctk.CTkFont(size=13),
        ).pack(anchor="w", pady=(2, 0))

        toggles = ctk.CTkFrame(header, fg_color="transparent")
        toggles.pack(side="right", pady=(4, 0))
        ctk.CTkCheckBox(
            toggles,
            text="Pausar minimizado",
            variable=self.pause_minimized_var,
        ).pack(anchor="e")
        ctk.CTkCheckBox(
            toggles,
            text="Sempre no topo",
            variable=self.topmost_var,
            command=self._toggle_topmost,
        ).pack(anchor="e", pady=(6, 0))

        info = ctk.CTkFrame(main, fg_color="#1b1f2b")
        info.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(info, textvariable=self.next_step_var, font=ctk.CTkFont(size=13), anchor="w").pack(
            side="left", padx=12, pady=8, fill="x", expand=True
        )
        ctk.CTkLabel(info, textvariable=self.target_var, text_color="#8bb8ff", font=ctk.CTkFont(size=13)).pack(
            side="right", padx=12
        )

        step1 = ctk.CTkFrame(main, corner_radius=12)
        step1.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(step1, text="1. Processo Alvo", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(10, 8))

        pick_row = ctk.CTkFrame(step1, fg_color="transparent")
        pick_row.pack(fill="x", padx=14, pady=(0, 8))

        self.cmb_windows = ctk.CTkComboBox(
            pick_row,
            variable=self.process_display_var,
            values=[""],
            command=lambda _choice: self._on_window_selected(),
            state="readonly",
            width=560,
        )
        self.cmb_windows.pack(side="left", fill="x", expand=True)

        self.btn_refresh = ctk.CTkButton(pick_row, text="Atualizar", width=110, command=self._refresh_windows)
        self.btn_refresh.pack(side="left", padx=(8, 0))
        self.btn_validate_target = ctk.CTkButton(
            pick_row,
            text="Validar",
            width=110,
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._validate_target,
        )
        self.btn_validate_target.pack(side="left", padx=(8, 0))

        ctk.CTkLabel(step1, text="Fallback por titulo", text_color="#8a8f9d").pack(anchor="w", padx=14)
        self.entry_title = ctk.CTkEntry(step1, placeholder_text="Titulo da janela")
        self.entry_title.insert(0, "Merge Tales")
        self.entry_title.pack(fill="x", padx=14, pady=(4, 12))

        step2 = ctk.CTkFrame(main, corner_radius=12)
        step2.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(step2, text="2. Ponto e Ritmo", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(10, 8))

        row = ctk.CTkFrame(step2, fg_color="transparent")
        row.pack(fill="x", padx=14)
        ctk.CTkLabel(row, text="X").pack(side="left")
        self.entry_x = ctk.CTkEntry(row, width=80)
        self.entry_x.insert(0, "500")
        self.entry_x.pack(side="left", padx=(6, 10))
        ctk.CTkLabel(row, text="Y").pack(side="left")
        self.entry_y = ctk.CTkEntry(row, width=80)
        self.entry_y.insert(0, "350")
        self.entry_y.pack(side="left", padx=(6, 10))
        ctk.CTkLabel(row, text="Intervalo (s)").pack(side="left")
        self.entry_interval = ctk.CTkEntry(row, width=80)
        self.entry_interval.insert(0, "0.2")
        self.entry_interval.pack(side="left", padx=(8, 14))

        self.cmb_click_mode = ctk.CTkComboBox(
            row,
            variable=self.click_mode_var,
            state="readonly",
            values=[
                "Background (PostMessage)",
                "Compatibilidade jogo (foco + click real)",
            ],
            command=self._on_click_mode_changed,
            width=310,
        )
        self.cmb_click_mode.pack(side="left")

        self.chk_force_foreground = ctk.CTkCheckBox(
            step2,
            text="Forcar foco no modo compatibilidade",
            variable=self.force_foreground_var,
        )
        self.chk_force_foreground.pack(anchor="w", padx=14, pady=(8, 8))

        point_actions = ctk.CTkFrame(step2, fg_color="transparent")
        point_actions.pack(fill="x", padx=14, pady=(0, 12))
        self.btn_capture = ctk.CTkButton(
            point_actions,
            text="Capturar (F8)",
            fg_color="#2563eb",
            hover_color="#1d4ed8",
            command=self._capture_point_interactive,
        )
        self.btn_capture.pack(side="left")
        self.btn_test = ctk.CTkButton(point_actions, text="Teste 1 Clique (F6)", command=self._test_single_click)
        self.btn_test.pack(side="left", padx=(8, 0))
        self.btn_capture_and_test = ctk.CTkButton(point_actions, text="Capturar + Testar", command=self._capture_and_test)
        self.btn_capture_and_test.pack(side="left", padx=(8, 0))

        step3 = ctk.CTkFrame(main, corner_radius=12)
        step3.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(step3, text="3. Execucao", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(10, 8))
        run_row = ctk.CTkFrame(step3, fg_color="transparent")
        run_row.pack(fill="x", padx=14, pady=(0, 10))
        self.btn_start = ctk.CTkButton(
            run_row,
            text="Iniciar (F7)",
            width=150,
            fg_color="#0f9d58",
            hover_color="#0b8248",
            command=self._start,
        )
        self.btn_start.pack(side="left")
        self.btn_stop = ctk.CTkButton(
            run_row,
            text="Parar (F7)",
            width=150,
            fg_color="#e53935",
            hover_color="#c62828",
            state="disabled",
            command=self._stop,
        )
        self.btn_stop.pack(side="left", padx=(8, 0))

        self.status_chip = ctk.CTkLabel(
            step3,
            textvariable=self.status_var,
            fg_color="#283142",
            corner_radius=8,
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.status_chip.pack(anchor="w", padx=14, pady=(0, 10))
        self.btn_clear_log = ctk.CTkButton(step3, text="Limpar log", width=120, command=self._clear_log)
        self.btn_clear_log.pack(anchor="w", padx=14, pady=(0, 12))

        log_card = ctk.CTkFrame(main, corner_radius=12)
        log_card.pack(fill="both", expand=True)
        ctk.CTkLabel(log_card, text="Atividade", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", padx=14, pady=(10, 8))
        self.log = tk.Text(
            log_card,
            height=12,
            state="disabled",
            bg="#111827",
            fg="#d1d5db",
            insertbackground="#d1d5db",
            relief="flat",
            font=("Consolas", 10),
        )
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 14))

        self._append_log("Fluxo: escolher processo -> validar -> capturar/testar -> iniciar")
        self._append_log("Para jogos, use modo compatibilidade.")
        self._toggle_topmost()
        self._update_action_availability()
        self._on_click_mode_changed(self.click_mode_var.get())

    def _bind_shortcuts(self):
        self.root.bind("<F6>", lambda _e: self._test_single_click())
        self.root.bind("<F7>", lambda _e: self._toggle_run())
        self.root.bind("<F8>", lambda _e: self._capture_point_interactive())

    def _get_click_mode(self):
        if self.click_mode_var.get() == "Compatibilidade jogo (foco + click real)":
            return CLICK_MODE_COMPAT
        return CLICK_MODE_BACKGROUND

    def _on_click_mode_changed(self, _choice=None):
        mode = self._get_click_mode()
        if mode == CLICK_MODE_COMPAT:
            self.chk_force_foreground.configure(state="normal")
            self._append_log("Modo: Compatibilidade jogo.")
            self._set_next_step("teste 1 clique em modo compatibilidade.")
        else:
            self.chk_force_foreground.configure(state="disabled")
            self._append_log("Modo: Background (PostMessage).")

    def _append_log(self, text: str):
        self.root.after(0, self._append_log_ui, text)

    def _append_log_ui(self, text: str):
        self.log.config(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} - {text}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _on_state_change(self, running: bool):
        self.root.after(0, self._set_buttons_state, running)

    def _set_buttons_state(self, running: bool):
        self.status_var.set(f"Status: {'rodando' if running else 'parado'}")
        if running:
            self.status_chip.configure(fg_color="#166534")
        else:
            self.status_chip.configure(fg_color="#283142")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self._update_action_availability()

    def _update_action_availability(self):
        has_target = False
        try:
            target_hwnd, _ = self._resolve_target_window()
            has_target = bool(target_hwnd)
        except Exception:
            has_target = False
        running = "rodando" in self.status_var.get()

        capture_state = "disabled" if running or self._capturing or not has_target else "normal"
        action_state = "disabled" if running or not has_target else "normal"
        start_state = "disabled" if running or not has_target else "normal"

        self.btn_capture.configure(state=capture_state)
        self.btn_test.configure(state=action_state)
        self.btn_capture_and_test.configure(state=action_state if not self._capturing else "disabled")
        self.btn_validate_target.configure(state="disabled" if running else "normal")
        self.btn_start.configure(state=start_state)

    def _set_next_step(self, text: str):
        self.next_step_var.set(f"Passo atual: {text}")

    def _clear_log(self):
        self.log.config(state="normal")
        self.log.delete("1.0", "end")
        self.log.config(state="disabled")
        self._append_log("Log limpo.")

    def _toggle_run(self):
        if "rodando" in self.status_var.get():
            self._stop()
        else:
            self._start()

    def _toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.topmost_var.get()))

    def _refresh_windows(self):
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
                    "hwnd": w["hwnd"],
                    "display": f"{w['process']} | PID {pid} | Janela: {w['title']}",
                }

        self.process_options = sorted(by_pid.values(), key=lambda p: p["display"].lower())
        values = [p["display"] for p in self.process_options] or [""]
        self.cmb_windows.configure(values=values)

        if not self.process_options:
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.process_display_var.set("")
            self._append_log("Nenhum processo com janela selecionavel encontrado.")
            self.target_var.set("Alvo: nenhum")
            self._set_next_step("abra o jogo/app e clique em Atualizar.")
            self._update_action_availability()
            return

        idx_to_select = 0
        if previous_pid:
            for idx, opt in enumerate(self.process_options):
                if opt["pid"] == previous_pid:
                    idx_to_select = idx
                    break
        else:
            for idx, opt in enumerate(self.process_options):
                if AUTO_TARGET_HINT in opt["title"].lower() or AUTO_TARGET_HINT in opt["process"].lower():
                    idx_to_select = idx
                    break

        selected = self.process_options[idx_to_select]
        self.process_display_var.set(selected["display"])
        self._on_window_selected()
        self._append_log(
            f"Lista atualizada: {len(self.process_options)} processo(s). Alvo automatico: {selected['process']} (PID {selected['pid']})"
        )
        self._update_action_availability()

    def _on_window_selected(self, _event=None):
        selected_display = self.process_display_var.get().strip()
        selected = None
        for opt in self.process_options:
            if opt["display"] == selected_display:
                selected = opt
                break
        if not selected:
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.target_var.set("Alvo: nenhum")
            self._update_action_availability()
            return

        self.selected_pid = selected["pid"]
        self.selected_hwnd = selected["hwnd"]
        self.target_var.set(f"Alvo: {selected['process']} [PID {selected['pid']}]")
        self.entry_title.delete(0, "end")
        self.entry_title.insert(0, selected["title"])
        self._append_log(
            f"Selecionado processo: {selected['process']} | PID {selected['pid']} (janela atual: {selected['title']})"
        )
        self._set_next_step("capture o ponto e teste 1 vez.")
        self._update_action_availability()

    def _resolve_target_window(self):
        if self.selected_pid:
            hwnd, title = find_window_by_pid(self.selected_pid)
            if hwnd:
                return hwnd, title
        if self.selected_hwnd and win32gui.IsWindow(self.selected_hwnd):
            return self.selected_hwnd, win32gui.GetWindowText(self.selected_hwnd)
        title = self.entry_title.get().strip()
        return find_window_by_title(title)

    def _start(self):
        read_ok, title, x, y, interval = self._read_form_values()
        if not read_ok:
            return
        if interval <= 0:
            messagebox.showerror("Erro", "Intervalo precisa ser maior que 0.")
            return

        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0 and not title:
            messagebox.showerror("Erro", "Selecione uma janela ou informe um titulo.")
            return
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo para iniciar.")
            return

        _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
        self.clicker.update_config(
            title,
            x,
            y,
            interval,
            target_hwnd,
            target_pid,
            target_title,
            bool(self.pause_minimized_var.get()),
            self._get_click_mode(),
            bool(self.force_foreground_var.get()),
        )
        self._append_log(f"Travado em HWND={target_hwnd}, PID={target_pid}, titulo='{target_title}'.")
        self.clicker.start()
        self._set_next_step("autoclick em execucao. Use F7 para parar.")
        self._update_action_availability()

    def _stop(self):
        self.clicker.stop()
        self._set_next_step("ajuste o ponto/intervalo e reinicie.")
        self._update_action_availability()

    def _capture_point_interactive(self):
        if self._capturing:
            return
        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Selecione uma janela valida antes de capturar o ponto.")
            return

        self._capturing = True
        self._update_action_availability()
        self._append_log(f"Captura iniciada para: {target_title}")
        self._append_log("Clique no ponto desejado dentro da janela do jogo.")
        self._set_next_step("clique no ponto dentro do jogo para salvar X/Y.")

        try:
            win32gui.ShowWindow(target_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(target_hwnd)
        except Exception:
            self._append_log("Nao foi possivel trazer a janela para frente. Continue mesmo assim.")

        thread = threading.Thread(target=self._capture_click_worker, args=(target_hwnd,), daemon=True)
        thread.start()

    def _read_form_values(self):
        try:
            title = self.entry_title.get().strip()
            x = int(self.entry_x.get().strip())
            y = int(self.entry_y.get().strip())
            interval = float(self.entry_interval.get().strip())
            return True, title, x, y, interval
        except ValueError:
            messagebox.showerror("Erro", "X, Y e Intervalo precisam ser numeros validos.")
            return False, "", 0, 0, 0.0

    def _test_single_click(self):
        read_ok, title, x, y, interval = self._read_form_values()
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
            self.clicker.update_config(
                title,
                x,
                y,
                interval,
                target_hwnd,
                target_pid,
                target_title,
                bool(self.pause_minimized_var.get()),
                self._get_click_mode(),
                bool(self.force_foreground_var.get()),
            )
            self.clicker._send_click(target_hwnd, x, y)
            mode_text = "compatibilidade" if self._get_click_mode() == CLICK_MODE_COMPAT else "background"
            self._append_log(f"Teste: 1 clique enviado para '{target_title}' em X={x}, Y={y} (modo: {mode_text}).")
            self._set_next_step("se o clique funcionou, inicie o autoclick.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha no teste de clique: {exc}")

    def _capture_and_test(self):
        self._test_after_capture = True
        self._capture_point_interactive()

    def _validate_target(self):
        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo.")
            self._set_next_step("corrija a selecao e valide novamente.")
            return
        self.selected_hwnd = target_hwnd
        _, pid = win32process.GetWindowThreadProcessId(target_hwnd)
        self.target_var.set(f"Alvo: {target_title} [PID {pid}]")
        self._append_log(f"Alvo validado: '{target_title}' (HWND={target_hwnd}, PID={pid}).")
        self._set_next_step("capture o ponto e teste 1 clique.")
        self._update_action_availability()

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
                    self._append_log("Clique fora da janela alvo. Tente de novo (aguardando...).")
                was_down = is_down
                time.sleep(0.01)
            self.root.after(0, self._finish_capture, False, 0, 0, "Tempo esgotado para captura.")
        except Exception as exc:
            self.root.after(0, self._finish_capture, False, 0, 0, f"Falha na captura: {exc}")

    def _finish_capture(self, ok: bool, x: int, y: int, error: str):
        self._capturing = False
        self._update_action_availability()
        if not ok:
            self._append_log(error)
            messagebox.showerror("Erro", error)
            self._set_next_step("capture novamente o ponto dentro da janela alvo.")
            self._test_after_capture = False
            return

        self.entry_x.delete(0, "end")
        self.entry_x.insert(0, str(x))
        self.entry_y.delete(0, "end")
        self.entry_y.insert(0, str(y))
        self._append_log(f"Ponto capturado com sucesso: X={x}, Y={y}")
        self._set_next_step("teste 1 clique e, se ok, inicie o autoclick.")
        if self._test_after_capture:
            self._test_after_capture = False
            self._test_single_click()

    def _on_close(self):
        self.clicker.shutdown()
        self.root.destroy()

def main():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
