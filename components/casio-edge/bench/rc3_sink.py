#!/usr/bin/env python3
"""Stand-in RC3 endpoint: accept cloud_relay's POSTs and keep what it sent.

  rc3_sink.py --out DIR [--bind 0.0.0.0] [--port 3030]

cloud_relay posts multipart/form-data: a JSON metadata text field and the
JPEG frame. Each POST becomes DIR/posts/NNNN.json (the metadata plus the
arrival time) and DIR/posts/NNNN.jpg; every answer is 200 so the relay keeps
posting as it would to a healthy RC3. Standard library only.
"""
import argparse
import json
import os
import time
from email.parser import BytesParser
from email.policy import HTTP
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=3030)
    a = ap.parse_args()
    posts = os.path.join(a.out, "posts")
    os.makedirs(posts, exist_ok=True)
    count = [0]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            count[0] += 1
            stem = os.path.join(posts, f"{count[0]:04d}")
            record = {"wall": time.time(), "path": self.path, "bytes": len(body)}
            msg = BytesParser(policy=HTTP).parsebytes(
                b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + body)
            for part in msg.iter_parts() if msg.is_multipart() else []:
                data = part.get_payload(decode=True) or b""
                if part.get_content_type() == "image/jpeg" or data[:2] == b"\xff\xd8":
                    with open(stem + ".jpg", "wb") as f:
                        f.write(data)
                else:
                    try:
                        record["metadata"] = json.loads(data)
                    except ValueError:
                        record.setdefault("fields", {})[part.get_param("name", header="content-disposition")] = data.decode(errors="replace")
            with open(stem + ".json", "w") as f:
                json.dump(record, f, indent=1)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, fmt, *args):
            pass

    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer((a.bind, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
