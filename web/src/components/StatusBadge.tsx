const COLORS: Record<string, string> = {
  active: 'bg-emerald-500/15 text-emerald-400',
  success: 'bg-emerald-500/15 text-emerald-400',
  creating: 'bg-amber-500/15 text-amber-400',
  running: 'bg-amber-500/15 text-amber-400',
  queued: 'bg-slate-500/15 text-slate-400',
  deleting: 'bg-amber-500/15 text-amber-400',
  error: 'bg-rose-500/15 text-rose-400',
  failed: 'bg-rose-500/15 text-rose-400',
  suspended: 'bg-slate-500/15 text-slate-400',
};

export default function StatusBadge({ status }: { status: string }) {
  const color = COLORS[status] ?? 'bg-slate-500/15 text-slate-400';
  return (
    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}>
      {status}
    </span>
  );
}
