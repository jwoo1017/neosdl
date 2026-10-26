"""샌드박스 self-test 대상 앱 — 표준 라이브러리만(설치 불필요, egress 불필요).

/safe?id=  → 파라미터 바인딩(안전). IAST: nonce 가 parameters 로 감 → reached=0 → false_positive
/vuln?id=  → 문자열 조립(취약). IAST: nonce 가 operation 으로 감 → reached=1 → confirmed

sitecustomize.py(IAST 훅)가 sqlite3.connect 를 감싸 execute 를 관측한다. flask 등 외부 패키지
없이 http.server + sqlite3 로만 — dind 빌드가 pip(pypi) egress 없이도 되게 한다.
"""
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.execute("CREATE TABLE t (id TEXT)")
_db.execute("INSERT INTO t VALUES ('1')")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urlparse(self.path)
        uid = parse_qs(u.query).get("id", [""])[0]
        try:
            if u.path == "/health":
                return self._ok("ok")
            if u.path == "/safe":
                _db.cursor().execute("SELECT * FROM t WHERE id=?", (uid,)).fetchall()
                return self._ok("safe")
            if u.path == "/vuln":
                _db.cursor().execute("SELECT * FROM t WHERE id='%s'" % uid).fetchall()
                return self._ok("vuln")
        except Exception:
            pass
        return self._ok("x")

    def _ok(self, body):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 8000), H).serve_forever()
