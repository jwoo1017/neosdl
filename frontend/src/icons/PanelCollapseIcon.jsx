export default function PanelCollapseIcon({ size = 14, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M17 6l-6 6 6 6" />
      <path d="M10 6l-6 6 6 6" />
    </svg>
  );
}
