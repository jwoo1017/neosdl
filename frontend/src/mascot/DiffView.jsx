function classify(line) {
  if (line.startsWith("+++") || line.startsWith("---")) return "diff-line-file";
  if (line.startsWith("@@")) return "diff-line-hunk";
  if (line.startsWith("+")) return "diff-line-add";
  if (line.startsWith("-")) return "diff-line-remove";
  return "diff-line-context";
}

// 백엔드(app/fix_generation.py)가 difflib로 이미 계산한 unified diff 문자열을
// 렌더링만 한다 — 프론트에서 diff를 새로 계산하지 않는다.
export default function DiffView({ diff }) {
  if (!diff) return null;
  const lines = diff.split("\n");
  return (
    <pre className="diff-view">
      {lines.map((line, i) => (
        // eslint-disable-next-line react/no-array-index-key
        <div key={i} className={`diff-line ${classify(line)}`}>{line || " "}</div>
      ))}
    </pre>
  );
}
