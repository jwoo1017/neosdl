import { useEffect, useRef, useState } from "react";
import { fetchTeamspacePresence, sendPresence, sendPresenceOfflineBeacon } from "../api";
import Avatar from "./Avatar";
import { ROLE_LABEL, STATUS_META } from "./avatarMeta";

// 상단바(우편함 왼쪽)의 "지금 이 팀스페이스에서 활동 중인 멤버" — 겹친 아바타 + 상태 점 + 역할 모자.
// 하트비트도 여기서 보낸다: 30초마다 {보고 있는 팀스페이스, active|idle}.
//  - active: 탭이 보이고 최근 5분 안에 마우스/키보드 입력이 있음
//  - idle:   탭이 가려졌거나 5분 넘게 입력 없음(자리 비움)
//  - 탭을 닫으면 sendBeacon 으로 offline(서버가 행 삭제). 비정상 종료는 서버가 90초 뒤 제외.
const HEARTBEAT_MS = 30_000;
const IDLE_AFTER_MS = 5 * 60_000;
const MAX_SHOWN = 5;

export default function PresenceStack({ teamspaceId }) {
  const [members, setMembers] = useState([]);
  const lastInputRef = useRef(0); // 마지막 입력 시각 — 마운트 시점부터 활동 중으로 본다(아래 effect)

  // 입력이 있을 때마다 마지막 활동 시각만 갱신(렌더링 없음).
  useEffect(() => {
    const touch = () => {
      lastInputRef.current = Date.now();
    };
    touch();
    const events = ["pointerdown", "keydown", "mousemove", "wheel", "touchstart"];
    events.forEach((e) => window.addEventListener(e, touch, { passive: true }));
    return () => events.forEach((e) => window.removeEventListener(e, touch));
  }, []);

  useEffect(() => {
    let cancelled = false;
    const currentState = () =>
      document.visibilityState === "visible" && Date.now() - lastInputRef.current < IDLE_AFTER_MS ? "active" : "idle";

    const refresh = () => {
      if (!teamspaceId) {
        setMembers([]);
        return;
      }
      fetchTeamspacePresence(teamspaceId)
        .then((list) => {
          if (!cancelled) setMembers(list);
        })
        .catch(() => {});
    };
    const beat = () => {
      sendPresence(teamspaceId, currentState())
        .catch(() => {})
        .finally(refresh); // 내 상태가 반영된 목록을 바로 다시 받는다
    };

    beat();
    const timer = setInterval(beat, HEARTBEAT_MS);
    const onVisibility = () => beat(); // 탭 전환 즉시 active/idle 반영
    const onPageHide = () => sendPresenceOfflineBeacon();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("pagehide", onPageHide);
    return () => {
      cancelled = true;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("pagehide", onPageHide);
    };
  }, [teamspaceId]);

  if (!teamspaceId || members.length === 0) return null;

  const shown = members.slice(0, MAX_SHOWN);
  const rest = members.length - shown.length;
  const summary = members
    .map((m) => `${m.nickname} — ${ROLE_LABEL[m.role] || ""} · ${STATUS_META[m.state]?.label || ""}`)
    .join("\n");

  return (
    <div className="presence-stack" aria-label={`활동 중인 멤버 ${members.length}명: ${summary}`}>
      {shown.map((m) => (
        <Avatar key={m.id} profile={m} size={30} status={m.state} role={m.role} className="presence-avatar" />
      ))}
      {rest > 0 && <span className="presence-more">+{rest}</span>}
    </div>
  );
}
