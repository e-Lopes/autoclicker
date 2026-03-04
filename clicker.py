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


APP_NAME = "Merge Tales AutoClicker"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AUTO_HINTS = ("merge tales", "mergetales", "google play games")

COLORS = {
    "bg": "#0B1220",
    "card": "#121A2B",
    "card_alt": "#17233A",
    "muted": "#8EA4C8",
    "text": "#E6EEF9",
    "accent": "#2F7BFF",
    "success": "#1FA971",
    "danger": "#E8505B",
}


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


def _matches_hint(title: str, process_name: str, class_name: str, process_path: str) -> bool:
    text = f"{title} {process_name} {class_name} {process_path}".lower()
    return any(hint in text for hint in AUTO_HINTS)


def list_selectable_windows():
    windows = []

    def enum_cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        process_name = get_process_name(pid)
        process_path = get_process_path(pid)
        class_name = ""

        try:
            class_name = win32gui.GetClassName(hwnd)
        except Exception:
            pass

        if not _is_taskbar_like_window(hwnd) and not _matches_hint(title, process_name, class_name, process_path):
            return

        windows.append(
            {
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
                "process": process_name,
                "path": process_path,
                "class_name": class_name,
                "display": f"{title} [{process_name} | PID {pid}]",
            }
        )

    win32gui.EnumWindows(enum_cb, None)
    windows.sort(key=lambda item: item["display"].lower())
    return windows


def find_window_by_pid(target_pid: int):
    if not target_pid:
        return 0, ""

    matches = []

    def enum_cb(hwnd, _):
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        if pid != target_pid:
            return

        title = win32gui.GetWindowText(hwnd).strip()
        if not title:
            return

        process_name = get_process_name(pid)
        process_path = get_process_path(pid)
        class_name = ""
        try:
            class_name = win32gui.GetClassName(hwnd)
        except Exception:
            pass

        keep = _is_taskbar_like_window(hwnd) or _matches_hint(title, process_name, class_name, process_path)
        if keep:
            matches.append((hwnd, title))

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return 0, ""
    return matches[0]


def post_message_click(hwnd: int, x: int, y: int):
    target_hwnd = hwnd
    tx, ty = x, y

    try:
        screen_pos = win32gui.ClientToScreen(hwnd, (x, y))
        child = win32gui.ChildWindowFromPointEx(
            hwnd,
            (x, y),
            win32con.CWP_SKIPDISABLED | win32con.CWP_SKIPINVISIBLE,
        )
        if child and child != hwnd:
            target_hwnd = child
            tx, ty = win32gui.ScreenToClient(child, screen_pos)
    except Exception:
        pass

    point = win32api.MAKELONG(tx, ty)
    win32gui.PostMessage(target_hwnd, win32con.WM_MOUSEMOVE, 0, point)
    win32gui.PostMessage(target_hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, point)
    win32gui.PostMessage(target_hwnd, win32con.WM_LBUTTONUP, 0, point)


