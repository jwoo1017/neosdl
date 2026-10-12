import { useEffect, useState } from "react";
import { fetchProjectRepositories } from "../api";
import { useMascot } from "../MascotContext";

function storageKey(projectId) {
  return `neosdl_active_repo_${projectId}`;
}

export default function RepositorySelect({ projectId, value, onChange }) {
  const [repos, setRepos] = useState(null);
  const { trackRepoView, untrackRepoView, setRepoLabel } = useMascot();

  useEffect(() => {
    setRepos(null);
    fetchProjectRepositories(projectId)
      .then((list) => {
        setRepos(list);
        const saved = localStorage.getItem(storageKey(projectId));
        const initial = list.find((r) => r.id === saved)?.id || list[0]?.id || null;
        onChange(initial);
      })
      .catch(() => setRepos([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // 이 select가 "지금 어떤 저장소를 보고 있는지"를 마스코트에 알린다 — 같은
  // 저장소가 여러 pane에 동시에 열려도 refCount로 안전하게 등록/해제된다.
  useEffect(() => {
    if (!value) return undefined;
    trackRepoView(projectId, value);
    return () => untrackRepoView(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, value]);

  // 마스코트 패널의 저장소 스위처가 UUID 대신 "namespace/name"을 보여줄 수
  // 있도록, 목록이 로드되면 라벨만 별도로 채운다(위 추적 등록과는 독립적).
  useEffect(() => {
    if (!repos || !value) return;
    const repo = repos.find((r) => r.id === value);
    if (repo) setRepoLabel(value, `${repo.namespace}/${repo.name}`);
  }, [repos, value, setRepoLabel]);

  const handleChange = (e) => {
    const id = e.target.value;
    localStorage.setItem(storageKey(projectId), id);
    onChange(id);
  };

  if (repos === null) return <p className="muted">레포지토리 불러오는 중...</p>;
  if (repos.length === 0) return <p className="muted">이 프로젝트에 연결된 레포지토리가 없습니다.</p>;

  return (
    <select className="repo-select" value={value || ""} onChange={handleChange}>
      {repos.map((r) => (
        <option key={r.id} value={r.id}>
          {r.namespace}/{r.name}
        </option>
      ))}
    </select>
  );
}
