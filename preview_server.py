import base64
import logging
import os
import re
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import argparse

_JPEG_PLACEHOLDER = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDABALDA4MChAODQ4SERATGCgaGBYWGy0aICQc"
    "HyA1JjQnKC0xNDY6Qjk+PkJGSDY6QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBA"
    "QEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAALCAABAAEBAREA/8QAFQABAQAA"
    "AAAAAAAAAAAAAAAAAP/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFQEBAQAAAAAAAAAA"
    "AAAAAAAAAAAgP/xAAUEQEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIRAxEAPwCf/9k="
)


class _FrameProducer:
    def __init__(self, frame_supplier, fps: float = 24.0):
        self.frame_supplier = frame_supplier
        self.fps = max(fps, 0.1)
        self.latest_frame = _JPEG_PLACEHOLDER
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        delay = 1.0 / self.fps
        while not self._stop_event.is_set():
            frame = self.frame_supplier()
            if frame:
                self.latest_frame = frame
            time.sleep(delay)

    def get_latest(self):
        return self.latest_frame

    def stop(self):
        self._stop_event.set()
        if hasattr(self.frame_supplier, "close"):
            try:
                self.frame_supplier.close()
            except Exception:
                logging.exception("Failed to close frame supplier")
        self._thread.join(timeout=1)


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


class _CameraRequestHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, frame_producer=None, **kwargs):
        self.frame_producer = frame_producer
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):
        logging.info("[preview] " + format % args)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self._serve_index()
        if self.path.startswith("/stream"):
            return self._serve_stream()
        return super().do_GET()

    def _serve_index(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        html = """
        <!doctype html>
        <html lang='en'>
        <head>
            <meta charset='utf-8'/>
            <title>Wan 2.2 Camera Preview</title>
            <style>body { margin:0; background:#111; display:flex; justify-content:center; align-items:center; height:100vh; }</style>
        </head>
        <body>
            <img id="wan-camera" src="/stream" style="max-width:100%; max-height:100%;" />
        </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def _serve_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            while True:
                frame = self.frame_producer.get_latest()
                if frame:
                    header = (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                    )
                    self.wfile.write(header)
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                time.sleep(1.0 / self.frame_producer.fps)
        except (BrokenPipeError, ConnectionResetError):
            return


def _start_preview_server(video_path: str, host: str, port: int):
    directory = os.path.abspath(os.path.dirname(video_path)) or "."
    handler = partial(_VideoRequestHandler, directory=directory, video_filename=os.path.basename(video_path))
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


class _Cv2Camera:
    def __init__(self, camera_index: int = 0, width: int | None = None, height: int | None = None):
        import cv2

        self.cap = cv2.VideoCapture(camera_index)
        if width:
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

    def __call__(self):
        ok, frame = self.cap.read()
        if not ok:
            return None
        import cv2

        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            return None
        return encoded.tobytes()

    def close(self):
        self.cap.release()


def _start_camera_preview(host: str, port: int, camera_index: int = 0, width: int | None = None, height: int | None = None, fps: float = 24.0, frame_supplier=None):
    supplier = frame_supplier or _Cv2Camera(camera_index=camera_index, width=width, height=height)
    producer = _FrameProducer(supplier, fps=fps)
    handler = partial(_CameraRequestHandler, frame_producer=producer)
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread, producer


__all__ = ["_start_preview_server", "_start_camera_preview"]


def main():
    parser = argparse.ArgumentParser(description="Lightweight Wan preview server utilities")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default: 127.0.0.1 for local-only access)")
    parser.add_argument("--port", type=int, default=17861, help="Port to serve preview on")
    parser.add_argument(
        "--camera",
        action="store_true",
        help="Capture from a USB camera and stream as MJPEG (useful for Streamlabs smoke tests)",
    )
    parser.add_argument("--camera-index", type=int, default=0, help="OpenCV camera index to read from")
    parser.add_argument("--width", type=int, default=None, help="Optional capture width")
    parser.add_argument("--height", type=int, default=None, help="Optional capture height")
    parser.add_argument("--fps", type=float, default=24.0, help="Target capture FPS")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.camera:
        server, thread, producer = _start_camera_preview(
            host=args.host,
            port=args.port,
            camera_index=args.camera_index,
            width=args.width,
            height=args.height,
            fps=args.fps,
        )
        logging.info("[preview] Camera preview ready at http://%s:%s/ (local-only unless host overridden)", args.host, args.port)
        try:
            while thread.is_alive():
                time.sleep(0.5)
        except KeyboardInterrupt:
            logging.info("[preview] Shutting down camera preview")
        finally:
            server.shutdown()
            producer.stop()
            thread.join(timeout=1)
    else:
        parser.error("No mode selected: use --camera for USB capture streaming")


if __name__ == "__main__":
    main()
