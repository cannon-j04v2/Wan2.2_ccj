import logging
import os
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _VideoRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, directory=None, video_filename=None, **kwargs):
        self.video_filename = video_filename
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format, *args):
        logging.info("[preview] " + format % args)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._serve_index()
        if self.path.startswith("/video"):
            return self._serve_video()
        return super().do_GET()

    def _serve_index(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        html = f"""
        <!doctype html>
        <html lang='en'>
        <head>
            <meta charset='utf-8'/>
            <title>Wan 2.2 Preview</title>
            <style>body {{ margin:0; background:#111; display:flex; justify-content:center; align-items:center; height:100vh; }}</style>
        </head>
        <body>
            <video id="wan-preview" src="/video" autoplay loop muted controls playsinline style=\"max-width:100%; max-height:100%;\"></video>
            <script>document.getElementById('wan-preview').addEventListener('loadeddata',()=>console.log('Preview ready'));</script>
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def _serve_video(self):
        path = self.translate_path(self.video_filename)
        if not os.path.isfile(path):
            self.send_error(404, "Video not found")
            return
        file_size = os.path.getsize(path)
        range_header = self.headers.get("Range")
        if range_header:
            m = re.match(r"bytes=(\d+)-(\d*)", range_header)
            if m:
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else file_size - 1
            else:
                start, end = 0, file_size - 1
            start = min(start, file_size - 1)
            end = min(end, file_size - 1)
            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                chunk = 64 * 1024
                while remaining > 0:
                    data = f.read(min(chunk, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Length", str(file_size))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(64 * 1024), b""):
                    self.wfile.write(chunk)


def _start_preview_server(video_path: str, host: str, port: int):
    directory = os.path.abspath(os.path.dirname(video_path)) or "."
    handler = partial(_VideoRequestHandler, directory=directory, video_filename=os.path.basename(video_path))
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


__all__ = ["_start_preview_server"]
