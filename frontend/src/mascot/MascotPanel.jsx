import { useMemo } from "react";
import { useMascot } from "../MascotContext";
import ActivityStoryBar from "./ActivityStoryBar";
import MascotFace from "./MascotFace";
import PanelCollapseIcon from "../icons/PanelCollapseIcon";

export default function MascotPanel() {
  const {
    panelOpen, closePanel, watchedRepos, repoLabels, activeRepoId, setActiveRepoId, markRepoSeen, isThinking,
  } = useMascot();
  const repoIds = useMemo(() => Object.keys(watchedRepos), [watchedRepos]);

  return (
    <div className={`mascot-panel ${panelOpen ? "mascot-panel-open" : ""}`} aria-hidden={!panelOpen}>
      <div className="mascot-panel-inner">
        <div className="mascot-panel-header">
          <div className="mascot-panel-title">
            <span className="mascot-panel-avatar">
              <MascotFace state={isThinking ? "thinking" : "idle"} />
            </span>
            <h3>NeoSDL 보안 알림</h3>
          </div>
          <button
            type="button"
            className="icon-btn mascot-panel-close"
            onClick={closePanel}
            aria-label="닫기"
            title="닫기"
          >
            <PanelCollapseIcon style={{ transform: "rotate(180deg)" }} />
          </button>
        </div>
        {repoIds.length > 1 && (
          <div className="mascot-panel-repo-switcher">
            <select
              value={activeRepoId || ""}
              onChange={(e) => {
                setActiveRepoId(e.target.value);
                markRepoSeen(e.target.value);
              }}
            >
              {repoIds.map((id) => (
                <option key={id} value={id}>{repoLabels[id] || id}</option>
              ))}
            </select>
          </div>
        )}
        <div className="mascot-panel-body">
          {activeRepoId ? (
            <ActivityStoryBar repositoryId={activeRepoId} />
          ) : (
            <p className="muted">저장소를 열면 활동 내역이 표시됩니다.</p>
          )}
        </div>
      </div>
    </div>
  );
}
