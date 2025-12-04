import http.client
import socket
import time

import pytest

from preview_server import _start_camera_preview, _start_preview_server


def _get_free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_preview_server_range_and_index(tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"0123456789")

    port = _get_free_port()
    server, thread = _start_preview_server(str(video), "127.0.0.1", port)

    try:
        time.sleep(0.1)

        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/video", headers={"Range": "bytes=2-5"})
        resp = conn.getresponse()
        payload = resp.read()
        conn.close()

        assert resp.status == 206
        assert payload == b"2345"

        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/")
        resp = conn.getresponse()
        html = resp.read().decode()
        conn.close()

        assert resp.status == 200
        assert "wan-preview" in html
    finally:
        server.shutdown()
        thread.join(timeout=1)


def test_camera_preview_streams_frames():
    frame = b"\xff\xd8\xff\xdb\x00C"  # minimal JPEG header chunk

    def supplier():
        return frame

    port = _get_free_port()
    server, thread, producer = _start_camera_preview("127.0.0.1", port, frame_supplier=supplier, fps=10)

    try:
        time.sleep(0.2)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        conn.request("GET", "/stream")
        resp = conn.getresponse()
        chunk = resp.read(128)
        conn.close()

        assert resp.status == 200
        assert b"--frame" in chunk
        assert frame in chunk
    finally:
        server.shutdown()
        producer.stop()
        thread.join(timeout=1)
