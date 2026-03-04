import ctypes
import os
import threading
import time
import tkinter as tk
from tkinter import messagebox

import win32api
import win32con
import win32gui
import win32process


APP_NAME = "Merge Tales AutoClicker"
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AUTO_HINTS = ("merge tales", "mergetales", "google play games")


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
        self.mode = "postmessage"

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
        mode: str,
    ):
        self.click_x = click_x
        self.click_y = click_y
        self.interval_seconds = interval_seconds
        self.target_hwnd = target_hwnd
        self.target_pid = target_pid
        # Modo fixo em background sem troca de foco.
        self.mode = "postmessage"

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
        # Prioriza a janela travada (HWND) para manter clique apenas no alvo escolhido.
        if self.target_hwnd and win32gui.IsWindow(self.target_hwnd):
            _, pid = win32process.GetWindowThreadProcessId(self.target_hwnd)
            if not self.target_pid or pid == self.target_pid:
                title = win32gui.GetWindowText(self.target_hwnd) or f"hwnd={self.target_hwnd}"
                return self.target_hwnd, title

        # Fallback: encontra novamente por PID se o HWND mudou/recriou.
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
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("900x690")
        self.root.resizable(False, False)

        self.clicker = WindowClicker()
        self.clicker.on_log = self._append_log
        self.clicker.on_state = self._on_clicker_state

        self.windows = []
        self.selected_pid = 0
        self.selected_hwnd = 0
        self._capturing = False

        self.window_var = tk.StringVar(value="")
        self.status_var = tk.StringVar(value="PARADO")
        self.target_var = tk.StringVar(value="Alvo: nenhum")

        self._build_ui()
        self._refresh_windows()
        self._bind_shortcuts()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        root = self.root
        main = tk.Frame(root, padx=12, pady=10)
        main.pack(fill="both", expand=True)

        header = tk.Frame(main)
        header.pack(fill="x", pady=(0, 8))
        tk.Label(header, text=APP_NAME, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(header, textvariable=self.status_var, font=("Segoe UI", 11, "bold"), fg="#155724").pack(side="right")

        tk.Label(main, textvariable=self.target_var, anchor="w", fg="#2b4a7f").pack(fill="x", pady=(0, 8))
        tk.Label(
            main,
            text="Modo fixo: PostMessage em background (sem trocar foco de janela).",
            anchor="w",
            fg="#555555",
        ).pack(fill="x", pady=(0, 10))

        process_box = tk.LabelFrame(main, text="Processo / Janela", padx=8, pady=8)
        process_box.pack(fill="x", pady=(0, 10))

        row = tk.Frame(process_box)
        row.pack(fill="x")
        self.window_menu = tk.OptionMenu(row, self.window_var, "")
        self.window_menu.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="Atualizar", width=12, command=self._refresh_windows).pack(side="left", padx=(8, 0))
        self.window_var.trace_add("write", lambda *_: self._on_window_selected())

        point_box = tk.LabelFrame(main, text="Ponto e Intervalo", padx=8, pady=8)
        point_box.pack(fill="x", pady=(0, 10))

        form = tk.Frame(point_box)
        form.pack(fill="x")
        tk.Label(form, text="X").pack(side="left")
        self.entry_x = tk.Entry(form, width=8)
        self.entry_x.insert(0, "500")
        self.entry_x.pack(side="left", padx=(6, 12))
        tk.Label(form, text="Y").pack(side="left")
        self.entry_y = tk.Entry(form, width=8)
        self.entry_y.insert(0, "350")
        self.entry_y.pack(side="left", padx=(6, 12))
        tk.Label(form, text="Intervalo (s)").pack(side="left")
        self.entry_interval = tk.Entry(form, width=8)
        self.entry_interval.insert(0, "0.2")
        self.entry_interval.pack(side="left", padx=(6, 0))

        mode_box = tk.LabelFrame(main, text="Modo de Clique", padx=8, pady=8)
        mode_box.pack(fill="x", pady=(0, 10))

        tk.Label(
            mode_box,
            text="PostMessage (background): clique somente na janela/processo selecionado, sem alternar foco.",
            anchor="w",
            justify="left",
        ).pack(fill="x")

        action_box = tk.LabelFrame(main, text="Acoes", padx=8, pady=8)
        action_box.pack(fill="x", pady=(0, 10))

        self.btn_validate = tk.Button(action_box, text="Validar", width=12, command=self._validate_target)
        self.btn_validate.pack(side="left")
        self.btn_capture = tk.Button(action_box, text="Capturar", width=12, command=self._capture_point)
        self.btn_capture.pack(side="left", padx=(8, 0))
        self.btn_test = tk.Button(action_box, text="Teste", width=12, command=self._test_click)
        self.btn_test.pack(side="left", padx=(8, 0))
        self.btn_start = tk.Button(action_box, text="Iniciar", width=12, command=self._start)
        self.btn_start.pack(side="left", padx=(8, 0))
        self.btn_stop = tk.Button(action_box, text="Parar", width=12, state="disabled", command=self._stop)
        self.btn_stop.pack(side="left", padx=(8, 0))

        log_box = tk.LabelFrame(main, text="Log", padx=8, pady=8)
        log_box.pack(fill="both", expand=True)
        self.log = tk.Text(log_box, height=12, state="disabled")
        self.log.pack(fill="both", expand=True)

        self._append_log("Atalhos: F6 teste | F7 iniciar | F8 parar | F9 capturar")
        self._append_log("Sem argumentos de linha de comando. Basta executar: python clicker.py")

    def _bind_shortcuts(self):
        self.root.bind("<F6>", lambda _e: self._test_click())
        self.root.bind("<F7>", lambda _e: self._start())
        self.root.bind("<F8>", lambda _e: self._stop())
        self.root.bind("<F9>", lambda _e: self._capture_point())

    def _append_log(self, text: str):
        self.root.after(0, self._append_log_ui, text)

    def _append_log_ui(self, text: str):
        self.log.config(state="normal")
        self.log.insert("end", f"{time.strftime('%H:%M:%S')} - {text}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _set_running_ui(self, running: bool):
        self.status_var.set("RODANDO" if running else "PARADO")
        self.btn_start.configure(state="disabled" if running else "normal")
        self.btn_stop.configure(state="normal" if running else "disabled")
        self.btn_test.configure(state="disabled" if running else "normal")
        self.btn_validate.configure(state="disabled" if running else "normal")
        self.btn_capture.configure(state="disabled" if running else "normal")

    def _on_clicker_state(self, running: bool):
        self.root.after(0, lambda: self._set_running_ui(running))

    def _toggle_run(self):
        if self.status_var.get() == "RODANDO":
            self._stop()
        else:
            self._start()

    def _refresh_windows(self):
        previous_pid = self.selected_pid
        self.windows = list_selectable_windows()

        menu = self.window_menu["menu"]
        menu.delete(0, "end")

        if not self.windows:
            self.window_var.set("")
            self.selected_pid = 0
            self.selected_hwnd = 0
            self.target_var.set("Alvo: nenhum")
            self._append_log("Nenhuma janela selecionavel encontrada.")
            return

        for win in self.windows:
            menu.add_command(label=win["display"], command=lambda value=win["display"]: self.window_var.set(value))

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
        self._append_log(f"Lista atualizada: {len(self.windows)} janela(s).")

    def _on_window_selected(self):
        selected_display = self.window_var.get().strip()
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
        self._append_log(f"Selecionado: {selected['display']}")

    def _resolve_target_window(self):
        if self.selected_pid:
            hwnd, title = find_window_by_pid(self.selected_pid)
            if hwnd:
                self.selected_hwnd = hwnd
                return hwnd, title
        if self.selected_hwnd and win32gui.IsWindow(self.selected_hwnd):
            return self.selected_hwnd, win32gui.GetWindowText(self.selected_hwnd)
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
            return

        self.entry_x.delete(0, "end")
        self.entry_x.insert(0, str(x))
        self.entry_y.delete(0, "end")
        self.entry_y.insert(0, str(y))
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
            mode="postmessage",
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
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
