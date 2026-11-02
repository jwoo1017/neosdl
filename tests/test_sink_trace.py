"""sink_trace — 엔드포인트에서 실제 sink 줄까지 소스 레벨로 내려가는지(오프라인, reader 주입)."""

from app import sink_trace as st

# 사건 재현: 컨트롤러 3번째 줄은 쿼리가 아니라 핸들러 선언이다. 실제 쿼리는 서비스 → 레포지토리에 있다.
_EXPRESS = {
    "src/server.js": (
        "import express from 'express';\n"
        "import authRouter from './routes/authRouter.js';\n"
        "const app = express();\n"
        "app.use('/api/v1/auth', authRouter);\n"
    ),
    "src/routes/authRouter.js": (
        "import { Router } from 'express';\n"
        "import { body } from 'express-validator';\n"
        "import * as authController from '../controllers/AuthController.js';\n"
        "const router = Router();\n"
        "router.post('/signup', body('email').isEmail(), authController.signup);\n"
        "router.post('/login', authController.login);\n"
        "router.get('/user/:id', authController.getUser);\n"
        "router.get('/history', authController.history);\n"
        "export default router;\n"
    ),
    "src/controllers/AuthController.js": (
        "import service from '../services/authService.js';\n"
        "\n"
        "export const signup = async (req, res) => {\n"
        "    const retData = await service.signup(req);\n"
        "\n"
        "    res.json(retData);\n"
        "};\n"
        "export const login = async (req, res) => {\n"
        "    const { email, password } = req.body;\n"
        "    res.json(await service.login(email, password));\n"
        "};\n"
        "export const getUser = async (req, res) => res.json(await service.getUser(req.params.id));\n"
        "export const history = async (req, res) => res.json(await service.history(req.query.page));\n"
    ),
    "src/services/authService.js": (
        "const repo = require('../repositories/userRepository');\n"
        "class AuthService {\n"
        "  async signup(req) {\n"
        "    const { email, name } = req.body;\n"
        "    return this.save(email, name);\n"
        "  }\n"
        "  async save(email, name) {\n"
        "    return repo.insertUser(email, name);\n"
        "  }\n"
        "  async login(email, password) {\n"
        "    return repo.findByEmail(email);\n"
        "  }\n"
        "  async getUser(id) {\n"
        "    return repo.getById(id);\n"
        "  }\n"
        "  async history(page) {\n"
        "    return repo.history(page);\n"
        "  }\n"
        "}\n"
        "module.exports = new AuthService();\n"
    ),
    "src/repositories/userRepository.js": (
        "const db = require('../db');\n"
        "const HISTORY_TABLE = 'login_history';\n"
        "exports.insertUser = async (email, name) => {\n"
        "  const sql = 'INSERT INTO users(email, name) VALUES (?, ?)';\n"
        "  return db.query(sql, [email, name]);\n"
        "};\n"
        "exports.findByEmail = async (email) => {\n"
        "  const sql = \"SELECT * FROM users WHERE email = '\" + email + \"'\";\n"
        "  return db.query(sql);\n"
        "};\n"
        "exports.getById = async (id) => {\n"
        "  return db.query(`SELECT * FROM users WHERE id = ${id}`);\n"
        "};\n"
        "exports.history = async (page) => {\n"
        "  return db.query(`SELECT * FROM ${HISTORY_TABLE} LIMIT 10 OFFSET ?`, [page]);\n"
        "};\n"
    ),
    "src/db.js": "module.exports = require('mysql2').createPool({});\n",
}


def _trace(rel, method="POST", files=_EXPRESS, route_file="src/routes/authRouter.js"):
    ep = {"method": method, "path": "/api/v1/auth" + rel, "route_rel": rel, "route_file": route_file,
          "route_line": None}
    return st.trace_endpoint(files.get, list(files), ep)


def test_signup_reaches_parameterized_insert_not_controller_line():
    prof = _trace("/signup")
    assert prof["handler"]["file"] == "src/controllers/AuthController.js"
    [s] = prof["sinks"]
    assert (s["file"], s["line"]) == ("src/repositories/userRepository.js", 5)   # 실제 쿼리 줄
    assert s["construction"] == st.PARAMETERIZED and not s["dangerous"]
    names = [h["name"] for h in s["chain"]]
    assert names[0] == "POST /api/v1/auth/signup" and "AuthService.signup" in names and "AuthService.save" in names


def test_concat_with_request_value_is_dangerous_at_sink_line():
    prof = _trace("/login")
    [s] = prof["dangerous"]
    assert (s["file"], s["line"]) == ("src/repositories/userRepository.js", 9)
    assert s["construction"] == st.CONCAT_INPUT and "email" in s["tainted"]
    assert s["category"] == "sql_injection"


