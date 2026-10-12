import MascotFace from "./MascotFace";
import MascotSpeechBubble from "./MascotSpeechBubble";
import { useMascot } from "../MascotContext";

export default function MascotButton() {
  const { totalUnread, isThinking, openPanel, activeRepoId, panelOpen } = useMascot();

  // 패널이 열려 있는 동안은 패널 헤더 좌측의 아바타가 이 역할을 대신한다 —
  // 화면에 마스코트가 두 곳에 동시에 보이지 않게 한다.
  if (panelOpen) return null;

  return (
    <div className="mascot-root">
      <MascotSpeechBubble />
      <button
        type="button"
        className="mascot-button"
        onClick={() => openPanel(activeRepoId)}
        aria-label="NeoSDL 보안 알림 열기"
      >
        <MascotFace state={isThinking ? "thinking" : "idle"} />
        {totalUnread > 0 && (
          <span className="mascot-badge">{totalUnread > 99 ? "99+" : totalUnread}</span>
        )}
        <span className="mascot-hover-tip">
          {totalUnread > 0 ? `새 알림 ${totalUnread}건` : "보안 알림 열기"}
        </span>
      </button>
    </div>
  );
}
