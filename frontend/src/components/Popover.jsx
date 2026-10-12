import { useEffect, useRef } from "react";

// 채팅 입력창의 "+" 메뉴 / "자동" 모델 메뉴 / 채팅 헤더의 세션·더보기 메뉴가
// 공유하는 가벼운 팝오버 — Modal.jsx(중앙 정렬 + 어두운 배경)와 달리 트리거
// 버튼 기준에 앵커되고 배경 없이 바깥 클릭/Escape로 닫힌다.
//
// `side`: 트리거 기준 위("top", 기본값 — 화면 아래쪽에 있는 입력창 툴바용)
// 아니면 아래("bottom" — 화면 위쪽에 있는 헤더용, 안 그러면 화면 밖으로 열림).
//
// `triggerRef`(연 버튼)를 넘기면 그 버튼 위 클릭은 "바깥 클릭"으로 안 친다 —
// 안 그러면 mousedown이 버튼의 click보다 먼저 document로 버블링해 onClose가
// 먼저 실행되고, 뒤이은 토글 버튼의 onClick이 곧바로 다시 열어 상태가
// 어긋난다(닫았다 바로 열리는 깜빡임).
export default function Popover({ align = "left", side = "top", width = 260, onClose, triggerRef, children }) {
  const panelRef = useRef(null);

  useEffect(() => {
    const onPointerDown = (e) => {
      if (panelRef.current && panelRef.current.contains(e.target)) return;
      if (triggerRef?.current && triggerRef.current.contains(e.target)) return;
      onClose();
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [onClose]);

  return (
    <div
      ref={panelRef}
      className={`chat-popover chat-popover-${align} chat-popover-${side}`}
      style={{ width }}
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </div>
  );
}