def test_template_interpolation_of_path_param_is_dangerous():
    [s] = _trace("/user/:id", "GET")["dangerous"]
    assert s["line"] == 12 and s["construction"] == st.CONCAT_INPUT


def test_constant_table_interpolation_with_bindings_is_not_dangerous():
    prof = _trace("/history", "GET")
    [s] = prof["sinks"]
    assert s["construction"] == st.CONCAT_CONST and not s["dangerous"]


def test_inline_handler_and_wrapped_handler():
    files = {
        "routes/r.js": (
            "const pool = require('../db');\n"
            "const asyncHandler = require('express-async-handler');\n"
            "router.get('/search', asyncHandler(async (req, res) => {\n"
            "  const q = req.query.q;\n"
            "  const [rows] = await pool.execute('SELECT * FROM p WHERE name LIKE \"%' + q + '%\"');\n"
            "  res.send(`<h1>${q}</h1>`);\n"
            "}));\n"
        ),
        "db.js": "",
    }
    prof = _trace("/search", "GET", files, "routes/r.js")
    cats = {s["category"]: s for s in prof["dangerous"]}
    assert cats["sql_injection"]["line"] == 5
    assert cats["xss"]["line"] == 6


def test_unknown_handler_returns_empty_not_error():
    prof = _trace("/nope")
    assert prof["sinks"] == [] and prof["resolved"] is False


def test_python_fastapi_fstring_execute():
    files = {
        "app/routes.py": (
            "from fastapi import APIRouter, Depends\n"
            "from app.repo import find_user\n"
            "router = APIRouter()\n"
            "@router.get('/users')\n"
            "def users(name: str, db = Depends(get_db)):\n"
            "    return find_user(db, name)\n"
        ),
        "app/repo.py": (
            "def find_user(db, name):\n"
            "    return db.execute(f\"SELECT * FROM users WHERE name = '{name}'\").fetchall()\n"
            "def safe(db, name):\n"
            "    return db.execute('SELECT * FROM users WHERE name = %s', (name,))\n"
        ),
    }
    prof = _trace("/users", "GET", files, "app/routes.py")
    [s] = prof["dangerous"]
    assert (s["file"], s["line"]) == ("app/repo.py", 2) and s["construction"] == st.CONCAT_INPUT


# DB 헬퍼 래퍼 + 클래스 필드 화살표 핸들러 + `export default new X()` + `.route().post().get()` 체인.
_HELPER = {
    "src/routes/order.js": (
        "import { Router } from 'express';\n"
        "import orderController from '../controllers/orderController.js';\n"
        "const router = Router();\n"
        "router.route('/cancel').post(validate, orderController.cancel).get(orderController.list);\n"
        "export default router;\n"
    ),
    "src/controllers/orderController.js": (
        "import orderService from '../services/orderService.js';\n"
        "class OrderController {\n"
        "  cancel = async (req, res, next) => {\n"
        "    const { symbol, order_id } = req.body;\n"
        "    res.json(await orderService.cancel(symbol, order_id));\n"
        "  };\n"
        "  list = async (req, res) => res.json(await orderService.list(req.query.symbol));\n"
        "}\n"
        "export default new OrderController();\n"
    ),
    "src/services/orderService.js": (
        "import { query } from '../db/index.js';\n"
        "const cancel = async (symbol, orderId) => {\n"
        "  return query(`UPDATE orders SET status='C' WHERE symbol='${symbol}' AND id=${orderId}`);\n"
        "};\n"
        "const list = async (symbol) => query('SELECT * FROM orders WHERE symbol = ?', [symbol]);\n"
        "export default { cancel, list };\n"
    ),
    "src/db/index.js": (
        "import mysql from 'mysql2/promise';\n"
        "const pool = mysql.createPool({});\n"
        "export const query = async (sql, params) => {\n"
        "  const [rows] = await pool.query(sql, params);\n"
        "  return rows;\n"
        "};\n"
    ),
}


def test_db_helper_wrapper_is_judged_at_the_caller_line():
    """`query(sql, params)` 헬퍼 안의 pool.query 가 아니라, 문자열을 보간한 **호출자 줄**을 가리킨다."""
    post = _trace("/cancel", "POST", _HELPER, "src/routes/order.js")
    [s] = post["sinks"]
    assert (s["file"], s["line"]) == ("src/services/orderService.js", 3) and s["dangerous"]
    assert set(s["tainted"]) == {"symbol", "orderId"} and s["via"][-1]["name"] == "pool.query"
    assert post["handler"]["name"] == "OrderController.cancel"
    get = _trace("/cancel", "GET", _HELPER, "src/routes/order.js")
    [g] = get["sinks"]
    assert (g["line"], g["construction"]) == (5, st.PARAMETERIZED)
