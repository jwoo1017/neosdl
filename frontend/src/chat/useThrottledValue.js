import { useEffect, useRef, useState } from "react";

// 빠르게 바뀌는 값(스트리밍 토큰 누적 문자열)을 인터벌당 최대 1번만 렌더로
// 흘려보낸다. trailing-edge 를 보장하므로 마지막 전체 값은 항상 반영된다.
export function useThrottledValue(value, ms = 80) {
  const [throttled, setThrottled] = useState(value);
  const lastRef = useRef(0);
  const timerRef = useRef(null);

  useEffect(() => {
    const now = Date.now();
    const elapsed = now - lastRef.current;
    if (elapsed >= ms) {
      lastRef.current = now;
      setThrottled(value);
      return undefined;
    }
    timerRef.current = setTimeout(() => {
      lastRef.current = Date.now();
      setThrottled(value);
    }, ms - elapsed);
    return () => clearTimeout(timerRef.current);
  }, [value, ms]);

  return throttled;
}
