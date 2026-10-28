import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { approveEngagement, engagementSocketUrl, fetchEngagement } from "../api";
import { useProject } from "../ProjectContext";
import { createBackoff, isTerminalClose } from "../wsReconnect";
import MarkdownAnswer from "../chat/MarkdownAnswer";
import EngageTimeline from "../components/EngageTimeline";
import { IconBoot, IconScan, IconFinding, IconEndpoint, IconReport, IconPause, IconLock } from "../components/EngageIcons";

// 섹션 제목 + 아이콘.
function SectionHead({ icon: Icon, children }) {
  return (
    <h2 style={{ display: "flex", alignItems: "center", gap: "0.45rem" }}>
      <Icon size={18} /> {children}
    </h2>
  );
}

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

// 동적 판정 배지 — confirmed(진짜 취약) / false_positive(오탐 제거) / inconclusive / not_attempted.
const VERDICT_BADGE = {
  confirmed: "badge-risk-critical", false_positive: "badge-risk-low",
  inconclusive: "badge-risk-medium", not_attempted: "badge-risk-none",
};
const VERDICT_LABEL = {
  confirmed: "확인됨", false_positive: "오탐", inconclusive: "불확정", not_attempted: "미시도",
};

export default function EngagementDetailPage() {
  const { engagementId } = useParams();
  const { activeProject, hasProjectRole } = useProject();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [connected, setConnected] = useState(false);

  const load = useCallback(() => {
    if (!engagementId) return;
    fetchEngagement(engagementId).then(setData).catch((e) => setError(e.message));
  }, [engagementId]);

  useEffect(() => {
    setData(null); setError(null);
    load();
  }, [load]);

  // engagement 는 워커 프로세스에서 돌기 때문에 상태 전이를 WS(engagement_bus)로 받는다.
  // 이벤트가 오면(주로 status 전이) 상세를 다시 불러온다 — 판정/리포트가 한 tx 로 쓰이므로 refetch 로 충분.
  const wsRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  useEffect(() => {
    if (!engagementId) return undefined;
    let cancelled = false;
    let gaveUp = false;
    const backoff = createBackoff();

    const connect = () => {
      if (cancelled || gaveUp) return;
      reconnectTimerRef.current = null;
      const ws = new WebSocket(engagementSocketUrl(engagementId));
      wsRef.current = ws;
      ws.onopen = () => { if (!cancelled) { backoff.reset(); setConnected(true); load(); } };
      ws.onmessage = () => { if (!cancelled) load(); };
      ws.onclose = (evt) => {
        if (cancelled) return;
        setConnected(false);
        if (isTerminalClose(evt.code)) { gaveUp = true; return; }
        reconnectTimerRef.current = setTimeout(connect, backoff.next());
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => {
      cancelled = true;
      clearTimeout(reconnectTimerRef.current);
      try { wsRef.current?.close(); } catch { /* 이미 닫힘 */ }
    };
  }, [engagementId, load]);

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;
  if (error) return <div className="dashboard"><div className="alert-error">{error}</div></div>;
  if (data === null) return <div className="dashboard"><p className="muted">불러오는 중...</p></div>;

  const eng = data.engagement;
  // 블랙박스 계열(외부 표적 / 레포 부팅)은 모두 BlackboxDetail 로 — repo_blackbox 는 target 이 없어
  // 예전엔 화이트박스 경로로 새어 소스열·점검범위가 안 보였다(수정).
  const kind = eng.params?.engagement_kind;
  const blackbox = !!(eng.params?.target) || kind === "blackbox" || kind === "repo_blackbox";
  if (blackbox) return (
    <BlackboxDetail eng={eng} report={data.report} connected={connected}
                    canApprove={hasProjectRole("manager")} onChange={load} />
  );

  const summary = data.report?.static_summary || {};
  const candidates = summary.candidates || [];
  const verByFinding = new Map((data.verifications || []).map((v) => [v.finding_id, v]));
  const dyn = eng.result?.dynamic; // 워커가 쓴 verdict 카운트(없으면 정적만)
  const staticOnly = !dyn && (data.verifications || []).length === 0;

  // 표시할 finding 행: 리포트 candidates 를 기준으로, 동적 판정이 있으면 덮어 쓴다.
  const rows = candidates.length > 0
    ? candidates.map((c) => ({ ...c, v: verByFinding.get(c.finding_id) }))
    : (data.verifications || []).map((v) => ({
        finding_id: v.finding_id, category: v.category, severity: v.severity,
        sink: (v.sink_symbol_key || "").split("#").pop(), source_var: v.source_var,
        entrypoint: v.entry_route
          ? { resolved: true, method: v.entry_route.method, path: v.entry_route.path,
              param: v.entry_route.param_name, auth_required: v.entry_route.auth_required }
          : { resolved: false, reason: v.reason },
        v,
      }));

  return (
    <div className="dashboard">
      <h1>
        Engagement{" "}
        <span className={`badge ${STATUS_BADGE[eng.status] || ""}`}>{STATUS_LABEL[eng.status] || eng.status}</span>
        {connected && <span className="muted" style={{ fontSize: "0.7rem", marginLeft: "0.5rem" }}>● 실시간</span>}
      </h1>
      <p className="muted">{eng.goal}</p>
      <div className="muted" style={{ fontSize: "0.8rem", marginBottom: "0.8rem" }}>
        권한 모드: {PERMISSION_LABEL[eng.permission_mode] || eng.permission_mode}
        {" · "}모드: {eng.mode}
        {eng.error && <span style={{ color: "var(--danger, #ff6b74)" }}>{" · 오류: " + eng.error}</span>}
      </div>

      <div className="dashboard-section">
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap" }}>
          <Stat label="보안 finding" value={summary.findings_total ?? rows.length} />
          <Stat label="엔트리포인트 해석" value={summary.entrypoint_resolved ?? "-"} />
          {dyn && (
            <>
              <Stat label="확인됨" value={dyn.confirmed || 0} />
              <Stat label="오탐 제거" value={dyn.false_positive || 0} />
              <Stat label="미시도" value={dyn.not_attempted || 0} />
            </>
          )}
        </div>
        {staticOnly && (
          <p className="muted" style={{ marginTop: "0.6rem" }}>
            정적 분석만 수행됨(동적 검증 비활성 또는 미기동). finding 은 엔트리포인트 해석까지 표시됩니다.
          </p>
        )}
      </div>

      {rows.length > 0 && (
        <div className="table-scroll">
          <table className="repo-table">
            <thead>
              <tr>
                <th>유형</th><th>Sink</th><th>엔트리포인트</th><th>판정</th><th>신뢰도</th><th>근거</th><th>수정 PR</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const ep = r.entrypoint || {};
                const v = r.v;
                return (
                  <tr key={r.finding_id}>
                    <td>{r.category}</td>
                    <td className="muted" style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>
                      {r.sink}
                      {r.source_var ? <div style={{ color: "var(--text-faint)" }}>← {r.source_var}</div> : null}
                    </td>
                    <td className="muted" style={{ fontSize: "0.78rem" }}>
                      {ep.resolved
                        ? <span style={{ display: "inline-flex", alignItems: "center", gap: "0.25rem" }}>
                            {ep.method} {ep.path}{ep.param ? ` (${ep.param})` : ""}
                            {ep.auth_required ? <IconLock size={12} /> : null}
                          </span>
                        : <span style={{ color: "var(--text-faint)" }}>미해석({ep.reason || "?"})</span>}
                    </td>
                    <td>
                      {v
                        ? <span className={`badge ${VERDICT_BADGE[v.verdict] || ""}`}>{VERDICT_LABEL[v.verdict] || v.verdict}</span>
                        : <span className="muted">-</span>}
                    </td>
                    <td className="muted">{v?.confidence != null ? `${Math.round(v.confidence * 100)}%` : "-"}</td>
                    <td className="muted" style={{ fontSize: "0.75rem", maxWidth: "220px" }}>
                      {v?.reason || (v?.oracle ? `오라클: ${v.oracle}` : "-")}
                    </td>
                    <td>
                      {v?.pr_url
                        ? <a className="md-link" href={v.pr_url} target="_blank" rel="noopener noreferrer">#{v.pr_number}</a>
                        : <span className="muted">{v?.fix_status || "-"}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {data.report?.markdown && (
        <div className="dashboard-section" style={{ marginTop: "1.2rem" }}>
          <h2>리포트</h2>
          <MarkdownAnswer content={data.report.markdown} />
        </div>
      )}
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div>
      <div style={{ fontSize: "1.4rem", fontWeight: 600 }}>{value}</div>
      <div className="muted" style={{ fontSize: "0.75rem" }}>{label}</div>
    </div>
  );
}

const SEV_BADGE = { critical: "badge-risk-critical", high: "badge-risk-high",
  medium: "badge-risk-medium", low: "badge-risk-low" };

// 블랙박스(외부 표적) 점검 결과 — Pentester가 터미널 도구로 점검한 리포트/발견/승인 대기.
function BlackboxDetail({ eng, report, connected, canApprove, onChange }) {
  const r = report || {};
  const awaiting = eng.status === "awaiting_approval";
  const [approving, setApproving] = useState(false);
  const [approveErr, setApproveErr] = useState(null);
  const pending = eng.result?.pending_command;

  const approve = async () => {
    setApproving(true); setApproveErr(null);
    try {
      await approveEngagement(eng.id);
      onChange?.();          // 상태 refetch — queued 로 바뀌고 WS 로 진행 수신
    } catch (e) {
      setApproveErr(e.message);
      setApproving(false);
    }
  };
  const repoBoot = eng.params?.engagement_kind === "repo_blackbox";
  const target = eng.params?.target || r.target || "-";
  return (
    <div className="dashboard">
      <h1 style={{ display: "flex", alignItems: "center", gap: "0.45rem", flexWrap: "wrap" }}>
        <IconBoot size={22} /> Engagement
        <span className={`badge ${STATUS_BADGE[eng.status] || ""}`}>{STATUS_LABEL[eng.status] || eng.status}</span>
        <span className="badge">{repoBoot ? "레포 부팅 블랙박스" : "블랙박스"}</span>
        {connected && <span className="muted" style={{ fontSize: "0.7rem" }}>● 실시간</span>}
      </h1>
      <p className="muted">{eng.goal}</p>
      <div className="muted" style={{ fontSize: "0.8rem", marginBottom: "0.8rem" }}>
        표적: <code>{target}</code>
        {r.web_port ? <> · 포트 <code>{r.web_port}</code></> : null}
        {" · "}권한 모드: {PERMISSION_LABEL[eng.permission_mode] || eng.permission_mode}
        {eng.error && <span style={{ color: "var(--danger, #ff6b74)" }}>{" · 오류: " + eng.error}</span>}
      </div>

      {awaiting && (
        <div className="dashboard-section" style={{ marginBottom: "0.8rem", padding: "0.7rem 0.9rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <IconPause size={16} /> 승인 대기 — 다음 명령은 침투성이라 사람 승인이 필요합니다:
          </div>
          <div style={{ margin: "0.4rem 0" }}><code>{pending || r.pending_command}</code></div>
          {approveErr && <div className="alert-error" style={{ marginBottom: "0.5rem" }}>{approveErr}</div>}
          {canApprove ? (
            <button className="btn-primary" onClick={approve} disabled={approving}>
              {approving ? "재개 중..." : "승인하고 실행"}
            </button>
          ) : (
            <span className="muted">승인하려면 manager 권한이 필요합니다.</span>
          )}
          <span className="muted" style={{ fontSize: "0.72rem", marginLeft: "0.6rem" }}>
            승인 시 정찰(nmap/nikto)은 다시 돌고 이어서 이 명령이 실행됩니다.
          </span>
        </div>
      )}

      <div className="dashboard-section">
        <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap" }}>
          <Stat label="실행 스텝" value={r.steps ?? "-"} />
          <Stat label="발견" value={(r.findings || []).length} />
        </div>
      </div>

      {(r.findings || []).length > 0 && (
        <div className="dashboard-section" style={{ marginTop: "1.2rem" }}>
          <SectionHead icon={IconFinding}>발견 ({r.findings.length})</SectionHead>
          <div className="table-scroll">
          <table className="repo-table">
            <thead><tr><th>심각도</th><th>제목</th><th>근거</th><th>소스 위치</th></tr></thead>
            <tbody>
              {r.findings.map((f, i) => (
                <tr key={i}>
                  <td><span className={`badge ${SEV_BADGE[f.severity] || ""}`}>{f.severity || "?"}</span></td>
                  <td>{f.title}</td>
                  <td className="muted" style={{ fontSize: "0.78rem" }}>{f.evidence}</td>
                  <td className="muted" style={{ fontSize: "0.75rem", fontFamily: "monospace" }}>
                    {f.file ? `${f.file}:${f.line ?? "?"}` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {(r.endpoints || []).length > 0 && (
        <div className="dashboard-section" style={{ marginTop: "1.2rem" }}>
          <SectionHead icon={IconEndpoint}>점검 범위 (엔드포인트 → 소스)</SectionHead>
          <p className="muted" style={{ fontSize: "0.8rem", marginTop: "-0.3rem" }}>
            사원이 확인할 파일 범위입니다 — 발견 유무와 무관하게 점검한 엔드포인트의 소스 위치.
          </p>
          <div className="table-scroll">
            <table className="repo-table">
              <thead><tr><th>메서드</th><th>경로</th><th>인증</th><th>소스 위치</th></tr></thead>
              <tbody>
                {r.endpoints.map((e, i) => (
                  <tr key={i}>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{e.method}</td>
                    <td style={{ fontFamily: "monospace", fontSize: "0.78rem" }}>{e.path}</td>
                    <td className="muted" style={{ fontSize: "0.75rem" }}>{e.auth ? "인증필요" : "공개"}</td>
                    <td className="muted" style={{ fontSize: "0.75rem", fontFamily: "monospace" }}>
                      {e.file ? `${e.file}:${e.line ?? "?"}` : "-"}
                      {e.handler ? ` (${e.handler})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {(r.history || []).length > 0 && (
        <div className="dashboard-section" style={{ marginTop: "1.2rem" }}>
          <SectionHead icon={IconScan}>실행 타임라인</SectionHead>
          <EngageTimeline steps={r.history} />
        </div>
      )}

      {r.markdown && (
        <div className="dashboard-section" style={{ marginTop: "1.2rem" }}>
          <details>
            <summary style={{ cursor: "pointer", display: "flex", alignItems: "center", gap: "0.45rem" }}>
              <IconReport size={16} /> 원문 리포트 (markdown)
            </summary>
            <div style={{ marginTop: "0.6rem" }}><MarkdownAnswer content={r.markdown} /></div>
          </details>
        </div>
      )}
    </div>
  );
}
