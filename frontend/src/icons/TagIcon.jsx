export default function TagIcon({ size = 12, ...props }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      {...props}
    >
      <path d="M12.6 3.4h5.6a1.4 1.4 0 0 1 1.4 1.4v5.6a1.4 1.4 0 0 1-.41 1L11 19.6a1.4 1.4 0 0 1-2 0L3.4 14a1.4 1.4 0 0 1 0-2l8.2-8.2a1.4 1.4 0 0 1 1-.4z" />
      <circle cx="16.2" cy="7.8" r="1.15" fill="currentColor" stroke="none" />
    </svg>
  );
}
