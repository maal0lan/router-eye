import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import {
  Activity,
  ShieldCheck,
  Layers3,
  KeyRound,
  LogOut,
  RefreshCw,
  Plus,
  CircleDollarSign,
  Zap,
  Cpu,
  Users,
  ArrowDownToLine,
  TrendingUp,
  Clock3,
  Trash2,
  Key,
} from 'lucide-react';
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  CartesianGrid,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import './styles.css';

const API = import.meta.env.VITE_API_BASE || '';

const KEY_PALETTE = [
  '#9b8cff', // Purple
  '#60d9ad', // Green / Emerald
  '#60a5fa', // Blue
  '#f59e0b', // Amber / Orange
  '#ec4899', // Pink
  '#06b6d4', // Cyan
  '#a855f7', // Violet
  '#10b981', // Mint
];

const fmtMoney = (value) =>
  new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 3,
  }).format(Number(value || 0));

const fmtNum = (value) =>
  new Intl.NumberFormat('en-US', {
    notation: Number(value || 0) > 999999 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(Number(value || 0));

const fmtFullNum = (value) =>
  new Intl.NumberFormat('en-US').format(Number(value || 0));

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };

  if (opts.body) headers['Content-Type'] = 'application/json';

  const csrf = sessionStorage.getItem('routerscope_csrf');
  if (['POST', 'DELETE'].includes((opts.method || 'GET').toUpperCase()) && csrf) {
    headers['X-CSRF-Token'] = csrf;
  }

  const response = await fetch(`${API}${path}`, {
    ...opts,
    headers,
    credentials: 'include',
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || `Request failed (${response.status})`);
  }

  return data;
}

