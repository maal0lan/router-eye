import { useEffect, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  BarChart,
  Bar,
} from "recharts";

const number = (value) =>
  new Intl.NumberFormat().format(value ?? 0);

export default function UsageAnalytics() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const response = await fetch("/api/analytics/overview", {
          credentials: "include",
        });
        if (!response.ok) throw new Error("Analytics request failed");
        const result = await response.json();
        if (active) {
          setData(result);
          setError("");
        }
      } catch {
        if (active) setError("Couldn't load usage analytics.");
      }
    }

    load();
    const timer = setInterval(load, 60_000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  if (error) return <p role="alert">{error}</p>;
  if (!data) return <p>Loading usage analytics…</p>;

  const accounts = data.accounts ?? [];
  const totalTokens = data.chart.reduce(
    (sum, point) => sum + point.total_tokens,
    0
  );
  const totalRequests = data.chart.reduce(
    (sum, point) => sum + point.requests,
    0
  );

  return (
    <main className="usage-analytics">
      <h2>Usage analytics</h2>
      <p>Last 24 hours · Router Eye tracked requests</p>

      <section className="usage-metrics">
        <article>
          <span>Total tokens</span>
          <strong>{number(totalTokens)}</strong>
        </article>
        <article>
          <span>Requests</span>
          <strong>{number(totalRequests)}</strong>
        </article>
        <article>
          <span>Tracked accounts</span>
          <strong>{number(accounts.length)}</strong>
        </article>
      </section>

      <section>
        <h3>Token volume by hour</h3>
        <div style={{ width: "100%", height: 300 }}>
          <ResponsiveContainer>
            <AreaChart data={data.chart}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="time"
                tickFormatter={(value) =>
                  new Date(value).toLocaleTimeString([], {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                }
              />
              <YAxis tickFormatter={number} />
              <Tooltip
                labelFormatter={(value) =>
                  new Date(value).toLocaleString()
                }
                formatter={(value) => number(value)}
              />
              <Legend />
              <Area
                type="monotone"
                dataKey="input_tokens"
                name="Input tokens"
                stackId="tokens"
                fill="#60a5fa"
                stroke="#3b82f6"
              />
              <Area
                type="monotone"
                dataKey="output_tokens"
                name="Output tokens"
                stackId="tokens"
                fill="#34d399"
                stroke="#059669"
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section>
        <h3>Accounts ranked by token usage</h3>
        {accounts.length === 0 ? (
          <p>No tracked requests yet. New requests will appear here.</p>
        ) : (
          <div style={{ width: "100%", height: 300 }}>
            <ResponsiveContainer>
              <BarChart data={accounts} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis type="number" tickFormatter={number} />
                <YAxis
                  type="category"
                  dataKey="account_name"
                  width={110}
                />
                <Tooltip formatter={(value) => number(value)} />
                <Bar dataKey="total_tokens" name="Total tokens" />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
        <ol>
          {accounts.map((account) => (
            <li key={account.account_id}>
              {account.account_name}: {number(account.total_tokens)} tokens
              {" · "}
              {number(account.requests)} requests
            </li>
          ))}
        </ol>
      </section>
    </main>
  );
}