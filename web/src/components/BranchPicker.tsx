import { useEffect, useRef, useState } from 'react';

interface BranchPickerProps {
  branches: string[];
  value: string;
  onChange: (branch: string) => void;
  placeholder?: string;
}

export default function BranchPicker({ branches, value, onChange, placeholder = 'Select a branch...' }: BranchPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  function select(branch: string) {
    onChange(branch);
    setOpen(false);
    setQuery('');
  }

  const filtered = branches.filter((branch) => branch.toLowerCase().includes(query.toLowerCase()));

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        className="flex w-full min-w-[14rem] items-center justify-between rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-left text-sm text-slate-100"
      >
        <span className={value ? '' : 'text-slate-500'}>{value || placeholder}</span>
        <span className="text-slate-500">{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div className="absolute z-10 mt-1 w-72 rounded-md border border-slate-700 bg-slate-900 shadow-lg">
          <div className="border-b border-slate-800 p-2">
            <input
              autoFocus
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search branches..."
              className="w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1 text-sm text-slate-100"
            />
          </div>
          <div className="max-h-56 overflow-auto p-1">
            {filtered.length === 0 && (
              <p className="px-2 py-2 text-sm text-slate-500">No matching branches.</p>
            )}
            {filtered.map((branch) => (
              <button
                key={branch}
                type="button"
                onClick={() => select(branch)}
                className={`block w-full rounded px-2 py-1.5 text-left text-sm hover:bg-slate-800 ${
                  branch === value ? 'text-indigo-400' : 'text-slate-200'
                }`}
              >
                {branch}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
