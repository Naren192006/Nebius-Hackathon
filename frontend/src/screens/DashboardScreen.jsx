import { useEffect, useState } from "react";
import { getBenchmark } from "../api/client";
import { Icon } from "../components/Icon";
import { formatMs, formatPct } from "../components/Metrics";

// Display order only; every value rendered comes from results.json.
const ORDER = ["nano", "super", "ultra", "trustrouter"];
const NAMES = { nano: "Nano", super: "Super", ultra: "Ultra", trustrouter: "TrustRouter" };
const COLORS = { nano: "#60a5fa", super: "#a78bfa", ultra: "#fbbf24", trustrouter: "#5eead4" };

// Per-prompt costs here are fractions of a cent, and the shared formatter's
// five decimals would print $0.00001 for both nano and anything within 5% of it.
const formatCost = (value) =>
  value == null || Number.isNaN(value) ? "—" : `$${value.toPrecision(3)}`;

// results.json keys categories as factual_easy; keep the key out of the UI.
const categoryLabel = (key) => key.replace(/_/g, " ");

export default function DashboardScreen() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let alive = true;
    getBenchmark()
      .then((result) => alive && setData(result))
      .catch((err) => alive && setError(err.message || "Could not load results.json"));
    return () => {
      alive = false;
    };
  }, []);

  if (error) {
    return (
      <div className="page">
        <div className="panel empty-benchmark">
          <Icon name="alert" size={20} />
          <h2>Benchmark unavailable</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="page">
        <div className="panel empty-benchmark">
          <span className="skeleton skeleton-line wide" />
          <span className="skeleton skeleton-line medium" />
        </div>
      </div>
    );
  }

  const byStrategy = new Map((data.strategies || []).map((row) => [row.strategy, row]));
  const strategies = ORDER.map((id) => byStrategy.get(id)).filter(Boolean);
  const categories = [...new Set((data.by_category || []).map((row) => row.category))];
  const sizes = [...new Set(strategies.map((row) => row.n))];

  return (
    <div className="page dashboard-page">
      <section className="page-intro">
        <div>
          <p className="eyebrow">Benchmark</p>
          <h1>Strategy comparison</h1>
          <p className="page-lede">
            Every figure below is read straight from results.json — nothing here is computed,
            estimated, or filled in.
          </p>
        </div>
        <div className="benchmark-meta">
          <span className="sample-pill">Measured</span>
          <span>
            n = {sizes.join(" / ")} prompts,{" "}
            {categories.length ? categories.map(categoryLabel).join(", ") : "uncategorised"}
          </span>
          {data.generated_at && <span>generated {data.generated_at}</span>}
        </div>
      </section>

      <section className="panel benchmark-table-wrap">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Full results</p>
            <h3>Strategy comparison</h3>
          </div>
          <span className="benchmark-source">results.json</span>
        </div>
        <div className="table-scroll">
          <table className="benchmark-table">
            <thead>
              <tr>
                <th>Strategy</th>
                <th>n</th>
                <th>Accuracy</th>
                <th>Claims supported</th>
                <th>Avg cost / prompt</th>
                <th>p50 latency</th>
                <th>p95 latency</th>
              </tr>
            </thead>
            <tbody>
              {strategies.map((row) => (
                <tr
                  key={row.strategy}
                  className={row.strategy === "trustrouter" ? "row-highlight" : ""}
                >
                  <td>
                    <span className="strategy-name">
                      <i style={{ background: COLORS[row.strategy] }} />
                      {NAMES[row.strategy] || row.strategy}
                    </span>
                  </td>
                  <td>{row.n}</td>
                  <td>{formatPct(row.accuracy * 100, 1)}</td>
                  <td>{formatPct(row.supported_claims_rate * 100, 1)}</td>
                  <td>{formatCost(row.avg_cost_usd)}</td>
                  <td>{formatMs(row.p50_latency_ms)}</td>
                  <td>{formatMs(row.p95_latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
