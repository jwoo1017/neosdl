import { useState } from "react";
import { useMe } from "../MeContext";
import { useWorkspace } from "../WorkspaceContext";
import { useTeamspace } from "../TeamspaceContext";
import { useProject } from "../ProjectContext";
import CreateTeamspaceModal from "../components/CreateTeamspaceModal";
import CreateProjectModal from "../components/CreateProjectModal";
import { ChatIcon, CheckIcon, RepoIcon, ShieldCheckIcon, TeamspaceIcon } from "../icons/SpaceIcons";
import logoUrl from "../assets/logo.svg?url";

// 프로젝트와 무관한 시작 탭 — 팀스페이스가 하나도 없을 때 사이드바가 자동으로 연다.
// 인사말 + 첫 세 단계(팀스페이스 → 프로젝트·레포 → 채팅/검증). 끝난 단계는 체크로 바뀐다.
export default function WelcomePane() {
  const { me } = useMe();
  const { activeWorkspace, activeWorkspaceId } = useWorkspace();
  const { teamspaces, personalSpace, reload: reloadTeamspaces } = useTeamspace();
  const { projects, reload: reloadProjects } = useProject();
  const [showCreateTeamspace, setShowCreateTeamspace] = useState(false);
  const [showCreateProject, setShowCreateProject] = useState(false);

  const hasTeamspace = (teamspaces || []).length > 0;
  const hasProject = (projects || []).length > 0;

  const steps = [
    {
      Icon: TeamspaceIcon,
      title: "팀스페이스 만들기",
      body: "함께 볼 프로젝트를 모아두는 공간입니다. 팀원을 초대하면 분석 결과와 채팅을 같이 봅니다.",
      done: hasTeamspace,
      action: activeWorkspaceId && { label: "팀스페이스 만들기", onClick: () => setShowCreateTeamspace(true) },
    },
    {
      Icon: RepoIcon,
      title: "프로젝트를 만들고 레포 연결",
      body: "GitHub·GitLab 레포를 연결하면 코드 구조와 보안 finding 을 분석합니다. 혼자 먼저 써보려면 '내 프로젝트'에 만드세요.",
      done: hasProject,
      action: personalSpace && { label: "내 프로젝트에 만들기", onClick: () => setShowCreateProject(true) },
    },
    {
      Icon: ChatIcon,
      title: "채팅으로 묻고, 검증까지",
      body: "프로젝트 채팅에서 코드에 대해 묻거나 \"이 레포 모의 해킹\"처럼 요청하면 샌드박스에서 실제로 재현해 오탐을 걸러냅니다.",
      done: false,
    },
  ];

  return (
    <div className="welcome-pane">
      <div className="welcome-inner">
        <img src={logoUrl} alt="NeoSDL" className="welcome-logo" />
        <h1 className="welcome-title">
          {me?.nickname ? `${me.nickname}님, 반갑습니다.` : "반갑습니다."}
        </h1>
        <p className="welcome-lead">
          NeoSDL 은 레포지토리를 연결하면 코드를 분석하고, 정적 분석에서 나온 보안 finding 을 샌드박스에서
          직접 재현해 진짜 취약점만 남기는 보안 개발 플랫폼입니다.
          {activeWorkspace?.name && <> 지금은 <strong>{activeWorkspace.name}</strong> 워크스페이스에 있습니다.</>}
        </p>

        <ol className="welcome-steps">
          {steps.map(({ Icon, title, body, done, action }, i) => (
            <li key={title} className={`welcome-step${done ? " is-done" : ""}`}>
              <span className="welcome-step-mark">
                {done ? <CheckIcon size={14} /> : <span className="welcome-step-num">{i + 1}</span>}
              </span>
              <div className="welcome-step-main">
                <div className="welcome-step-head">
                  <Icon size={16} className="welcome-step-icon" />
                  <span className="welcome-step-title">{title}</span>
                </div>
                <p className="welcome-step-body">{body}</p>
                {action && !done && (
                  <button type="button" className="mp-btn mp-btn-secondary welcome-step-btn" onClick={action.onClick}>
                    {action.label}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ol>

        <p className="welcome-foot">
          <ShieldCheckIcon size={14} />
          검증 run 은 manager 이상 권한에서 시작할 수 있고, 실행·파괴 action 은 기본적으로 사람 승인을 거칩니다.
        </p>
      </div>

      {showCreateTeamspace && (
        <CreateTeamspaceModal
          workspaceId={activeWorkspaceId}
          onCreated={() => reloadTeamspaces()}
          onClose={() => setShowCreateTeamspace(false)}
        />
      )}
      {showCreateProject && personalSpace && (
        <CreateProjectModal
          teamspaceId={personalSpace.id}
          onCreated={reloadProjects}
          onClose={() => setShowCreateProject(false)}
        />
      )}
    </div>
  );
}
