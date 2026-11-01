"""Internal HTTP service: python3 -m app.service --host 127.0.0.1 --port 18562."""
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .rpc import PROVIDER, PROVIDER_VERSION, SCHEMA_VERSION, capabilities, execute

MAX_REQUEST_BYTES = 16 * 1024 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON value")


def make_server(address, token):
    if not isinstance(token, str) or len(token) < 24:
        raise ValueError("BIOCHECK_SERVICE_TOKEN须为至少24字符的私有服务凭据")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            # Access logs must not echo source text, request bodies or Authorization.
            pass

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=True, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def error(self, status, code):
            self.send_json(status, {"schema_version": SCHEMA_VERSION, "provider": PROVIDER,
                                    "provider_version": PROVIDER_VERSION, "status": "failed",
                                    "error_code": code})

        def authorized(self):
            supplied = self.headers.get("Authorization", "")
            if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                self.error(401, "unauthorized")
                return False
            return True

        def do_GET(self):
            if not self.authorized():
                return
            if self.path == "/health":
                self.send_json(200, {"schema_version": SCHEMA_VERSION, "provider": PROVIDER,
                                    "provider_version": PROVIDER_VERSION, "status": "ok"})
            elif self.path == "/v1/capabilities":
                self.send_json(200, dict(capabilities(), max_request_bytes=MAX_REQUEST_BYTES))
            else:
                self.error(404, "not_found")

        def do_POST(self):
            if not self.authorized():
                return
            if self.path != "/v1/execute":
                self.error(404, "not_found")
                return
            if self.headers.get("Transfer-Encoding") or len(self.headers.get_all("Content-Length", [])) != 1:
                self.error(400, "invalid_body_length")
                return
            try:
                size = int(self.headers["Content-Length"])
                if not 0 < size <= MAX_REQUEST_BYTES:
                    self.error(413, "request_too_large")
                    return
                if self.headers.get_content_type() != "application/json":
                    self.error(415, "json_required")
                    return
                body = self.rfile.read(size)
                if len(body) != size:
                    self.error(400, "incomplete_body")
                    return
                request = json.loads(body.decode(), object_pairs_hook=_unique_object,
                                     parse_constant=_reject_constant)
            except (ValueError, UnicodeError, RecursionError, TimeoutError):
                self.error(400, "invalid_json")
                return
            # Protocol/method failures are explicit JSON statuses; only transport failures use HTTP errors.
            self.send_json(200, execute(request))

    server = ThreadingHTTPServer(address, Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description="BioCheck无模型持钥专业服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18562)
    args = parser.parse_args()
    token = os.environ.get("BIOCHECK_SERVICE_TOKEN", "")
    with make_server((args.host, args.port), token) as server:
        print(json.dumps({"provider": PROVIDER, "provider_version": PROVIDER_VERSION,
                          "host": args.host, "port": server.server_port, "model_access": "host_only"}), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
