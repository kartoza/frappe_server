import { FormEvent, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import type { SqlDatabase, SqlStatementResult } from '../api/types';

function isResultSet(result: SqlStatementResult): result is { columns: string[]; rows: unknown[][]; truncated: boolean } {
  return 'columns' in result;
}

export default function SqlPlayground() {
  const [databases, setDatabases] = useState<SqlDatabase[]>([]);
  const [database, setDatabase] = useState('');
  const [sql, setSql] = useState('');
  const [results, setResults] = useState<SqlStatementResult[] | null>(null);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);

  useEffect(() => {
    api.get<{ databases: SqlDatabase[] }>('/sql/databases/').then((data) => {
      setDatabases(data.databases);
      if (data.databases.length > 0) setDatabase(data.databases[0].name);
    });
  }, []);

  async function handleRun(event: FormEvent) {
    event.preventDefault();
    if (!database || !sql.trim()) return;
    setError('');
    setResults(null);
    setRunning(true);
    try {
      const result = await api.post<{ results: SqlStatementResult[] }>('/sql/query/', {
        database,
        sql,
      });
      setResults(result.results);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Query failed.');
    } finally {
      setRunning(false);
    }
  }

  return (
    <div>
      <h1 className="mb-2 text-2xl font-semibold">SQL Playground</h1>
      <p className="mb-6 text-sm text-slate-500">
        Full read/write access to the selected database — statements run immediately,
        with no undo. Separate multiple statements with &ldquo;;&rdquo;.
      </p>

      <form onSubmit={handleRun} className="space-y-4 rounded-lg border border-slate-800 bg-slate-900 p-6">
        <div>
          <label className="mb-1 block text-sm text-slate-400">Database</label>
          <select
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-slate-100"
            value={database}
            onChange={(e) => setDatabase(e.target.value)}
            required
          >
            <option value="" disabled>
              Select a database...
            </option>
            {databases.map((db) => (
              <option key={db.name} value={db.name}>
                {db.name}
                {db.site ? ` [${db.site}]` : ''}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className="mb-1 block text-sm text-slate-400">SQL</label>
          <textarea
            className="w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-sm text-slate-100"
            rows={8}
            value={sql}
            onChange={(e) => setSql(e.target.value)}
            placeholder="SELECT * FROM tabUser LIMIT 10;"
            required
          />
        </div>

        {error && <p className="whitespace-pre-wrap text-sm text-rose-400">{error}</p>}

        <button
          type="submit"
          disabled={running || !database}
          className="rounded-md bg-indigo-500 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-400 disabled:opacity-50"
        >
          {running ? 'Running...' : 'Run'}
        </button>
      </form>

      {results && (
        <div className="mt-6 space-y-6">
          {results.map((result, i) => (
            <div key={i} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
              <div className="mb-2 text-xs text-slate-500">Statement {i + 1}</div>
              {isResultSet(result) ? (
                result.rows.length > 0 ? (
                  <div className="overflow-x-auto">
                    <table className="w-full text-left text-sm">
                      <thead>
                        <tr className="border-b border-slate-800 text-slate-400">
                          {result.columns.map((col) => (
                            <th key={col} className="px-3 py-2 font-medium">
                              {col}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {result.rows.map((row, rowIndex) => (
                          <tr key={rowIndex} className="border-b border-slate-800/50">
                            {row.map((cell, cellIndex) => (
                              <td key={cellIndex} className="px-3 py-2 text-slate-200">
                                {cell === null ? (
                                  <span className="text-slate-500">NULL</span>
                                ) : (
                                  String(cell)
                                )}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {result.truncated && (
                      <p className="mt-2 text-xs text-amber-400">
                        Showing the first {result.rows.length} rows — more rows were returned by the
                        server.
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-slate-500">No rows.</p>
                )
              ) : (
                <p className="text-sm text-emerald-400">{result.affected_rows} row(s) affected.</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
