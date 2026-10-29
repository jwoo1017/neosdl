import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import GraphView from "../GraphView";
import { fetchScans } from "../api";
import { useProject } from "../ProjectContext";
import Select from "../components/Select";

const scanLabel = (s) =>
  `${s.repo_name ? `${s.repo_name} · ` : ""}${s.commit_sha.slice(0, 8)} · ${new Date(s.created_at).toLocaleString()}`;

// 코드 지도 — 스캔 결과(심볼·호출 관계)를 그래프로 본다.
// scanId 가 없으면(Overview 의 "코드 지도 열기") 가장 최근 성공 스캔을 바로 연다.
export default function GraphViewerPage() {
  const { scanId: routeScanId } = useParams();
  const [search] = useSearchParams();
  const repoFilter = search.get("repo");
  // 흐름 탭 "코드 지도에서 보기" — ?hl=path&keys=<줄바꿈으로 이은 심볼 키>&title=
  const hlKeysRaw = search.get("keys");
  const hlMode = search.get("hl");
  const hlTitle = search.get("title");
  const initialHighlight = useMemo(
    () => (hlMode && hlKeysRaw
      ? { mode: hlMode, keys: hlKeysRaw.split(String.fromCharCode(10)).filter(Boolean), title: hlTitle || null }
      : null),
    [hlMode, hlKeysRaw, hlTitle]
  );
  const navigate = useNavigate();
  const { activeProject } = useProject();
  const [debugScanId, setDebugScanId] = useState(routeScanId || "");
  const [scans, setScans] = useState(null); // null = 불러오는 중

  useEffect(() => {
    if (!activeProject) return;
    fetchScans(activeProject.id, repoFilter || undefined)
      .then((list) => setScans(list.filter((s) => s.status === "succeeded")))
      .catch(() => setScans([]));
  }, [activeProject?.id, repoFilter]);

  const load = (id) => {
    if (!id || !activeProject) return;
    navigate(`/projects/${activeProject.id}/detail/graph/${id}`, { replace: !routeScanId });
  };

  useEffect(() => {
    if (routeScanId) setDebugScanId(routeScanId);
    else if (scans?.length) load(scans[0].id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeScanId, scans]);

  const options = (scans || []).map((s) => ({ value: s.id, label: scanLabel(s) }));
  if (routeScanId && !options.some((o) => o.value === routeScanId)) {
    options.unshift({ value: routeScanId, label: `스캔 ${routeScanId.slice(0, 8)}` });
  }

  return (
    <div className="page">
      <header className="graph-page-header">
        <div className="graph-page-title">
          <h1>코드 지도</h1>
          <span className="muted">스캔한 코드의 함수·모듈이 어떻게 호출되고 연결되는지 보여줍니다.</span>
        </div>
        <div className="app-controls">
          {options.length > 0 && (
            <Select
              className="graph-scan-select"
              ariaLabel="스캔 버전 선택"
              value={routeScanId || ""}
              onChange={load}
              placeholder="스캔 버전 선택"
              options={options}
            />
          )}
          {import.meta.env.DEV && (
            <>
              <input
                placeholder="scan_id (개발용)"
                value={debugScanId}
                onChange={(e) => setDebugScanId(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && load(debugScanId)}
              />
              <button onClick={() => load(debugScanId)}>불러오기</button>
            </>
          )}
        </div>
      </header>
      <main className="app-main">
        {routeScanId ? (
          <GraphView scanId={routeScanId} initialHighlight={initialHighlight} />
        ) : scans === null ? (
          <div className="app-empty">최근 스캔을 찾는 중...</div>
        ) : (
          <div className="app-empty">
            아직 완료된 스캔이 없습니다. Overview 에서 레포지토리의 "스캔 실행"을 누르면 코드 지도가 만들어집니다.
          </div>
        )}
      </main>
    </div>
  );
}
