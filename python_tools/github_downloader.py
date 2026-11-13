"""GitHub file/folder downloader. Python 3.10+; pip install requests."""
import hashlib
import os
from pathlib import Path
import queue
import re
import shutil
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk
from urllib.parse import quote, unquote, urlsplit

import requests


class Cancelled(Exception):
    pass


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def parse_link(link):
    u = urlsplit(link.strip())
    if u.scheme != 'https' or u.hostname != 'github.com' or u.username or u.port:
        raise ValueError('Nhập link https://github.com/OWNER/REPO/tree/BRANCH/FOLDER hoặc /blob/BRANCH/FILE.')
    p = [unquote(x) for x in u.path.strip('/').split('/')]
    if len(p) < 2 or not all(p[:2]):
        raise ValueError('Link thiếu chủ sở hữu hoặc tên repo.')
    owner, repo = p[:2]
    repo = repo.removesuffix('.git')
    if not re.fullmatch(r'[\w.-]+', owner) or not re.fullmatch(r'[\w.-]+', repo):
        raise ValueError('Tên chủ sở hữu/repo không hợp lệ.')
    if len(p) == 2:
        return owner, repo, '', ''
    if len(p) < 4 or p[2] not in ('tree', 'blob'):
        raise ValueError('Chỉ hỗ trợ link repo, thư mục /tree/ hoặc file /blob/.')
    return owner, repo, p[2], '/'.join(p[3:])


def safe_path(root, relative):
    parts = relative.split('/')
    for part in parts:
        if (not part or part in ('.', '..') or any(ord(c) < 32 for c in part)
                or any(c in part for c in '\\:*?"<>|') or part.endswith((' ', '.'))
                or re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part, re.I)):
            raise ValueError(f'Tên file không an toàn hoặc không tương thích Windows: {relative}')
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Đường dẫn vượt ngoài thư mục lưu.')
    current = target
    while current != root:
        if current.is_symlink():
            raise ValueError('Không ghi qua symbolic link có sẵn.')
        current = current.parent
    return target


def save_without_overwrite(temp, target):
    """Save in the selected directory, adding a suffix if the name exists."""
    original = target
    number = 0
    while True:
        try:
            handle = target.open('xb')
            break
        except FileExistsError:
            number += 1
            target = original.with_name(f'{original.stem} ({number}){original.suffix}')
    try:
        with handle, open(temp, 'rb') as source:
            shutil.copyfileobj(source, handle)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return target


