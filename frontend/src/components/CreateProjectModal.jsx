import { useEffect, useState } from "react";
import {
  attachRepositoryToProject,
  createProject,
  fetchConfig,
  fetchInstallationRepositories,
  fetchMyInstallations,
  fetchTeamspace,
  generateProjectSpec,
  githubAppInstallUrl,
  registerRepository,
  requestScan,
} from "../api";
import { requestNotificationPermission, watchProjectAnalysis } from "../analysis/analysisWatch";
import Modal from "./Modal";
import TagIcon from "../icons/TagIcon";

const STEPS = ["basics", "lead", "repo", "review"];
const STEP_LABELS = { basics: "소개", lead: "담당자", repo: "레포지토리", review: "완료" };
const DISCIPLINE_LABEL = { pm: "PM", frontend: "프론트엔드", backend: "백엔드" };
const LEAD_ROLES = ["manager", "developer", "viewer"];

export default function CreateProjectModal({ teamspaceId, onCreated, onClose }) {
  const [step, setStep] = useState("basics");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [leadMemberId, setLeadMemberId] = useState("");
  const [leadRole, setLeadRole] = useState("manager");

  // 담당자 후보는 이 project가 속할 teamspace의 멤버로 한정한다 — 직무(PM/
  // frontend/backend)는 teamspace 합류 단계에서 이미 정해져 있으므로, 여기선
  // 그중 누구를 어떤 project 권한으로 앉힐지만 고르면 된다.
  const [members, setMembers] = useState(null);
  const [githubAppSlug, setGithubAppSlug] = useState(null);

  const [installations, setInstallations] = useState(null);
  const [installationId, setInstallationId] = useState(null);
  const [repos, setRepos] = useState(null);
  const [selectedRepo, setSelectedRepo] = useState(null);

  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const [warning, setWarning] = useState(null);
  const [spec, setSpec] = useState(null);
  // 저장소 연결 직후 자동 분석(app/projects.py) 결과 — started | {projectId, repositoryId} 재시도 대상
  const [scanState, setScanState] = useState(null); // null | "started" | "retrying" | {projectId, repositoryId}

  useEffect(() => {
    fetchTeamspace(teamspaceId).then((t) => setMembers(t.members)).catch((err) => setError(err.message));
    fetchConfig().then((c) => setGithubAppSlug(c.github_app_slug)).catch(() => {});
  }, [teamspaceId]);

  useEffect(() => {
    if (step !== "repo" || installations !== null) return;
    fetchMyInstallations()
      .then((list) => {
        setInstallations(list);
        setInstallationId(list[0]?.id || null);
      })
      .catch((err) => setError(err.message));
  }, [step, installations]);

  useEffect(() => {
    if (!installationId) return;
    fetchInstallationRepositories(installationId).then(setRepos).catch((err) => setError(err.message));
  }, [installationId]);

  const connectInstallation = () => {
    if (!githubAppSlug) return;
    window.location.href = githubAppInstallUrl(githubAppSlug);
  };

  const goNext = () => setStep(STEPS[STEPS.indexOf(step) + 1]);
  const goBack = () => setStep(STEPS[STEPS.indexOf(step) - 1]);

  const finish = async () => {
    if (selectedRepo) requestNotificationPermission(); // 클릭 안에서만 물을 수 있다
    setCreating(true);
    setError(null);
    setWarning(null);
    try {
      const project = await createProject(teamspaceId, name.trim(), {
        description: description.trim() || undefined,
        leadMemberId: leadMemberId || undefined,
        leadRole: leadMemberId ? leadRole : undefined,
      });
      await onCreated();

      if (selectedRepo) {
        try {
          const registered = selectedRepo.already_registered
            ? { id: selectedRepo.already_registered }
            : await registerRepository(installationId, selectedRepo);
          const attached = await attachRepositoryToProject(project.id, registered.id);
          if (attached.scan) {
            watchProjectAnalysis(project.id, name.trim());
            setScanState("started");
          } else if (attached.scan_error) {
            setScanState({ projectId: project.id, repositoryId: registered.id });
          }
        } catch (err) {
          setWarning(`레포지토리 연결에 실패했습니다: ${err.message} (프로젝트 설정에서 다시 연결할 수 있습니다)`);
        }
      }

      try {
        const result = await generateProjectSpec(project.id, {
          repoLanguage: selectedRepo?.language,
          repoDescription: selectedRepo?.description,
          repoTopics: selectedRepo?.topics,
        });
        setSpec(result);
        await onCreated();
      } catch (err) {
        setWarning((w) => w || `자동 스펙 정리에 실패했습니다: ${err.message}`);
      }

      setStep("done");
    } catch (err) {
      setError(err.message);
    } finally {
      setCreating(false);
    }
  };

  const retryScan = async () => {
    const target = scanState;
    setScanState("retrying");
    try {
      await requestScan(target.repositoryId);
      watchProjectAnalysis(target.projectId, name.trim());
      setScanState("started");
    } catch {
      setScanState(target);
    }
  };

  return (
    <Modal title="새 프로젝트" onClose={onClose}>
      {step !== "done" && (
        <div className="wizard-steps">
          {STEPS.map((s) => (
            <span key={s} className={`wizard-step ${s === step ? "active" : ""}`}>
              {STEP_LABELS[s]}
            </span>
          ))}
        </div>
      )}

      {error && <div className="alert-error">{error}</div>}

      {step === "basics" && (
        <>
          <div className="auth-form">
            <input
              autoFocus
              placeholder="프로젝트 이름"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
            <textarea
              placeholder="프로젝트 소개 (선택 — 자동 스펙 정리에 쓰입니다)"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
            />
          </div>
          <div className="wizard-actions">
            <span />
            <button className="btn-primary" disabled={!name.trim()} onClick={goNext}>
              다음
            </button>
          </div>
        </>
      )}

      {step === "lead" && (
        <>
          <div className="auth-form">
            <select value={leadMemberId} onChange={(e) => setLeadMemberId(e.target.value)}>
              <option value="">담당자 선택 안 함</option>
              {(members || []).map((m) => (
                <option key={m.member_id} value={m.member_id}>
                  {m.nickname}
                  {m.discipline ? ` · ${DISCIPLINE_LABEL[m.discipline]}` : ""}
                </option>
              ))}
            </select>
            {members !== null && members.length === 0 && (
              <p className="muted">이 팀스페이스엔 아직 다른 멤버가 없습니다.</p>
            )}
            {leadMemberId && (
              <select value={leadRole} onChange={(e) => setLeadRole(e.target.value)}>
                {LEAD_ROLES.map((role) => (
                  <option key={role} value={role}>
                    프로젝트 권한: {role}
                  </option>
                ))}
              </select>
            )}
          </div>
          <div className="wizard-actions">
            <button onClick={goBack}>이전</button>
            <button className="btn-primary" onClick={goNext}>
              다음
            </button>
          </div>
        </>
      )}

      {step === "repo" && (
        <>
          {installations === null ? (
            <p className="muted">불러오는 중...</p>
          ) : (
            <div className="install-tab-strip">
              {installations.map((inst) => (
                <button
                  key={inst.id}
                  className={`install-tab ${inst.id === installationId ? "active" : ""}`}
                  onClick={() => setInstallationId(inst.id)}
                >
                  {inst.account_login}
                </button>
              ))}
              {githubAppSlug && (
                <button className="install-tab install-tab-add" onClick={connectInstallation}>
                  + GitHub App 설치 연결하기
                </button>
              )}
            </div>
          )}

          {installationId && (
            repos === null ? (
              <p className="muted">레포지토리 불러오는 중...</p>
            ) : (
              <table className="repo-table">
                <tbody>
                  {repos.map((repo) => (
                    <tr key={repo.remote_id}>
                      <td>{repo.full_name}</td>
                      <td className="muted">{repo.language || ""}</td>
                      <td>
                        <button
                          onClick={() => setSelectedRepo(repo)}
                          disabled={selectedRepo?.remote_id === repo.remote_id}
                        >
                          {selectedRepo?.remote_id === repo.remote_id ? "선택됨" : "선택"}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}

          <div className="wizard-actions">
            <button onClick={goBack}>이전</button>
            <button className="btn-primary" onClick={finish} disabled={creating}>
              {creating ? "만드는 중..." : selectedRepo ? "완료" : "건너뛰고 완료"}
            </button>
          </div>
        </>
      )}

      {step === "done" && (
        <div className="dashboard-empty">
          <p>"{name}" 프로젝트를 만들었습니다.</p>
          {warning && <div className="alert-error">{warning}</div>}
          {scanState === "started" && (
            <p className="attach-scan-note">저장소 분석을 시작했어요. 끝나면 알려드릴게요 — 그 뒤로 채팅에서 질문할 수 있어요.</p>
          )}
          {scanState && typeof scanState === "object" && (
            <p className="attach-scan-note attach-scan-note-error">
              저장소는 연결했지만 분석을 시작하지 못했어요.{" "}
              <button type="button" className="link-btn" onClick={retryScan}>다시 분석</button>
            </p>
          )}
          {scanState === "retrying" && <p className="attach-scan-note">분석 요청 중...</p>}
          {spec?.summary && <p className="spec-summary">{spec.summary}</p>}
          {spec?.tags?.length > 0 && (
            <div className="tag-list">
              {spec.tags.map((tag) => (
                <span className="badge spec-tag-badge" key={tag}>
                  <TagIcon /> {tag}
                </span>
              ))}
            </div>
          )}
          <button className="btn-primary" onClick={onClose}>
            닫기
          </button>
        </div>
      )}
    </Modal>
  );
}
