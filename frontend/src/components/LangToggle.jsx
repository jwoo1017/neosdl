import { useLanguage } from "../i18n/LanguageContext";
import { useToast } from "../toast/ToastContext";

// 상단바 오른쪽 KR/EN 전환 스위치 — 아이콘 없이 SVG로 직접 그린 알약형 트랙 + 미끄러지는 노브.
// 번역 엔진은 브라우저 내장 Translator API(i18n/pageTranslator.js). 스위치 자체는 번역 대상에서 뺀다.

const RING_R = 6;
const RING_LEN = 2 * Math.PI * RING_R;

const TITLES = {
  downloading: "번역 모델을 내려받는 중…",
  unsupported: "이 브라우저는 내장 번역을 지원하지 않습니다",
  error: "번역기를 시작하지 못했습니다",
};

export default function LangToggle() {
  const { lang, status, progress, setLang } = useLanguage();
  const toast = useToast();
  const en = lang === "en";
  const busy = status === "downloading";

  const onClick = () => {
    if (busy) return;
    if (status === "unsupported") {
      toast.show({
        kind: "error",
        title: "이 브라우저는 내장 번역을 지원하지 않습니다",
        body: "Chrome 또는 Edge 138 이상 데스크톱 버전에서 영어 보기를 쓸 수 있습니다.",
      });
      return;
    }
    setLang(en ? "ko" : "en");
  };

  const title = TITLES[status] ?? (en ? "Switch back to Korean" : "영어로 보기 (브라우저 내장 번역)");

  return (
    <button
      type="button"
      role="switch"
      aria-checked={en}
      aria-label={`Language: KR / EN — ${title}`}
      translate="no"
      className={`lang-toggle notranslate is-${status}${en ? " is-en" : ""}`}
      onClick={onClick}
      aria-busy={busy || status === "translating"}
    >
      <svg viewBox="0 0 64 28" width="64" height="28" aria-hidden="true" focusable="false">
        <rect className="lang-toggle-track" x="0.5" y="0.5" width="63" height="27" rx="13.5" />
        <g className="lang-toggle-knob">
          <rect x="3" y="3" width="30" height="22" rx="11" />
        </g>
        <text className={`lang-toggle-label${en ? "" : " is-on"}`} x="18" y="14" textAnchor="middle" dominantBaseline="central">KR</text>
        {busy ? (
          <g className="lang-toggle-progress" transform="translate(46 14) rotate(-90)">
            <circle r={RING_R} className="lang-toggle-progress-bg" />
            <circle
              r={RING_R}
              className="lang-toggle-progress-bar"
              strokeDasharray={RING_LEN}
              strokeDashoffset={RING_LEN * (1 - Math.min(1, progress || 0))}
            />
          </g>
        ) : (
          <text className={`lang-toggle-label${en ? " is-on" : ""}`} x="46" y="14" textAnchor="middle" dominantBaseline="central">EN</text>
        )}
      </svg>
    </button>
  );
}
