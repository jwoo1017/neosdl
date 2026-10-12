import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchScanRisk, fetchScans } from "../api";
import { useProject } from "../ProjectContext";
import RepositorySelect from "../components/RepositorySelect";
import {
  DIMENSION_LABELS, REPRESENTATION_LABELS, exposurePillClass, scoreBucket, topDimension,
} from "../riskDisplay";

// Risk 목록 — 스캔의 function/method 별 Risk Object. 대부분 RAW(외부 전송 허용),
// 소수만 제한. 행 클릭 → 심볼 드릴다운(RiskSymbolPage). 분석이 안 됐으면 백그라운드
// 잡(risk.build)이 돌 때까지 폴링한다.
export default function SecurityRiskPage() {
  const { activeProject } = useProject();
  const navigate = useNavigate();
  const [repositoryId, setRepositoryId] = useState(null);
  const [scanId, setScanId] = useState(null);
  const [data, setData] = useState(null); // null=loading | {status:"analyzing"} | {status:"ready",...}
  const [error, setError] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    if (!repositoryId || !activeProject) return;
    setScanId(null);
    setData(null);
    setError(null);
    fetchScans(activeProject.id, repositoryId)
      .then((scans) => {
        if (scans.length === 0) {
          setError("이 레포지토리에 아직 스캔이 없습니다 — 대시보드에서 먼저 스캔을 실행하세요.");
          return;
        }
        setScanId(scans[0].id);
      })
      .catch((err) => setError(err.message));
  }, [repositoryId, activeProject]);

  useEffect(() => {
    if (!scanId) return undefined;
    let tries = 0;
    let cancelled = false;

    const load = () => {
      fetchScanRisk(scanId)
        .then((res) => {
          if (cancelled) return;
          setData(res);
          if (res.status === "analyzing" && tries < 20) {
            tries += 1;
            pollRef.current = setTimeout(load, 5000);
          }
        })
        .catch((err) => !cancelled && setError(err.message));
    };
    load();

    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [scanId]);

  const refresh = () => {
    if (pollRef.current) clearTimeout(pollRef.current);
    setData(null);
    fetchScanRisk(scanId).then(setData).catch((err) => setError(err.message));
  };

  if (!activeProject) return <div className="dashboard">프로젝트를 먼저 선택하세요.</div>;

  return (
    <div className="dashboard">
      <h1>Risk</h1>
      <p className="muted">
        이 스캔의 함수별 Business/IP · 보안 · PII · 비밀정보 위험과, 현재 외부 AI에
        어느 수준까지 공개 가능한지(Exposure)를 보여줍니다.
      </p>
      <RepositorySelect projectId={activeProject.id} value={repositoryId} onChange={setRepositoryId} />
      {error && <div className="alert-error">{error}</div>}

      {scanId && data === null && <p className="muted">불러오는 중...</p>}

      {data?.status === "analyzing" && (
        <div className="dashboard-empty">
          Risk 분석이 백그라운드에서 진행 중입니다.
          <button className="btn-primary" style={{ marginLeft: "0.75rem" }} onClick={refresh}>
            새로고침
          </button>
        </div>
      )}

      {data?.status === "ready" && (
        <RiskReady data={data} navigate={navigate} projectId={activeProject.id} />
      )}
    </div>
  );
}

function RiskReady({ data, navigate, projectId }) {
  const { summary, risk_objects: objects } = data;

  if (!objects.length) {
    return <div className="dashboard-empty">Risk Object가 없습니다.</div>;
  }

  return (
    <>
      <div className="risk-summary">
        <span className="badge">{summary.total}개 심볼</span>
        <span className="badge badge-risk-high">외부 원문 차단 {summary.external_raw_denied}</span>
        {summary.specialized_model_required > 0 && (
          <span className="badge badge-risk-medium">보안 특화모델 {summary.specialized_model_required}</span>
        )}
        {Object.entries(summary.by_representation || {}).map(([rep, n]) => (
          <span key={rep} className={`pill ${exposurePillClass(rep)}`}>
            {REPRESENTATION_LABELS[rep] || rep} {n}
          </span>
        ))}
      </div>

      <div className="table-scroll">
        <table className="repo-table">
          <thead>
            <tr>
              <th>심볼</th>
              <th>최고 위험</th>
              <th>분류</th>
              <th>Exposure</th>
              <th>신뢰도</th>
            </tr>
          </thead>
          <tbody>
            {objects.map((o) => {
              const top = topDimension(o.risk);
              const bucket = scoreBucket(top.score);
              const rep = o.exposure.allowed_representation;
              return (
                <tr
                  key={o.target.symbol_key}
                  className="risk-row"
                  onClick={() =>
                    navigate(
                      `/projects/${projectId}/detail/security/risk/` +
                        `${encodeURIComponent(o.target.symbol_key)}?scan=${data.scan_id}`,
                    )
                  }
                >
                  <td>
                    <div>{o.target.symbol}</div>
                    <div className="muted" style={{ fontSize: "0.72rem" }}>{o.target.file}</div>
                  </td>
                  <td>
                    <span className={`badge ${bucket.cls}`}>{bucket.label}</span>{" "}
                    {top.dim && <span className="muted">{DIMENSION_LABELS[top.dim]}</span>}
                  </td>
                  <td className="muted">{o.classification?.business_ip_class || "–"}</td>
                  <td>
                    <span className={`pill ${exposurePillClass(rep)}`}>
                      {REPRESENTATION_LABELS[rep] || rep}
                    </span>
                    {!o.exposure.external_raw_allowed && (
                      <span className="muted" style={{ marginLeft: "0.4rem" }}>🔒</span>
                    )}
                  </td>
                  <td className="muted">{Math.round((top.confidence || 0) * 100)}%</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