class WindowClicker:
    def __init__(self):
        self._running = False
        self._exit_requested = False
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

        self.target_hwnd = 0
        self.target_pid = 0
        self.click_x = 500
        self.click_y = 350
        self.interval_seconds = 0.2

        self.on_log = None
        self.on_state = None
        self._last_logged_hwnd = 0

    def _log(self, message: str):
        if self.on_log:
            self.on_log(message)

    def configure(
        self,
        click_x: int,
        click_y: int,
        interval_seconds: float,
        target_hwnd: int,
        target_pid: int,
    ):
        self.click_x = click_x
        self.click_y = click_y
        self.interval_seconds = interval_seconds
        self.target_hwnd = target_hwnd
        self.target_pid = target_pid

    def start(self):
        with self._lock:
            self._running = True
        if self.on_state:
            self.on_state(True)
        self._log("Autoclick iniciado.")

    def stop(self):
        with self._lock:
            self._running = False
        if self.on_state:
            self.on_state(False)
        self._log("Autoclick parado.")

    def shutdown(self):
        self._exit_requested = True
        self.stop()
        self._thread.join(timeout=1)

    def _resolve_window(self):
        if self.target_hwnd and win32gui.IsWindow(self.target_hwnd):
            _, pid = win32process.GetWindowThreadProcessId(self.target_hwnd)
            if not self.target_pid or pid == self.target_pid:
                title = win32gui.GetWindowText(self.target_hwnd) or f"hwnd={self.target_hwnd}"
                return self.target_hwnd, title

        if self.target_pid:
            hwnd, title = find_window_by_pid(self.target_pid)
            if hwnd:
                self.target_hwnd = hwnd
                return hwnd, title

        return 0, ""

    def click_once(self, hwnd: int, x: int, y: int):
        post_message_click(hwnd, x, y)

    def _loop(self):
        while not self._exit_requested:
            with self._lock:
                running = self._running

            if not running:
                time.sleep(0.05)
                continue

            hwnd, title = self._resolve_window()
            if hwnd == 0:
                self._log("Janela alvo indisponivel. Parando.")
                self.stop()
                continue

            if hwnd != self._last_logged_hwnd:
                self._last_logged_hwnd = hwnd
                self._log(f"Janela alvo: {title}")

            try:
                self.click_once(hwnd, self.click_x, self.click_y)
            except Exception as exc:
                self._log(f"Erro no clique: {exc}")
                self.stop()

            time.sleep(self.interval_seconds)


