"""IAST-lite — sink 지점만 좁게 계측해 "오염 입력(nonce)이 위험 위치에 실제로 도달했는가"를
런타임에서 직접 관측한다. 화이트박스라 가능한 가장 강한 SQLi 오라클.

핵심 판정(`nonce_reached_sink`): DB execute 호출에서 nonce 가
- 쿼리 문자열(operation, 첫 인자)에 들어갔으면 → 조립됨 = **도달(confirmed)**
- 파라미터 배열/튜플(두 번째 인자~)에만 있으면 → 바인딩 = **미도달(false_positive)**  ← 이번 9건
이는 app/dataflow.py 의 `_SAFE_FROM_ARG_INDEX`(정적 판정)를 런타임으로 확인하는 것과 같다.

여기(파이썬 모듈)에는 순수 판정 로직 + 각 언어 프리로드 스크립트 텍스트만 둔다. 실제 주입은
샌드박스(worker/sandbox)가 앱 기동 시 언어별로 건다(Python sitecustomize, JS --require).
"""

from __future__ import annotations


def nonce_reached_sink(nonce: str, operation: str, parameters) -> bool:
    """DB execute(operation, parameters) 한 번에 대해 nonce 가 '위험 위치'에 도달했는지.

    operation(쿼리 문자열)에 nonce 가 있으면 True(조립 = 위험). parameters(바인딩 값)에만
    있으면 False(안전). 둘 다 없으면 False.
    """
    if nonce and nonce in (operation or ""):
        return True
    return False


# --- 파이썬 대상 앱에 넣는 sitecustomize 훅(문자열) ---
# DBAPI 커서의 execute 를 감싸 nonce 가 operation 에 들어갔는지 한 줄 로그로 남긴다.
# 샌드박스가 이 파일을 앱 작업디렉터리에 두고 PYTHONPATH 앞에 붙여 로드시킨다.
PYTHON_SITECUSTOMIZE = r'''
import os, sys
_NONCE = os.environ.get("NEOSDL_IAST_NONCE", "")
def _emit(reached, where):
    sys.stderr.write("NEOSDL_IAST reached=%s where=%s\n" % (int(bool(reached)), where)); sys.stderr.flush()
def _wrap_execute(orig):
    def execute(self, operation, parameters=None, *a, **k):
        try:
            if _NONCE and _NONCE in (operation or ""):
                _emit(True, "sql_operation")
            elif _NONCE and _NONCE in repr(parameters):
                _emit(False, "sql_parameter")
        except Exception:
            pass
        return orig(self, operation, parameters, *a, **k) if parameters is not None else orig(self, operation, *a, **k)
    return execute
try:
    import sqlite3; sqlite3.Cursor.execute = _wrap_execute(sqlite3.Cursor.execute)
except Exception: pass
for mod in ("psycopg2.extensions", "pymysql.cursors", "MySQLdb.cursors"):
    try:
        import importlib; m = importlib.import_module(mod)
        for cls in ("cursor", "Cursor", "DictCursor"):
            c = getattr(m, cls, None)
            if c and hasattr(c, "execute"):
                c.execute = _wrap_execute(c.execute)
    except Exception: pass
'''

# --- Node.js 대상 앱에 넣는 --require 프리로드(문자열) ---
# mysql2/pg 의 query/execute 첫 인자(SQL 문자열)에 nonce 가 있으면 reached=1.
NODE_PRELOAD = r'''
const NONCE = process.env.NEOSDL_IAST_NONCE || "";
function emit(reached, where){ try{ process.stderr.write("NEOSDL_IAST reached="+(reached?1:0)+" where="+where+"\n"); }catch(e){} }
function wrap(obj, name){
  if(!obj || !obj.prototype || typeof obj.prototype[name] !== "function") return;
  const orig = obj.prototype[name];
  obj.prototype[name] = function(sql, ...rest){
    try{
      const text = typeof sql === "string" ? sql : (sql && sql.text) || "";
      if(NONCE && text.indexOf(NONCE) !== -1) emit(true, "sql_text");
      else if(NONCE && JSON.stringify(rest).indexOf(NONCE) !== -1) emit(false, "sql_param");
    }catch(e){}
    return orig.apply(this, [sql, ...rest]);
  };
}
for(const mod of ["mysql2","mysql2/promise","pg","mysql"]){
  try{ const m = require(mod); wrap(m.Connection||m.Pool||{}, "query"); wrap(m.Connection||{}, "execute"); }catch(e){}
}
'''