class Downloader:
    def __init__(self, token, stop, emit):
        self.stop, self.emit = stop, emit
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Tk-GitHub-Downloader', 'Accept': 'application/vnd.github+json'})
        if token:
            self.session.headers['Authorization'] = f'Bearer {token}'

    def check(self):
        if self.stop.is_set():
            raise Cancelled()

    def request(self, endpoint, raw=False):
        for attempt in range(3):
            self.check()
            try:
                response = self.session.get('https://api.github.com' + endpoint,
                    headers={'Accept': 'application/vnd.github.raw+json' if raw else 'application/vnd.github+json'},
                    timeout=(10, 30), stream=raw)
            except requests.RequestException:
                if attempt == 2:
                    raise RuntimeError('Lỗi kết nối mạng sau 3 lần thử.') from None
                if self.stop.wait(2 ** attempt):
                    raise Cancelled()
                continue
            if response.status_code >= 500 and attempt < 2:
                response.close()
                if self.stop.wait(2 ** attempt):
                    raise Cancelled()
                continue
            if not response.ok:
                status = response.status_code
                reset = response.headers.get('X-RateLimit-Reset', '')
                response.close()
                hints = {401: 'Token không hợp lệ hoặc đã hết hạn.',
                         403: 'Thiếu quyền hoặc vượt giới hạn API. Thử token có quyền đọc repo.',
                         404: 'Không tìm thấy repo/ref/path hoặc token chưa có quyền truy cập.',
                         429: 'GitHub giới hạn yêu cầu. Thử lại sau.'}
                raise ApiError(status, f'HTTP {status}: ' + hints.get(status, 'GitHub từ chối yêu cầu.') +
                               (f' Rate-limit reset (Unix): {reset}' if status in (403, 429) and reset else ''))
            return response

    def json(self, endpoint):
        with self.request(endpoint) as response:
            return response.json()

    def resolve(self, link, override):
        owner, repo, kind, tail = parse_link(link)
        self.base = f'/repos/{quote(owner)}/{quote(repo)}'
        if not kind:
            ref = override or self.json(self.base)['default_branch']
            commit = self.json(self.base + '/commits/' + quote(ref, safe=''))
            return repo, '', commit['commit']['tree']['sha']
        if override:
            if not (tail == override or tail.startswith(override + '/')):
                raise ValueError('Branch/tag nhập tay phải khớp phần đầu sau /tree/ hoặc /blob/ trong link.')
            refs = [override]
        else:
            # Prefer longest valid ref, including branch names containing '/'.
            segments = tail.split('/')
            refs = ['/'.join(segments[:i]) for i in range(len(segments), 0, -1)]
        for ref in refs:
            self.check()
            try:
                commit = self.json(self.base + '/commits/' + quote(ref, safe=''))
            except ApiError as exc:
                if exc.status in (404, 422) and not override:
                    continue
                raise
            path = tail[len(ref):].lstrip('/')
            return repo, path, commit['commit']['tree']['sha']
        raise ValueError('Không xác định được branch/tag. Kiểm tra link và ô branch/tag.')

    def tree(self, sha):
        result = self.json(self.base + '/git/trees/' + sha)
        if result.get('truncated'):
            raise RuntimeError('GitHub trả danh sách bị cắt; dừng để tránh tải thiếu mà báo thành công.')
        return result['tree']

    def run(self, link, override, destination):
        repo, path, tree_sha = self.resolve(link, override)
        node = {'type': 'tree', 'sha': tree_sha, 'mode': '040000'}
        if path:
            for component in path.split('/'):
                if node['type'] != 'tree':
                    raise ValueError('Đường dẫn đi qua một file.')
                node = next((n for n in self.tree(node['sha']) if n['path'] == component), None)
                if node is None:
                    raise ValueError('Không tìm thấy file/thư mục trong ref đã chọn.')
        self.emit('log', 'Đang lập danh sách file…')
        name = path.rsplit('/', 1)[-1] if path else repo
        stack, files, skipped = [(node, name)], [], 0
        while stack:
            self.check()
            entry, rel = stack.pop()
            safe_path(destination, rel)
            if entry['type'] == 'tree':
                stack.extend((child, rel + '/' + child['path']) for child in self.tree(entry['sha']))
            elif entry['type'] == 'blob' and entry['mode'] != '120000':
                files.append((entry, rel))
            else:
                skipped += 1
                self.emit('log', 'Bỏ qua symbolic link/submodule: ' + rel)
        # Single files go directly into the selected folder.
        single_file = node['type'] == 'blob' and node['mode'] != '120000'
        output = destination if single_file else Path(tempfile.mkdtemp(prefix='github_download_', dir=destination))
        self.emit('log', 'Nơi lưu: ' + str(output))
        self.emit('progress', (0, len(files)))
        failed = 0
        for index, (entry, rel) in enumerate(files, 1):
            self.check()
            target = safe_path(output, rel)
            temp = None
            try:
                if entry.get('size', 0) > 100 * 1024 * 1024:
                    raise ValueError('Git blob vượt 100 MiB; dùng Git/Git LFS cho file này.')
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temp = tempfile.mkstemp(prefix='.download_', dir=target.parent)
                total = 0
                digest = hashlib.sha1(f"blob {entry['size']}\0".encode())
                prefix = b''
                with os.fdopen(fd, 'wb') as handle, self.request(self.base + '/git/blobs/' + entry['sha'], raw=True) as response:
                    for chunk in response.iter_content(128 * 1024):
                        self.check()
                        if chunk:
                            if len(prefix) < 200:
                                prefix += chunk[:200 - len(prefix)]
                            handle.write(chunk)
                            total += len(chunk)
                            digest.update(chunk)
                if total != entry['size'] or digest.hexdigest() != entry['sha']:
                    raise ValueError('Nội dung tải về không khớp kích thước/SHA của GitHub.')
                if single_file:
                    target = save_without_overwrite(temp, target)
                    Path(temp).unlink()
                else:
                    os.replace(temp, target)
                temp = None
                if prefix.startswith(b'version https://git-lfs.github.com/spec/v1'):
                    self.emit('log', 'LƯU Ý: chỉ tải Git LFS pointer, chưa tải nội dung LFS: ' + rel)
                    skipped += 1
                self.emit('log', 'Đã tải: ' + str(target.relative_to(output)))
            except Cancelled:
                raise
            except (OSError, ValueError, RuntimeError, ApiError, requests.RequestException) as exc:
                failed += 1
                self.emit('log', f'LỖI {rel}: {exc}')
                if isinstance(exc, ApiError) and exc.status in (401, 403, 429):
                    raise
            finally:
                if temp:
                    Path(temp).unlink(missing_ok=True)
            self.emit('progress', (index, len(files)))
        return f'Đã lưu {len(files) - failed}/{len(files)} file. Lỗi: {failed}. Bỏ qua/LFS pointer: {skipped}.\n{output}'


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('GitHub Downloader — tải file / thư mục')
        self.geometry('850x590')
        self.minsize(700, 480)
        self.events, self.stop = queue.Queue(), threading.Event()
        self.running = False
        frame = ttk.Frame(self, padding=16)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(1, weight=1)
        self.link, self.ref, self.token, self.dest = [tk.StringVar() for _ in range(4)]
        for row, (label, var) in enumerate([
            ('Link GitHub:', self.link), ('Branch/tag (tùy chọn):', self.ref),
            ('Token (tùy chọn):', self.token), ('Thư mục lưu:', self.dest)]):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', pady=6)
            ttk.Entry(frame, textvariable=var, show='*' if row == 2 else '').grid(row=row, column=1, sticky='ew', padx=8)
        ttk.Button(frame, text='Chọn folder…', command=self.browse).grid(row=3, column=2)
        ttk.Label(frame, text='Dán link /blob/ để tải file, /tree/ để tải folder; link repo để tải toàn bộ.\nTải file: lưu trực tiếp vào nơi đã chọn; trùng tên sẽ thêm (1), (2)…', wraplength=760).grid(row=4, column=0, columnspan=3, sticky='w', pady=10)
        bar = ttk.Frame(frame)
        bar.grid(row=5, column=0, columnspan=3, sticky='w')
        self.start_button = ttk.Button(bar, text='Tải xuống', command=self.start)
        self.start_button.pack(side='left')
        self.cancel_button = ttk.Button(bar, text='Hủy', command=self.cancel, state='disabled')
        self.cancel_button.pack(side='left', padx=10)
        self.progress = ttk.Progressbar(frame)
        self.progress.grid(row=6, column=0, columnspan=3, sticky='ew', pady=10)
        self.status = tk.StringVar(value='Sẵn sàng')
        ttk.Label(frame, textvariable=self.status).grid(row=7, column=0, columnspan=3, sticky='w')
        self.log = scrolledtext.ScrolledText(frame, height=12, state='disabled', wrap='word')
        self.log.grid(row=8, column=0, columnspan=3, sticky='nsew', pady=8)
        frame.rowconfigure(8, weight=1)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.after(100, self.poll)

    def browse(self):
        folder = filedialog.askdirectory()
        if folder:
            self.dest.set(folder)

    def cancel(self):
        self.stop.set()
        self.status.set('Đang hủy; chờ yêu cầu mạng hiện tại kết thúc…')

    def close(self):
        if self.running:
            self.cancel()
            messagebox.showinfo('Đang hủy', 'Chờ tiến trình dừng rồi đóng cửa sổ. File đã tải vẫn được giữ.')
        else:
            self.destroy()

    def start(self):
        try:
            parse_link(self.link.get())
            destination = Path(self.dest.get()).expanduser().resolve()
            if not self.dest.get().strip() or not destination.is_dir():
                raise ValueError('Hãy chọn thư mục lưu có sẵn.')
        except ValueError as exc:
            messagebox.showerror('Kiểm tra thông tin', str(exc))
            return
        args = (self.link.get().strip(), self.ref.get().strip(), destination)
        token = self.token.get().strip()
        self.stop.clear()
        self.running = True
        self.start_button.config(state='disabled')
        self.cancel_button.config(state='normal')
        self.status.set('Đang xác định repo và đường dẫn…')
        self.progress.config(value=0)
        def worker():
            client = Downloader(token, self.stop, lambda kind, value: self.events.put((kind, value)))
            try:
                result = client.run(*args)
            except Cancelled:
                result = 'Đã hủy. Giữ lại các file tải xong; file tạm được xóa.'
            except Exception as exc:
                result = f'Dừng do lỗi: {exc}'
            finally:
                client.session.close()
            self.events.put(('done', result))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        for _ in range(100):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'progress':
                done, total = value
                self.progress.config(maximum=max(total, 1), value=done)
                self.status.set(f'Đã xử lý {done}/{total} file')
            else:
                self.log.config(state='normal')
                self.log.insert('end', str(value) + '\n')
                self.log.see('end')
                self.log.config(state='disabled')
                if kind == 'done':
                    self.running = False
                    self.start_button.config(state='normal')
                    self.cancel_button.config(state='disabled')
                    self.status.set(str(value).split('\n')[0])
        self.after(100, self.poll)


if __name__ == '__main__':
    App().mainloop()
