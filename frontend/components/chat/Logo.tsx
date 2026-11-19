export function Logo({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden>
      <g fill="hsl(var(--brand))">
        {Array.from({ length: 8 }).map((_, i) => (
          <rect key={i} x="14.5" y="3" width="3" height="11" rx="1.5" transform={`rotate(${i * 45} 16 16)`} />
        ))}
      </g>
    </svg>
  );
}
