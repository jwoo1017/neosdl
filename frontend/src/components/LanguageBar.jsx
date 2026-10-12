import { useEffect, useState } from "react";
import { fetchRepositoryLanguages } from "../api";

const MAX_SHOWN = 6;
const OTHER_COLOR = "#8b949e";

export default function LanguageBar({ repositoryId }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    fetchRepositoryLanguages(repositoryId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((err) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [repositoryId]);

  if (error) return <div className="language-bar-loading muted">언어 구성을 불러오지 못했습니다.</div>;
  if (!data) return <div className="language-bar-loading muted">언어 구성 불러오는 중...</div>;
  if (data.languages.length === 0) return null;

  const shown = data.languages.slice(0, MAX_SHOWN);
  const restBytes = data.languages.slice(MAX_SHOWN).reduce((sum, l) => sum + l.bytes, 0);
  const segments = restBytes > 0
    ? [...shown, {
        name: "기타",
        bytes: restBytes,
        percent: data.total_bytes ? Math.round((restBytes / data.total_bytes) * 1000) / 10 : 0,
        color: OTHER_COLOR,
      }]
    : shown;

  return (
    <div className="language-bar-wrap">
      <div className="language-bar">
        {segments.map((lang) => (
          <div
            key={lang.name}
            className="language-bar-segment"
            style={{ width: `${lang.percent}%`, backgroundColor: lang.color }}
            title={`${lang.name} ${lang.percent}%`}
          />
        ))}
      </div>
      <div className="language-bar-legend">
        {segments.map((lang) => (
          <span key={lang.name} className="language-bar-legend-item">
            <span className="language-bar-dot" style={{ backgroundColor: lang.color }} />
            {lang.name} {lang.percent}%
          </span>
        ))}
      </div>
    </div>
  );
}
