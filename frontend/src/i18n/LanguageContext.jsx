import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import * as pageTranslator from "./pageTranslator";

// 화면 언어(KR/EN) 상태. EN이면 pageTranslator가 브라우저 내장 번역기로 화면 전체를 영어로 바꾼다.
// status: idle | downloading | translating | unsupported | error

const STORAGE_KEY = "neosdl.lang";
const LanguageContext = createContext(null);

function readSaved() {
  try {
    return localStorage.getItem(STORAGE_KEY) === "en" ? "en" : "ko";
  } catch {
    return "ko";
  }
}

function save(lang) {
  try {
    localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // 저장이 막힌 브라우저 — 이번 탭에서만 유지
  }
}

export function LanguageProvider({ children }) {
  const [lang, setLangState] = useState("ko");
  const [status, setStatus] = useState(pageTranslator.isSupported() ? "idle" : "unsupported");
  const [progress, setProgress] = useState(0);
  const busyRef = useRef(false);

  const onBusy = useCallback((busy) => {
    if (busyRef.current === busy) return;
    busyRef.current = busy;
    setStatus((s) => (s === "downloading" ? s : busy ? "translating" : "idle"));
  }, []);

  const enable = useCallback(async () => {
    const avail = await pageTranslator.availability();
    if (avail === "unavailable") {
      setStatus("unsupported");
      return false;
    }
    if (avail !== "available") {
      setProgress(0);
      setStatus("downloading");
    }
    try {
      await pageTranslator.ensureTranslator(setProgress);
    } catch {
      setStatus("error");
      return false;
    }
    setStatus("idle");
    pageTranslator.start(onBusy);
    setLangState("en");
    return true;
  }, [onBusy]);

  const setLang = useCallback(async (next) => {
    if (next === "en") {
      if (await enable()) save("en");
    } else {
      pageTranslator.revert();
      setLangState("ko");
      setStatus(pageTranslator.isSupported() ? "idle" : "unsupported");
      save("ko");
    }
  }, [enable]);

  // 새로고침 후 EN이었다면 — 모델이 이미 받아져 있을 때만 자동 적용(다운로드는 클릭이 필요)
  useEffect(() => {
    if (readSaved() !== "en" || !pageTranslator.isSupported()) return;
    pageTranslator.availability().then((avail) => {
      if (avail === "available") enable();
    });
  }, [enable]);

  const value = useMemo(() => ({ lang, status, progress, setLang }), [lang, status, progress, setLang]);
  return <LanguageContext.Provider value={value}>{children}</LanguageContext.Provider>;
}

export function useLanguage() {
  return useContext(LanguageContext);
}
