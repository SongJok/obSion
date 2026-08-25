export function Logo({ compact = false }: { compact?: boolean }) {
  return (
    <div className="logo" aria-label="Obsion">
      <span className="logo-mark" aria-hidden="true">
        <i />
      </span>
      {!compact && (
        <span className="logo-word">
          Obsion <small>WORKSPACE</small>
        </span>
      )}
    </div>
  );
}
