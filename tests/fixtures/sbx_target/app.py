"""샌드박스 self-test 대상 앱(의도적으로 안전/취약 둘 다).

/safe?id=  → 파라미터 바인딩(안전). IAST: nonce 가 parameters 로 감 → reached=0 → false_positive
/vuln?id=  → 문자열 조립(취약). IAST: nonce 가 operation 으로 감 → reached=1 → confirmed

실제 취약점을 만드는 게 목적이 아니라, 검증 루프(boot→HTTP→IAST→oracle)가 안전/취약을
구분하는지 확인하기 위한 최소 대상이다. sqlite in-memory 라 외부 DB 불필요.
"""
import sqlite3

from flask import Flask, request

app = Flask(__name__)
_db = sqlite3.connect(":memory:", check_same_thread=False)
_db.execute("CREATE TABLE t (id TEXT)")
_db.execute("INSERT INTO t VALUES ('1')")


@app.get("/health")
def health():
    return "ok"


@app.get("/safe")
def safe():
    uid = request.args.get("id", "")
    try:
        _db.cursor().execute("SELECT * FROM t WHERE id=?", (uid,)).fetchall()
    except Exception:
        pass
    return "safe"


@app.get("/vuln")
def vuln():
    uid = request.args.get("id", "")
    try:
        _db.cursor().execute("SELECT * FROM t WHERE id='%s'" % uid).fetchall()
    except Exception:
        pass
    return "vuln"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
