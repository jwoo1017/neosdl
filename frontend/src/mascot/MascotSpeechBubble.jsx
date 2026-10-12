import { useEffect } from "react";
import { useMascot } from "../MascotContext";

const AUTO_HIDE_MS = 8000;

export default function MascotSpeechBubble() {
  const { bubble, dismissBubble, openPanel } = useMascot();

  useEffect(() => {
    if (!bubble) return undefined;
    const timer = setTimeout(dismissBubble, AUTO_HIDE_MS);
    return () => clearTimeout(timer);
  }, [bubble, dismissBubble]);

  if (!bubble) return null;

  return (
    <button
      type="button"
      className="mascot-bubble"
      onClick={() => openPanel(bubble.repoId)}
    >
      {bubble.message}
    </button>
  );
}