function Login({ onLogin }) {
  const [username, setUsername] = useState('maalolan');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');

    try {
      const data = await api('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      });
      sessionStorage.setItem('routerscope_csrf', data.csrf);
      onLogin();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login-wrap">
      <div className="login-card">
        <div className="brand-mark"><Activity size={23} /></div>
        <div className="eyebrow">PRIVATE USAGE INTELLIGENCE</div>
        <h1>Welcome to RouterScope</h1>
        <p className="muted">Sign in to manage your OpenRouter accounts.</p>
        <form onSubmit={submit}>
          <label>
            Username
            <input
              value={username}
              onChange={(event) => setUsername(event.target.value)}
              autoComplete="username"
              required
            />
          </label>
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              autoComplete="current-password"
              required
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="primary full" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in securely'}
          </button>
        </form>
        <div className="login-foot">
          <ShieldCheck size={15} /> Keys are stored encrypted on the server
        </div>
      </div>
    </main>
  );
}

function Metric({ icon: Icon, label, value, detail, tint = 'violet' }) {
  return (
    <div className="metric card">
      <div className={`metric-icon ${tint}`}><Icon size={19} /></div>
      <div className="metric-label">{label}</div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">{detail}</div>
    </div>
  );
}

function CustomTokenTooltip({ active, payload, label, topKeyNames = [] }) {
  if (!active || !payload || !payload.length) return null;

  return (
    <div className="chart-tooltip">
      <div className="chart-tooltip-title">Date: {label}</div>
      {payload
        .filter((entry) => Number(entry.value || 0) > 0)
        .map((entry) => {
          return (
            <div className="chart-tooltip-row" key={entry.name}>
              <span className="chart-tooltip-label">
                <span className="legend-dot" style={{ background: entry.color }} />
                {entry.name}
              </span>
              <span className="chart-tooltip-value">
                {fmtFullNum(entry.value)} tok
              </span>
            </div>
          );
        })}
      {payload.every((entry) => Number(entry.value || 0) === 0) && (
        <div className="chart-tooltip-row" style={{ color: '#858ba3', fontSize: 11 }}>
          No token activity
        </div>
      )}
    </div>
  );
}

function App() {
  const [authenticated, setAuthenticated] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [dashboard, setDashboard] = useState(null);
  const [days, setDays] = useState(30);
  const [filter, setFilter] = useState('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [showAdd, setShowAdd] = useState(false);
  const [label, setLabel] = useState('');
  const [key, setKey] = useState('');
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState('');
  const [graphMode, setGraphMode] = useState('tokens'); // 'spend' | 'tokens'

  useEffect(() => {
    api('/api/auth/me')
      .then((data) => {
        setAuthenticated(data.authenticated);
        if (data.csrf) sessionStorage.setItem('routerscope_csrf', data.csrf);
      })
      .catch(() => setAuthenticated(false));
  }, []);

  async function load() {
    setLoading(true);
    setError('');

    try {
      const accountPath = '/api/accounts';
      const dashboardPath = `/api/dashboard?days=${days}${
        filter !== 'all' ? `&account_id=${encodeURIComponent(filter)}` : ''
      }`;
      const [accountData, dashboardData] = await Promise.all([
        api(accountPath),
        api(dashboardPath),
      ]);

      // Combine accounts with dashboard stats for accurate tokens/spend
      const statsMap = new Map();
      if (dashboardData && Array.isArray(dashboardData.accounts)) {
        for (const item of dashboardData.accounts) {
          statsMap.set(item.id, item);
        }
      }

      const mergedAccounts = accountData.map((acc) => {
        const stats = statsMap.get(acc.id) || {};
        return {
          ...acc,
          spend_usd: stats.spend_usd ?? 0,
          tokens: stats.tokens ?? 0,
          requests: stats.requests ?? 0,
        };
      });

      setAccounts(mergedAccounts);
      setDashboard(dashboardData);
    } catch (err) {
      setError(err.message);
      if (err.message.toLowerCase().includes('sign in')) {
        setAuthenticated(false);
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (authenticated) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authenticated, days, filter]);

  async function addAccount(event) {
    event.preventDefault();
    setAdding(true);
    setError('');

    try {
      await api('/api/accounts', {
        method: 'POST',
        body: JSON.stringify({ label, management_key: key }),
      });

      setLabel('');
      setKey('');
      setShowAdd(false);
      setNotice('Account connected. Initial usage collection may take a moment.');
      await api('/api/refresh', { method: 'POST' });
      await load();
    } catch (err) {
      setError(err.message);
    } finally {
      setAdding(false);
    }
  }

  async function refresh() {
    setLoading(true);
    setError('');

    try {
      await api('/api/refresh', { method: 'POST' });
      await load();
      setNotice('Usage refreshed successfully.');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function removeAccount(account) {
    if (!window.confirm(`Disconnect "${account.label}" and delete its stored history?`)) {
      return;
    }

    try {
      await api(`/api/accounts/${account.id}`, { method: 'DELETE' });
      await load();
      setNotice('Account disconnected and its stored history deleted.');
    } catch (err) {
      setError(err.message);
    }
  }

  async function logout() {
    try {
      await api('/api/auth/logout', { method: 'POST' });
    } finally {
      sessionStorage.removeItem('routerscope_csrf');
      setAuthenticated(false);
    }
  }

  const summary = dashboard?.summary || {};
  const series = dashboard?.series || [];
  const topKeys = dashboard?.top_keys || [];
  const keyTrend = dashboard?.key_trend || [];
  const topKeyNames = dashboard?.top_key_names || [];

  const exportCsv = () => {
    const rows = [
      ['day', 'spend_usd', 'requests', 'tokens'],
      ...series.map((row) => [row.day, row.spend_usd, row.requests, row.tokens]),
    ];
    const csv = rows.map((row) => row.join(',')).join('\n');
    const url = URL.createObjectURL(
      new Blob([csv], { type: 'text/csv;charset=utf-8;' }),
    );
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'routerscope-history.csv';
    anchor.click();
    URL.revokeObjectURL(url);
  };

  if (authenticated === null) {
    return <div className="loading-screen">Loading RouterScope…</div>;
  }

  if (!authenticated) {
    return <Login onLogin={() => setAuthenticated(true)} />;
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-mark"><Activity size={21} /></div>
          <span>Router<span className="logo-light">Scope</span></span>
          <span className="version">BETA</span>
        </div>
        <div className="side-label">WORKSPACE</div>
        <button className="nav-item active"><Layers3 size={17} /> Overview</button>
        <button className="nav-item" onClick={() => setShowAdd(true)}>
          <KeyRound size={17} /> Accounts <span className="nav-count">{accounts.length}</span>
        </button>
        <div className="side-bottom">
          <div className="secure-status">
            <span className="status-dot" />
            <div><b>Local encryption</b><small>Secrets protected at rest</small></div>
          </div>
          <button className="nav-item logout" onClick={logout}>
            <LogOut size={17} /> Sign out
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <div className="eyebrow">USAGE OVERVIEW</div>
            <h1>Your AI spend & tokens, in one place.</h1>
            <p className="muted">Monitor every connected OpenRouter account and API key from one dashboard.</p>
          </div>
          <div className="top-actions">
            <span className="live-pill"><span className="status-dot" /> PRIVATE WORKSPACE</span>
            <button className="icon-button" onClick={refresh} title="Refresh usage" disabled={loading}>
              <RefreshCw size={16} className={loading ? 'spin' : ''} />
            </button>
            <button className="primary" onClick={() => setShowAdd(true)}>
              <Plus size={17} /> Add account
            </button>
          </div>
        </header>

        {error && (
          <div className="alert error">
            <span>{error}</span>
            <button onClick={() => setError('')}>Dismiss</button>
          </div>
        )}
        {notice && (
          <div className="alert success">
            <span>{notice}</span>
            <button onClick={() => setNotice('')}>Dismiss</button>
          </div>
        )}

        <section className="toolbar">
          <div className="section-kicker"><span className="pulse-dot" /> LIVE MONITORING</div>
          <div className="toolbar-right">
            <label className="filter-label">
              Account
              <select value={filter} onChange={(event) => setFilter(event.target.value)}>
                <option value="all">All accounts</option>
                {accounts.map((account) => (
                  <option value={account.id} key={account.id}>{account.label}</option>
                ))}
              </select>
            </label>
            <div className="segmented">
              {[7, 30, 90].map((numberOfDays) => (
                <button
                  key={numberOfDays}
                  className={days === numberOfDays ? 'selected' : ''}
                  onClick={() => setDays(numberOfDays)}
                >
                  {numberOfDays}D
                </button>
              ))}
            </div>
          </div>
        </section>

        <section className="metrics-grid">
          <Metric icon={CircleDollarSign} label="TOTAL SPEND" value={fmtMoney(summary.spend_usd)} detail={`${days}-day window`} tint="violet" />
          <Metric icon={Zap} label="TOTAL REQUESTS" value={fmtNum(summary.requests)} detail="Across connected accounts" tint="blue" />
          <Metric icon={Cpu} label="TOKENS PROCESSED" value={fmtNum(summary.tokens)} detail="Prompt + completion" tint="cyan" />
          <Metric icon={Users} label="CREDIT BALANCE" value={fmtMoney(summary.credits_remaining)} detail={`${summary.accounts || 0} connected accounts`} tint="orange" />
        </section>

        {/* Top API Keys Leaderboard */}
        {topKeys.length > 0 && (
          <section className="keys-summary-grid">
            {topKeys.slice(0, 4).map((tk, idx) => {
              const color = KEY_PALETTE[idx % KEY_PALETTE.length];
              return (
                <div className="key-summary-pill" key={tk.key}>
                  <div className="key-summary-left">
                    <span className="key-summary-dot" style={{ background: color }} />
                    <Key size={13} style={{ color }} />
                    <span className="key-summary-name" title={tk.key}>{tk.key}</span>
                  </div>
                  <span className="key-summary-tokens">{fmtNum(tk.tokens)} tokens</span>
                </div>
              );
            })}
          </section>
        )}

        <section className="charts-grid">
          <div className="card chart-card">
            <div className="card-heading">
              <div>
                <h2>{graphMode === 'tokens' ? 'Token Usage Trend by API Key & Account' : 'Spend over time'}</h2>
                <p className="muted">
                  {graphMode === 'tokens'
                    ? 'Interactive color-coded daily token volume across your top API keys'
                    : 'Daily total usage across selected accounts'}
                </p>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                {/* Graph Mode Toggle */}
                <div className="segmented">
                  <button
                    type="button"
                    className={graphMode === 'tokens' ? 'selected' : ''}
                    onClick={() => setGraphMode('tokens')}
                  >
                    Tokens Trend
                  </button>
                  <button
                    type="button"
                    className={graphMode === 'spend' ? 'selected' : ''}
                    onClick={() => setGraphMode('spend')}
                  >
                    Spend ($)
                  </button>
                </div>
                <button className="subtle-button" onClick={exportCsv}>
                  <ArrowDownToLine size={15} /> Export CSV
                </button>
              </div>
            </div>

            <div className="chart-total">
              <strong>{graphMode === 'tokens' ? fmtFullNum(summary.tokens) : fmtMoney(summary.spend_usd)}</strong>
              <span className="chart-period">{graphMode === 'tokens' ? 'Tokens in last ' + days + ' days' : 'Last ' + days + ' days'}</span>
            </div>

            <div className="chart-area">
              {graphMode === 'tokens' ? (
                keyTrend.length ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                      data={keyTrend}
                      margin={{ top: 10, right: 12, left: 0, bottom: 0 }}
                    >
                      <defs>
                        {topKeyNames.map((keyName, idx) => {
                          const color = KEY_PALETTE[idx % KEY_PALETTE.length];
                          return (
                            <linearGradient
                              id={`grad-key-${idx}`}
                              x1="0"
                              y1="0"
                              x2="0"
                              y2="1"
                              key={keyName}
                            >
                              <stop offset="0%" stopColor={color} stopOpacity={0.4} />
                              <stop offset="95%" stopColor={color} stopOpacity={0.02} />
                            </linearGradient>
                          );
                        })}
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#242a3b" vertical={false} />
                      <XAxis
                        dataKey="day"
                        tickFormatter={(value) => value.slice(5)}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fill: '#858ba3', fontSize: 11 }}
                        minTickGap={26}
                      />
                      <YAxis
                        tickFormatter={fmtNum}
                        tickLine={false}
                        axisLine={false}
                        tick={{ fill: '#858ba3', fontSize: 11 }}
                        width={58}
                      />
                      <Tooltip
                        content={<CustomTokenTooltip topKeyNames={topKeyNames} />}
                      />
                      {topKeyNames.map((keyName, idx) => {
                        const color = KEY_PALETTE[idx % KEY_PALETTE.length];
                        return (
                          <Area
                            key={keyName}
                            type="monotone"
                            dataKey={keyName}
                            stroke={color}
                            strokeWidth={2}
                            fill={`url(#grad-key-${idx})`}
                            isAnimationActive={true}
                            animationDuration={800}
                            animationEasing="ease-out"
                            activeDot={{
                              r: 5,
                              fill: color,
                              stroke: '#151a2a',
                              strokeWidth: 2,
                            }}
                          />
                        );
                      })}
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div className="empty-chart">
                    <TrendingUp size={25} />
                    <b>No token history collected yet</b>
                    <span>Connect an account and refresh to start tracking token volume.</span>
                  </div>
                )
              ) : series.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={series} margin={{ top: 10, right: 12, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="#8b7cff" stopOpacity={0.28} />
                        <stop offset="95%" stopColor="#8b7cff" stopOpacity={0.01} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="#242a3b" vertical={false} />
                    <XAxis
                      dataKey="day"
                      tickFormatter={(value) => value.slice(5)}
                      tickLine={false}
                      axisLine={false}
                      tick={{ fill: '#858ba3', fontSize: 11 }}
                      minTickGap={26}
                    />
                    <YAxis
                      tickFormatter={(value) => `$${value}`}
                      tickLine={false}
                      axisLine={false}
                      tick={{ fill: '#858ba3', fontSize: 11 }}
                      width={54}
                    />
                    <Tooltip
                      contentStyle={{
                        background: '#151a2a',
                        border: '1px solid #30364b',
                        borderRadius: 12,
                        color: '#f5f6ff',
                      }}
                      formatter={(value, name) => [
                        name === 'spend_usd' ? fmtMoney(value) : fmtNum(value),
                        name === 'spend_usd' ? 'Spend' : name,
                      ]}
                      labelFormatter={(value) => `Date: ${value}`}
                    />
                    <Area
                      type="monotone"
                      dataKey="spend_usd"
                      stroke="#9b8cff"
                      strokeWidth={2.5}
                      fill="url(#spendFill)"
                      isAnimationActive={true}
                      animationDuration={800}
                      activeDot={{
                        r: 4,
                        fill: '#b3a8ff',
                        stroke: '#171a2a',
                        strokeWidth: 2,
                      }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="empty-chart">
                  <TrendingUp size={25} />
                  <b>No history collected yet</b>
                  <span>Connect an account and refresh to start collecting usage.</span>
                </div>
              )}
            </div>

            <div className="chart-foot">
              <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
                {graphMode === 'tokens' ? (
                  topKeyNames.map((name, idx) => {
                    const color = KEY_PALETTE[idx % KEY_PALETTE.length];
                    return (
                      <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                        <span className="legend-dot" style={{ background: color }} />
                        <span style={{ fontSize: 10, color: '#b2b8d0' }}>{name}</span>
                      </span>
                    );
                  })
                ) : (
                  <span><span className="legend-dot" /> Daily spend</span>
                )}
              </div>
              <span><Clock3 size={13} /> Updated via OpenRouter Analytics</span>
            </div>
          </div>

          <div className="card account-card">
            <div className="card-heading">
              <div>
                <h2>Connected accounts</h2>
                <p className="muted">Separate credentials, one view</p>
              </div>
              <button className="text-button" onClick={() => setShowAdd(true)}>Manage <span>→</span></button>
            </div>

            {accounts.length === 0 ? (
              <div className="empty-accounts">
                <div className="empty-icon"><KeyRound size={22} /></div>
                <b>No accounts yet</b>
                <span>Add a management key to start monitoring.</span>
                <button className="primary" onClick={() => setShowAdd(true)}>
                  <Plus size={16} /> Connect first account
                </button>
              </div>
            ) : (
              <div className="account-list">
                {accounts.map((account) => (
                  <div className="account-row" key={account.id}>
                    <div className="account-avatar">{(account.label || '?').slice(0, 1).toUpperCase()}</div>
                    <div className="account-info">
                      <b>{account.label}</b>
                      <span>
                        {account.last_error ? (
                          <em className="bad-status">Needs attention</em>
                        ) : (
                          <em className="good-status"><span className="status-dot" /> Connected</em>
                        )}
                      </span>
                      <div className="account-keys-count">
                        {fmtNum(account.tokens || 0)} tokens · {account.requests || 0} reqs
                      </div>
                    </div>
                    <div className="account-spend">
                      <b>{fmtMoney(account.spend_usd ?? 0)}</b>
                      <small>selected period</small>
                    </div>
                    <button className="danger-icon" onClick={() => removeAccount(account)} title="Disconnect account">
                      <Trash2 size={15} />
                    </button>
                  </div>
                ))}
              </div>
            )}
            <div className="account-footer">
              <ShieldCheck size={14} /> Management keys are never shown again after saving.
            </div>
          </div>
        </section>

        <section className="card table-card">
          <div className="card-heading">
            <div>
              <h2>Daily usage</h2>
              <p className="muted">Historical data saved by RouterScope</p>
            </div>
            <span className="table-count">{series.length} days</span>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>DATE</th><th>SPEND</th><th>REQUESTS</th><th>TOKENS</th><th>DAILY CHANGE</th></tr>
              </thead>
              <tbody>
                {[...series].reverse().slice(0, 10).map((row, index, rows) => {
                  const previous = rows[index + 1];
                  const change = previous && previous.spend_usd > 0
                    ? ((row.spend_usd - previous.spend_usd) / previous.spend_usd) * 100
                    : null;

                  return (
                    <tr key={row.day}>
                      <td>{new Date(`${row.day}T12:00:00`).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</td>
                      <td className="table-money">{fmtMoney(row.spend_usd)}</td>
                      <td>{fmtNum(row.requests)}</td>
                      <td>{fmtNum(row.tokens)}</td>
                      <td>
                        {change === null ? (
                          <span className="muted">—</span>
                        ) : (
                          <span className={change > 0 ? 'change-up' : 'change-down'}>
                            {change > 0 ? '+' : ''}{change.toFixed(1)}%
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {series.length === 0 && (
              <div className="table-empty">No saved usage yet. Connect an account and refresh.</div>
            )}
          </div>
        </section>

        <footer className="footer">
          <span>RouterScope <span className="muted">· Personal usage analytics</span></span>
          <span><ShieldCheck size={13} /> Encrypted credentials · Private by design</span>
        </footer>
      </main>

      {showAdd && (
        <div className="modal-backdrop" role="presentation" onClick={(event) => {
          if (event.target === event.currentTarget && !adding) setShowAdd(false);
        }}>
          <section className="modal-card" role="dialog" aria-modal="true" aria-labelledby="add-account-title">
            <div className="card-heading">
              <div>
                <h2 id="add-account-title">Connect an account</h2>
                <p className="muted">Add an OpenRouter management key.</p>
              </div>
              <button className="text-button" type="button" onClick={() => setShowAdd(false)} disabled={adding}>Close</button>
            </div>
            <form onSubmit={addAccount}>
              <label>
                Account label
                <input value={label} onChange={(event) => setLabel(event.target.value)} required maxLength={100} />
              </label>
              <label>
                OpenRouter management key
                <input type="password" value={key} onChange={(event) => setKey(event.target.value)} required autoComplete="off" />
              </label>
              <div className="modal-actions">
                <button type="button" className="subtle-button" onClick={() => setShowAdd(false)} disabled={adding}>Cancel</button>
                <button type="submit" className="primary" disabled={adding}>
                  {adding ? 'Connecting…' : 'Connect account'}
                </button>
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
