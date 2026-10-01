import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Iterable, Iterator, List, Mapping, Tuple, Union, cast


ResponseBody = Union[str, bytes, Mapping[str, object]]
Response = Tuple[int, Mapping[str, str], ResponseBody]


@dataclass
class RequestRecord:
    method: str
    path: str
    headers: Dict[str, str]
    body: bytes


class ScriptedHTTPServer(ThreadingHTTPServer):
    responses: List[Response]
    requests: List[RequestRecord]
    url: str


@contextmanager
def scripted_http_server(responses: Iterable[Response]) -> Iterator[ScriptedHTTPServer]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_DELETE(self) -> None:
            self._handle()

        def _handle(self) -> None:
            server = cast(ScriptedHTTPServer, self.server)
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b""
            server.requests.append(
                RequestRecord(
                    method=self.command,
                    path=self.path,
                    headers={key: value for key, value in self.headers.items()},
                    body=body,
                )
            )

            if server.responses:
                status, headers, response_body = server.responses.pop(0)
            else:
                status, headers, response_body = 500, {}, "no scripted response remains"

            if isinstance(response_body, Mapping):
                payload = json.dumps(response_body).encode("utf-8")
                headers = {"Content-Type": "application/json", **headers}
            elif isinstance(response_body, str):
                payload = response_body.encode("utf-8")
            else:
                payload = response_body

            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ScriptedHTTPServer(("127.0.0.1", 0), Handler)
    server.responses = list(responses)
    server.requests = []
    server.url = "http://127.0.0.1:{}".format(server.server_port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
