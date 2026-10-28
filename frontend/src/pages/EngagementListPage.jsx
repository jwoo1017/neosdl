import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { createEngagement, fetchEngagements, previewBootRecipe } from "../api";
import { useProject } from "../ProjectContext";
import Modal from "../components/Modal";

// engagement = 자율 보안 검증 run. 정적 finding 을 샌드박스에서 실제로 재현/반증해
// 오탐을 자동 제거하고 confirmed 만 남긴다. 생성은 manager 이상(코드를 실제로 때리는 행위).
const STATUS_BADGE = {
  queued: "badge-risk-low", planning: "badge-risk-low", running: "badge-risk-medium",
  awaiting_approval: "badge-risk-high", paused: "badge-risk-medium",
  succeeded: "badge-risk-low", failed: "badge-risk-critical", cancelled: "badge-risk-low",
};
const STATUS_LABEL = {
  queued: "대기", planning: "계획", running: "실행 중", awaiting_approval: "승인 대기",
  paused: "일시정지", succeeded: "완료", failed: "실패", cancelled: "취소됨",
};
const PERMISSION_LABEL = { plan: "설계만(plan)", ask: "승인 필요(ask)", auto: "자동(auto)" };

export default function EngagementListPage() {
  const { activeProject, hasProjectRole } = useProject();
  const navigate = useNavigate();
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const canStart = hasProjectRole("manager");

  const load = () => {
    if (!activeProject) return;
    fetchEngagements(activeProject.id).then(setRows).catch((e) => setError(e.message));
  };

  useEffect(() => {
    setRows(null); setError(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject?.id]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <h1>Engagements</h1>
        {canStart && (
          <button className="btn-primary" onClick={() => setShowModal(true)}>검증 실행</button>
        )}
      </div>
      <p className="muted">
        정적 분석에서 나온 보안 finding 을 샌드박스에서 실제로 재현/반증해 오탐을 자동 제거합니다.
        확인된(confirmed) 취약점만 수정 PR 로 이어집니다.
      </p>
      {error && <div className="alert-error">{error}</div>}

      {rows === null && <p className="muted">불러오는 중...</p>}
      {rows && rows.length === 0 && (
        <div className="dashboard-empty">
          아직 검증 run 이 없습니다.{canStart ? " '검증 실행' 으로 시작하세요." : " manager 권한이 있어야 시작할 수 있습니다."}
        </div>
      )}

      {rows && rows.length > 0 && (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>상태</th><th>목표</th><th>권한 모드</th><th>생성</th><th>종료</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className="risk-row"
                    onClick={() => navigate(`/projects/${activeProject.id}/detail/security/engagements/${e.id}`)}>
                  <td><span className={`badge ${STATUS_BADGE[e.status] || ""}`}>{STATUS_LABEL[e.status] || e.status}</span></td>
                  <td>{e.goal}</td>
                  <td className="muted">{PERMISSION_LABEL[e.permission_mode] || e.permission_mode}</td>
                  <td className="muted">{e.created_at ? new Date(e.created_at).toLocaleString() : "-"}</td>
                  <td className="muted">{e.finished_at ? new Date(e.finished_at).toLocaleString() : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showModal && (
        <CreateEngagementModal
          projectId={activeProject.id}
          onClose={() => setShowModal(false)}
          onCreated={(id) => {
            setShowModal(false);
            navigate(`/projects/${activeProject.id}/detail/security/engagements/${id}`);
          }}
        />
      )}
    </div>
  );
}

function CreateEngagementModal({ projectId, onClose, onCreated }) {
  const [kind, setKind] = useState("whitebox"); // whitebox | blackbox(외부 표적) | repo_blackbox(레포 부팅)
  const [goal, setGoal] = useState("최신 스캔의 보안 finding 자동 검증(오탐 제거)");
  const [target, setTarget] = useState("");
  const [permissionMode, setPermissionMode] = useState("ask");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // 부팅 레시피(모달에서 사람이 확인/수정) — repo_blackbox 선택 시 자동완성해 폼에 채운다.
  const [recipe, setRecipe] = useState(null);       // {base_image, app_port, start_command, dockerfile, needs_build, env, db_credentials, ...}
  const [recipeLoading, setRecipeLoading] = useState(false);
  const blackbox = kind === "blackbox";
  const repoBlackbox = kind === "repo_blackbox";

  // 레포 부팅 블랙박스는 일회용·격리 표적이라 auto(사전 승인)가 기본 권장 — 선택 시 자동 전환 +
  // 레시피 자동완성(LLM/결정적)을 받아 폼에 채운다. 사람은 확인/수정만.
  const onKindChange = async (v) => {
    setKind(v);
    setError(null);
    if (v === "repo_blackbox") {
      setPermissionMode("auto");
      if (!recipe) {
        setRecipeLoading(true);
        try {
          const res = await previewBootRecipe(projectId);
          setRecipe(res.recipe);
        } catch (e) {
          setError(`레시피 자동완성 실패: ${e.message} — 아래 값을 직접 채우세요.`);
          setRecipe({ base_image: "", app_port: 3000, start_command: "", dockerfile: "", needs_build: true, env: {}, db_credentials: {} });
        } finally {
          setRecipeLoading(false);
        }
      }
    }
  };

  const setR = (patch) => setRecipe((r) => ({ ...(r || {}), ...patch }));
  const envText = recipe ? Object.entries(recipe.env || {}).map(([k, v]) => `${k}=${v}`).join("\n") : "";
  const onEnvText = (text) => {
    const env = {};
    for (const line of text.split("\n")) {
      const t = line.trim();
      if (!t || t.startsWith("#") || !t.includes("=")) continue;
      const i = t.indexOf("=");
      env[t.slice(0, i).trim()] = t.slice(i + 1).trim();
    }
    setR({ env });
  };

  const submit = async () => {
    if (!goal.trim()) { setError("목표를 입력하세요."); return; }
    if (blackbox && !target.trim()) { setError("표적(host 또는 URL)을 입력하세요."); return; }
    if (repoBlackbox && recipe && !recipe.app_port) { setError("앱 포트를 입력하세요."); return; }
    setBusy(true); setError(null);
    try {
      const body = { goal: goal.trim(), mode: "automation", permission_mode: permissionMode };
      if (blackbox) body.target = target.trim();
      if (repoBlackbox) {
        body.boot_repo = true;
        if (recipe) body.boot_recipe = { ...recipe, app_port: Number(recipe.app_port) || 0 };
      }
      const res = await createEngagement(projectId, body);
      onCreated(res.id);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  };

  return (
    <Modal title="검증 run 실행" onClose={onClose}>
      <label style={{ display: "block", marginBottom: "0.25rem" }}>검증 유형</label>
      <select value={kind} onChange={(e) => onKindChange(e.target.value)} disabled={busy}
              style={{ width: "100%", boxSizing: "border-box" }}>
        <option value="whitebox">화이트박스 — 스캔의 finding을 샌드박스에서 재현/반증(오탐 제거)</option>
        <option value="repo_blackbox">레포 부팅 블랙박스 — 이 레포(Docker/compose)를 자동으로 띄워 점검(IP·allowlist 불필요)</option>
        <option value="blackbox">블랙박스 — 인가된 외부 표적에 보안 도구(nmap/sqlmap) 점검</option>
      </select>

      {blackbox && (
        <>
          <label style={{ display: "block", marginTop: "0.8rem", marginBottom: "0.25rem" }}>표적 (host 또는 URL)</label>
          <input value={target} onChange={(e) => setTarget(e.target.value)} disabled={busy}
                 placeholder="예: test.internal 또는 http://10.0.0.5:8080/app"
                 style={{ width: "100%", boxSizing: "border-box" }} />
          <p className="muted" style={{ fontSize: "0.75rem", marginTop: "0.25rem" }}>
            표적은 서버 allowlist(법적 권한 보유)에 등록돼 있어야 실행됩니다. 미인가 표적은 거부됩니다.
          </p>
        </>
      )}
      {repoBlackbox && (
        <>
          <p className="muted" style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>
            이 레포를 샌드박스에 띄워 그 앱을 표적으로 점검합니다(표적 IP·allowlist 불필요). 아래 부팅
            레시피는 README·매니페스트로 자동완성됐습니다 — 확인하고 필요하면 수정하세요.
          </p>
          {recipeLoading && <p className="muted" style={{ fontSize: "0.8rem" }}>레시피 자동완성 중…</p>}
          {recipe && (
            <div style={{ marginTop: "0.6rem", padding: "0.6rem", border: "1px solid var(--border, #ccc)", borderRadius: 6 }}>
              <div style={{ display: "flex", gap: "0.5rem" }}>
                <div style={{ flex: 2 }}>
                  <label style={{ display: "block", fontSize: "0.75rem", marginBottom: "0.2rem" }}>베이스 이미지</label>
                  <input value={recipe.base_image || ""} onChange={(e) => setR({ base_image: e.target.value })}
                         disabled={busy} style={{ width: "100%", boxSizing: "border-box" }} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ display: "block", fontSize: "0.75rem", marginBottom: "0.2rem" }}>앱 포트</label>
                  <input type="number" value={recipe.app_port || ""} onChange={(e) => setR({ app_port: e.target.value })}
                         disabled={busy} style={{ width: "100%", boxSizing: "border-box" }} />
                </div>
              </div>
              <label style={{ display: "block", fontSize: "0.75rem", margin: "0.4rem 0 0.2rem" }}>시작 명령</label>
              <input value={recipe.start_command || ""} onChange={(e) => setR({ start_command: e.target.value })}
                     disabled={busy} placeholder="예: node server.js"
                     style={{ width: "100%", boxSizing: "border-box" }} />
              <label style={{ display: "block", fontSize: "0.75rem", margin: "0.4rem 0 0.2rem" }}>
                <input type="checkbox" checked={!!recipe.needs_build} onChange={(e) => setR({ needs_build: e.target.checked })}
                       disabled={busy} /> dind 에서 빌드(레포에 이미지 없음)
              </label>
              {recipe.needs_build && (
                <>
                  <label style={{ display: "block", fontSize: "0.75rem", margin: "0.2rem 0" }}>Dockerfile (초안 — 레포에 없을 때)</label>
                  <textarea rows={6} value={recipe.dockerfile || ""} onChange={(e) => setR({ dockerfile: e.target.value })}
                            disabled={busy} style={{ width: "100%", boxSizing: "border-box", fontFamily: "monospace", fontSize: "0.72rem" }} />
                </>
              )}
              <label style={{ display: "block", fontSize: "0.75rem", margin: "0.4rem 0 0.2rem" }}>런타임 env (KEY=VALUE, 한 줄에 하나 — DB_HOST는 백킹 서비스 별칭)</label>
              <textarea rows={4} value={envText} onChange={(e) => onEnvText(e.target.value)}
                        disabled={busy} style={{ width: "100%", boxSizing: "border-box", fontFamily: "monospace", fontSize: "0.72rem" }} />
              {typeof recipe.confidence === "number" && recipe.confidence < 0.5 && (
                <p className="muted" style={{ fontSize: "0.72rem", marginTop: "0.3rem" }}>
                  ⚠ 자동완성 신뢰도 낮음({recipe.notes || "스택 미상"}) — 값을 직접 확인하세요.
                </p>
              )}
            </div>
          )}
        </>
      )}
      {!blackbox && !repoBlackbox && (
        <p className="muted" style={{ marginTop: "0.6rem" }}>대상 스캔은 이 프로젝트의 가장 최근 완료된 스캔이 자동 선택됩니다.</p>
      )}

      <label style={{ display: "block", marginTop: "0.8rem", marginBottom: "0.25rem" }}>목표</label>
      <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} disabled={busy}
                style={{ width: "100%", boxSizing: "border-box" }} />
      <label style={{ display: "block", marginTop: "0.8rem", marginBottom: "0.25rem" }}>권한 모드</label>
      <select value={permissionMode} onChange={(e) => setPermissionMode(e.target.value)} disabled={busy}
              style={{ width: "100%", boxSizing: "border-box" }}>
        <option value="ask">승인 필요(ask) — 실행·파괴 action마다 사람 승인</option>
        <option value="plan">설계만(plan) — 샌드박스 실행 안 함(정적만)</option>
        <option value="auto">자동(auto) — 사전 승인, 자동 진행</option>
      </select>
      {error && <div className="alert-error" style={{ marginTop: "0.6rem" }}>{error}</div>}
      <div style={{ display: "flex", gap: "0.5rem", justifyContent: "flex-end", marginTop: "1rem" }}>
        <button onClick={onClose} disabled={busy}>취소</button>
        <button className="btn-primary" onClick={submit} disabled={busy}>
          {busy ? "시작 중..." : "실행"}
        </button>
      </div>
    </Modal>
  );
}