class App:
    def __init__(self, root: ctk.CTk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("980x760")
        self.root.resizable(False, False)

        self.clicker = WindowClicker()
        self.clicker.on_log = self._append_log
        self.clicker.on_state = self._on_clicker_state

        self.windows = []
        self.selected_pid = 0
        self.selected_hwnd = 0
        self._capturing = False

        self.window_var = tk.StringVar(value="")
        self.target_var = tk.StringVar(value="Alvo: nenhum")
        self.status_var = tk.StringVar(value="PARADO")
        self.step_var = tk.StringVar(value="1) Selecione processo  2) Capture ponto  3) Teste  4) Inicie")

        self._build_ui()
        self._refresh_windows()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        self.root.configure(fg_color=COLORS["bg"])

        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=14)

        header = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        header.pack(fill="x", pady=(0, 10))

        header_left = ctk.CTkFrame(header, fg_color="transparent")
        header_left.pack(side="left", fill="x", expand=True, padx=14, pady=12)
        ctk.CTkLabel(
            header_left,
            text=APP_NAME,
            font=ctk.CTkFont(size=30, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w")
        ctk.CTkLabel(
            header_left,
            text="PostMessage em background (sem troca de foco)",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["muted"],
        ).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(
            header_left,
            textvariable=self.target_var,
            font=ctk.CTkFont(size=13),
            text_color="#9CC4FF",
        ).pack(anchor="w", pady=(6, 0))

        self.status_chip = ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            width=130,
            height=44,
            corner_radius=10,
            fg_color="#2B344A",
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=16, weight="bold"),
        )
        self.status_chip.pack(side="right", padx=12, pady=12)

        self.step_chip = ctk.CTkLabel(
            main,
            textvariable=self.step_var,
            corner_radius=10,
            anchor="w",
            fg_color=COLORS["card_alt"],
            text_color=COLORS["text"],
            font=ctk.CTkFont(size=13, weight="bold"),
        )
        self.step_chip.pack(fill="x", pady=(0, 10), ipady=8)

        process_card = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        process_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            process_card,
            text="PROCESSO / JANELA",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=14, pady=(10, 8))

        process_row = ctk.CTkFrame(process_card, fg_color="transparent")
        process_row.pack(fill="x", padx=14, pady=(0, 12))
        self.cmb_process = ctk.CTkComboBox(
            process_row,
            variable=self.window_var,
            values=[""],
            state="readonly",
            command=self._on_process_selected,
            height=40,
            fg_color="#0F1A2E",
            button_color=COLORS["accent"],
            dropdown_fg_color="#0F1A2E",
        )
        self.cmb_process.pack(side="left", fill="x", expand=True)
        self.btn_refresh = ctk.CTkButton(
            process_row,
            text="Atualizar",
            width=140,
            height=40,
            fg_color=COLORS["accent"],
            hover_color="#2668D6",
            command=self._refresh_windows,
        )
        self.btn_refresh.pack(side="left", padx=(8, 0))

        point_card = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        point_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            point_card,
            text="PONTO E INTERVALO",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=14, pady=(10, 8))

        row = ctk.CTkFrame(point_card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 12))

        ctk.CTkLabel(row, text="X", text_color=COLORS["text"]).pack(side="left")
        self.entry_x = ctk.CTkEntry(row, width=90)
        self.entry_x.insert(0, "500")
        self.entry_x.pack(side="left", padx=(6, 14))

        ctk.CTkLabel(row, text="Y", text_color=COLORS["text"]).pack(side="left")
        self.entry_y = ctk.CTkEntry(row, width=90)
        self.entry_y.insert(0, "350")
        self.entry_y.pack(side="left", padx=(6, 14))

        ctk.CTkLabel(row, text="Intervalo (s)", text_color=COLORS["text"]).pack(side="left")
        self.entry_interval = ctk.CTkEntry(row, width=100)
        self.entry_interval.insert(0, "0.2")
        self.entry_interval.pack(side="left", padx=(8, 0))

        mode_card = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        mode_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            mode_card,
            text="MODO",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=14, pady=(10, 4))
        ctk.CTkLabel(
            mode_card,
            text="Background fixo em PostMessage: sem alternar foco da janela de trabalho.",
            text_color=COLORS["muted"],
            font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=14, pady=(0, 10))

        action_card = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        action_card.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(
            action_card,
            text="AÇÕES",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        ).pack(anchor="w", padx=14, pady=(10, 8))

        actions = ctk.CTkFrame(action_card, fg_color="transparent")
        actions.pack(fill="x", padx=14, pady=(0, 12))

        self.btn_validate = ctk.CTkButton(actions, text="VALIDAR", width=140, height=64, command=self._validate_target)
        self.btn_validate.pack(side="left")

        self.btn_capture = ctk.CTkButton(
            actions,
            text="CAPTURAR",
            width=140,
            height=64,
            fg_color=COLORS["accent"],
            hover_color="#2668D6",
            command=self._capture_point,
        )
        self.btn_capture.pack(side="left", padx=(8, 0))

        self.btn_test = ctk.CTkButton(actions, text="TESTE", width=140, height=64, command=self._test_click)
        self.btn_test.pack(side="left", padx=(8, 0))

        self.btn_start = ctk.CTkButton(
            actions,
            text="INICIAR",
            width=140,
            height=64,
            fg_color=COLORS["success"],
            hover_color="#16835A",
            command=self._start,
        )
        self.btn_start.pack(side="left", padx=(8, 0))

        self.btn_stop = ctk.CTkButton(
            actions,
            text="PARAR",
            width=140,
            height=64,
            fg_color=COLORS["danger"],
            hover_color="#C63D49",
            state="disabled",
            command=self._stop,
        )
        self.btn_stop.pack(side="left", padx=(8, 0))

        log_card = ctk.CTkFrame(main, corner_radius=14, fg_color=COLORS["card"])
        log_card.pack(fill="both", expand=True)

        log_top = ctk.CTkFrame(log_card, fg_color="transparent")
        log_top.pack(fill="x", padx=14, pady=(10, 8))
        ctk.CTkLabel(
            log_top,
            text="LOG",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=COLORS["text"],
        ).pack(side="left")
        self.btn_clear_log = ctk.CTkButton(log_top, text="Limpar", width=110, height=34, command=self._clear_log)
        self.btn_clear_log.pack(side="right")

        self.log = ctk.CTkTextbox(
            log_card,
            height=240,
            fg_color="#0A1222",
            text_color=COLORS["text"],
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self.log.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.log.configure(state="disabled")

        self._append_log("Atalhos: F6 teste | F7 iniciar | F8 parar | F9 capturar")
        self._append_log("Executar: python clicker.py")

    def _bind_shortcuts(self):
        self.root.bind("<F6>", lambda _e: self._test_click())
        self.root.bind("<F7>", lambda _e: self._start())
        self.root.bind("<F8>", lambda _e: self._stop())
        self.root.bind("<F9>", lambda _e: self._capture_point())

    def _append_log(self, text: str):
        self.root.after(0, self._append_log_ui, text)

    def _append_log_ui(self, text: str):
        self.log.configure(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} - {text}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")
        self._append_log("Log limpo.")

    def _set_running_ui(self, running: bool):
        self.status_var.set("RODANDO" if running else "PARADO")
        self.status_chip.configure(fg_color=COLORS["success"] if running else "#2B344A")

        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_test.configure(state="disabled" if running else "normal")
        self.btn_validate.configure(state="disabled" if running else "normal")
        self.btn_capture.configure(state="disabled" if running else ("disabled" if self._capturing else "normal"))
        self.btn_refresh.configure(state="disabled" if running else "normal")

        if running:
            self.step_var.set("Rodando em background (PostMessage)")
        else:
            self.step_var.set("1) Selecione processo  2) Capture ponto  3) Teste  4) Inicie")

    def _on_clicker_state(self, running: bool):
        self.root.after(0, lambda: self._set_running_ui(running))

    def _refresh_windows(self):
        previous_pid = self.selected_pid
        self.windows = list_selectable_windows()

        if not self.windows:
            self.window_var.set("")
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.target_var.set("Alvo: nenhum")
            self.cmb_process.configure(values=[""])
            self._append_log("Nenhuma janela selecionavel encontrada.")
            return

        values = [win["display"] for win in self.windows]
        self.cmb_process.configure(values=values)

        idx = 0
        if previous_pid:
            for i, option in enumerate(self.windows):
                if option["pid"] == previous_pid:
                    idx = i
                    break
        else:
            for i, option in enumerate(self.windows):
                joined = f"{option['title']} {option['process']}".lower()
                if any(hint in joined for hint in AUTO_HINTS):
                    idx = i
                    break

        selected = self.windows[idx]
        self.window_var.set(selected["display"])
        self._on_process_selected(selected["display"])
        self._append_log(f"Lista atualizada: {len(self.windows)} janela(s).")

    def _on_process_selected(self, selected_display: str):
        selected = None
        for option in self.windows:
            if option["display"] == selected_display:
                selected = option
                break

        if not selected:
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.target_var.set("Alvo: nenhum")
            return

        self.selected_pid = selected["pid"]
        self.selected_hwnd = selected["hwnd"]
        self.target_var.set(f"Alvo: {selected['title']} [{selected['process']} | PID {selected['pid']}]")
        self.step_var.set("Valide o alvo e capture o ponto")
        self._append_log(f"Selecionado: {selected['display']}")

    def _resolve_target_window(self):
        if self.selected_pid:
            hwnd, title = find_window_by_pid(self.selected_pid)
            if hwnd:
                self.selected_hwnd = hwnd
                return hwnd, title
        if self.selected_hwnd and win32gui.IsWindow(self.selected_hwnd):
            title = win32gui.GetWindowText(self.selected_hwnd) or f"hwnd={self.selected_hwnd}"
            return self.selected_hwnd, title
        return 0, ""

    def _read_values(self):
        try:
            x = int(self.entry_x.get().strip())
            y = int(self.entry_y.get().strip())
            interval = float(self.entry_interval.get().strip())
            return True, x, y, interval
        except ValueError:
            messagebox.showerror("Erro", "X, Y e intervalo precisam ser numeros validos.")
            return False, 0, 0, 0.0

    def _validate_target(self):
        hwnd, title = self._resolve_target_window()
        if hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo.")
            return

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        self.selected_pid = pid
        self.selected_hwnd = hwnd
        self.target_var.set(f"Alvo: {title} [PID {pid}]")
        self.step_var.set("Capture e teste o ponto")
        self._append_log(f"Alvo validado: {title} (HWND={hwnd}, PID={pid})")

    def _capture_point(self):
        if self._capturing:
            return

        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Selecione/valide um alvo antes de capturar.")
            return

        self._capturing = True
        self.btn_capture.configure(state="disabled")
        self.step_var.set("Clique no ponto dentro do jogo (20s)")
        self._append_log(f"Captura iniciada (sem troca de foco). Clique no ponto dentro de: {target_title}")

        worker = threading.Thread(target=self._capture_worker, args=(target_hwnd,), daemon=True)
        worker.start()

    def _capture_worker(self, target_hwnd: int):
        try:
            deadline = time.time() + 20
            was_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)

            while time.time() < deadline:
                is_down = bool(win32api.GetAsyncKeyState(win32con.VK_LBUTTON) & 0x8000)
                if is_down and not was_down:
                    pos = win32api.GetCursorPos()
                    clicked_hwnd = win32gui.WindowFromPoint(pos)
                    inside = clicked_hwnd == target_hwnd or win32gui.IsChild(target_hwnd, clicked_hwnd)

                    if inside:
                        client_x, client_y = win32gui.ScreenToClient(target_hwnd, pos)
                        self.root.after(0, self._capture_done, True, client_x, client_y, "")
                        return
                    self._append_log("Clique fora da janela alvo. Aguardando...")

                was_down = is_down
                time.sleep(0.01)

            self.root.after(0, self._capture_done, False, 0, 0, "Tempo de captura esgotado.")
        except Exception as exc:
            self.root.after(0, self._capture_done, False, 0, 0, f"Falha na captura: {exc}")

    def _capture_done(self, ok: bool, x: int, y: int, error: str):
        self._capturing = False
        if self.status_var.get() != "RODANDO":
            self.btn_capture.configure(state="normal")

        if not ok:
            self._append_log(error)
            messagebox.showerror("Erro", error)
            self.step_var.set("Capture novamente")
            return

        self.entry_x.delete(0, "end")
        self.entry_x.insert(0, str(x))
        self.entry_y.delete(0, "end")
        self.entry_y.insert(0, str(y))
        self.step_var.set("Teste o clique e depois inicie")
        self._append_log(f"Ponto capturado: X={x}, Y={y}")

    def _apply_clicker_config(self):
        read_ok, x, y, interval = self._read_values()
        if not read_ok:
            return False
        if interval <= 0:
            messagebox.showerror("Erro", "Intervalo precisa ser maior que 0.")
            return False

        target_hwnd, target_title = self._resolve_target_window()
        if target_hwnd == 0:
            messagebox.showerror("Erro", "Nao encontrei a janela alvo.")
            return False

        _, target_pid = win32process.GetWindowThreadProcessId(target_hwnd)
        self.selected_pid = target_pid
        self.selected_hwnd = target_hwnd

        self.clicker.configure(
            click_x=x,
            click_y=y,
            interval_seconds=interval,
            target_hwnd=target_hwnd,
            target_pid=target_pid,
        )

        self._append_log(
            f"Config aplicado: alvo='{target_title}', PID={target_pid}, X={x}, Y={y}, int={interval}, modo=postmessage"
        )
        return True

    def _test_click(self):
        if not self._apply_clicker_config():
            return

        try:
            self.clicker.click_once(self.clicker.target_hwnd, self.clicker.click_x, self.clicker.click_y)
            self.step_var.set("Se funcionou, clique em INICIAR")
            self._append_log("Teste enviado.")
        except Exception as exc:
            messagebox.showerror("Erro", f"Falha no teste: {exc}")

    def _start(self):
        if not self._apply_clicker_config():
            return
        self.clicker.start()

    def _stop(self):
        self.clicker.stop()

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

