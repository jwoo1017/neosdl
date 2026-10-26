# IAST-lite 훅(sqlite) — sqlite3.Cursor 는 C 확장 타입이라 메서드 monkeypatch 가 안 된다.
# 대신 sqlite3.connect 를 감싸 execute 를 래핑한 Cursor 서브클래스를 주입한다(앱에 투명).
# nonce 가 쿼리 문자열(=조립, 위험)에 들어갔는지 parameters(=바인딩, 안전)에만 있는지 stderr 로 남긴다.
import os, sys, sqlite3

_NONCE = os.environ.get("NEOSDL_IAST_NONCE", "")


def _emit(reached, where):
    sys.stderr.write("NEOSDL_IAST reached=%s where=%s\n" % (int(bool(reached)), where)); sys.stderr.flush()


class _Cur(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        try:
            if _NONCE and _NONCE in (sql or ""):
                _emit(True, "sql_operation")
            elif _NONCE and _NONCE in repr(parameters):
                _emit(False, "sql_parameter")
        except Exception:
            pass
        return super().execute(sql, parameters)


class _Conn(sqlite3.Connection):
    def cursor(self, factory=_Cur):
        return super().cursor(factory)


_orig = sqlite3.connect
def _connect(*a, **k):
    k.setdefault("factory", _Conn)
    return _orig(*a, **k)
sqlite3.connect = _connect
