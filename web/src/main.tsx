import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
} from "lightweight-charts";
import GridLayout, { Layout, WidthProvider } from "react-grid-layout";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  FlaskConical,
  Pin,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings,
  PenLine,
  WalletCards,
  X,
} from "lucide-react";

import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";
import "./styles.css";

const Grid = WidthProvider(GridLayout);

type Candle = {
  open_time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  close_time: number;
};
type MarketData = {
  market: any;
  decision: any;
  decisions?: Record<string, any>;
  edge?: any;
  account: any;
  candles: Candle[];
  strategies?: string[];
  mode: string;
  stream_connected?: boolean;
  stream_last_event_ms?: number;
  monitoring?: boolean;
  patterns?: any;
};
type PinEntry = { symbol: string; market: any; stream_connected: boolean };
type SymbolResult = {
  symbol: string;
  base: string;
  quote: string;
  pinned: boolean;
};

const API = "http://127.0.0.1:8000";
const money = (n: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(n || 0);
const pct = (n: number) => Number(n || 0).toFixed(2) + "%";
const baseOf = (symbol: string) => symbol.replace(/USDT$/, "");

const DEFAULT_LAYOUT: Layout[] = [
  { i: "overview", x: 0, y: 0, w: 12, h: 2, minH: 2 },
  { i: "chart", x: 0, y: 2, w: 8, h: 11, minW: 4, minH: 6 },
  { i: "strategy", x: 8, y: 2, w: 4, h: 6, minW: 3, minH: 4 },
  { i: "edge", x: 8, y: 8, w: 4, h: 4, minW: 3, minH: 3 },
  { i: "portfolio", x: 8, y: 12, w: 4, h: 3, minW: 3, minH: 3 },
  { i: "execution", x: 8, y: 15, w: 4, h: 4, minW: 3, minH: 4 },
  { i: "ledger", x: 0, y: 13, w: 8, h: 6, minW: 4, minH: 4 },
  { i: "board", x: 8, y: 19, w: 4, h: 6, minW: 3, minH: 4 },
  { i: "monitor", x: 8, y: 25, w: 4, h: 4, minW: 3, minH: 3 },
  { i: "arena", x: 0, y: 19, w: 8, h: 7, minW: 3, minH: 3 },
];

function loadLayout(): Layout[] {
  try {
    const raw = localStorage.getItem("pulseshift-layout");
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length) {
        const known = new Set(parsed.map((l: Layout) => l.i));
        const missing = DEFAULT_LAYOUT.filter((l) => !known.has(l.i)).map(
          (l) => ({
            ...l,
            y: Math.max(...parsed.map((p: Layout) => p.y + p.h), 0),
          }),
        );
        return [...parsed, ...missing];
      }
    }
  } catch {
    /* ignore */
  }
  return DEFAULT_LAYOUT;
}

function App() {
  const [pins, setPins] = useState<PinEntry[]>([]);
  const [active, setActive] = useState<string>(
    () => localStorage.getItem("pulseshift-active") || "",
  );
  const [data, setData] = useState<MarketData | null>(null);
  const [error, setError] = useState("");
  const [bt, setBt] = useState<any>(null);
  const [amount, setAmount] = useState(250);
  const [layout, setLayout] = useState<Layout[]>(loadLayout);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolResult[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [focusStrategy, setFocusStrategy] = useState("ema_momentum");
  const [btStrategy, setBtStrategy] = useState("ema_momentum");
  const [styleFilter, setStyleFilter] = useState("all");
  const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({});
  const [monitorEvents, setMonitorEvents] = useState<any[]>([]);
  const [btInterval, setBtInterval] = useState("5m");
  const [btStart, setBtStart] = useState("");
  const [btEnd, setBtEnd] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [providers, setProviders] = useState<any>({});
  const [providerName, setProviderName] = useState("jev");
  const [providerUrl, setProviderUrl] = useState("");
  const [providerKey, setProviderKey] = useState("");
  const [drawMode, setDrawMode] = useState(false);
  const [drawVersion, setDrawVersion] = useState(0);

  const loadConfig = useCallback(async () => {
    try {
      const r = await fetch(API + "/api/config");
      if (r.ok) setProviders((await r.json()).providers);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    loadConfig();
  }, [loadConfig]);

  const saveProvider = async () => {
    const r = await fetch(`${API}/api/config/${providerName}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: providerUrl || null,
        api_key: providerKey || null,
      }),
    });
    if (r.ok) {
      setProviderKey("");
      loadConfig();
      fetch(API + "/api/strategies/reload", { method: "POST" });
    }
  };

  const refreshStrategies = useCallback(async () => {
    try {
      const r = await fetch(API + "/api/strategies");
      if (r.ok) setStrategies((await r.json()).strategies);
    } catch {
      /* ignore */
    }
  }, []);
  useEffect(() => {
    refreshStrategies();
  }, [refreshStrategies]);

  const configureStrategy = async (
    id: string,
    params: Record<string, number>,
  ) => {
    const r = await fetch(`${API}/api/strategies/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ params }),
    });
    if (r.ok) {
      const { params: updated } = await r.json();
      setStrategies((list) =>
        list.map((s) => (s.id === id ? { ...s, params: updated } : s)),
      );
      refresh(active);
    }
  };

  const refreshPins = useCallback(async () => {
    try {
      const r = await fetch(API + "/api/watchlist");
      if (r.ok) setPins((await r.json()).symbols);
    } catch {
      /* ignore */
    }
  }, []);

  const activeRef = useRef(active);
  activeRef.current = active;

  const refresh = useCallback(async (symbol: string) => {
    if (!symbol) return;
    try {
      const r = await fetch(API + "/api/market?symbol=" + symbol);
      if (!r.ok) throw new Error(await r.text());
      const payload = await r.json();
      if (activeRef.current !== symbol) return;
      setData(payload);
      setError("");
    } catch (e: any) {
      if (activeRef.current === symbol) setError(e.message);
    }
  }, []);

  // Bootstrap watchlist, then pick an active symbol.
  useEffect(() => {
    refreshPins();
  }, [refreshPins]);
  useEffect(() => {
    if (!pins.length) return;
    if (!active || !pins.some((p) => p.symbol === active)) {
      setActive(pins[0].symbol);
    }
  }, [pins, active]);
  useEffect(() => {
    if (active) localStorage.setItem("pulseshift-active", active);
  }, [active]);

  // Poll pin strip prices.
  useEffect(() => {
    const id = setInterval(refreshPins, 4000);
    return () => clearInterval(id);
  }, [refreshPins]);

  // Load + stream the active symbol.
  useEffect(() => {
    if (!active) return;
    setData(null);
    setBt(null);
    refresh(active);
    const ws = new WebSocket(`ws://127.0.0.1:8000/ws/market?symbol=${active}`);
    ws.onmessage = (event) => {
      const packet = JSON.parse(event.data);
      if (packet.type !== "market") return;
      setData((current) => {
        if (!current) return current;
        const next = {
          ...current,
          market: packet.market,
          decision: packet.decision || current.decision,
          decisions: packet.decisions || current.decisions,
          edge: packet.edge || current.edge,
          account: packet.account || current.account,
          stream_connected: packet.stream_connected,
        };
        if (packet.candle) {
          const candles = [...current.candles];
          const i = candles.length - 1;
          if (i >= 0 && candles[i].open_time === packet.candle.open_time)
            candles[i] = packet.candle;
          else candles.push(packet.candle);
          next.candles = candles.slice(-120);
        }
        return next;
      });
    };
    ws.onerror = () => setError("Live market stream disconnected");
    ws.onopen = () => setError("");
    return () => ws.close();
  }, [active, refresh]);

  // Symbol search.
  useEffect(() => {
    if (!pickerOpen) return;
    const id = setTimeout(async () => {
      try {
        const r = await fetch(
          API + "/api/symbols?query=" + encodeURIComponent(query),
        );
        if (r.ok) setResults((await r.json()).symbols);
      } catch {
        /* ignore */
      }
    }, 200);
    return () => clearTimeout(id);
  }, [query, pickerOpen]);

  const pin = async (symbol: string) => {
    const r = await fetch(API + "/api/watchlist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    });
    if (!r.ok) {
      setError((await r.json()).detail || `Could not pin ${symbol}`);
      return;
    }
    const { symbols } = await r.json();
    setPins(
      (symbols as string[]).map((s) => ({
        symbol: s,
        market:
          s === symbol
            ? null
            : (pins.find((p) => p.symbol === s)?.market ?? null),
        stream_connected: false,
      })),
    );
    setQuery("");
    setPickerOpen(false);
    refreshPins();
    setActive(symbol);
  };
  const unpin = async (symbol: string) => {
    const r = await fetch(API + "/api/watchlist/" + symbol, {
      method: "DELETE",
    });
    if (r.ok) {
      const { symbols } = await r.json();
      setPins((current) => current.filter((p) => symbols.includes(p.symbol)));
    }
    refreshPins();
  };

  const order = async (side: string) => {
    const r = await fetch(API + "/api/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ side, amount_usd: amount, symbol: active }),
    });
    if (!r.ok) {
      setError((await r.json()).detail || "Order failed");
      return;
    }
    setError("");
    refresh(active);
  };
  const toggleMonitor = async () => {
    const enabled = !(data?.monitoring === true);
    const r = await fetch(`${API}/api/monitor/${active}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    if (r.ok) {
      setData((current) =>
        current ? { ...current, monitoring: enabled } : current,
      );
    }
  };

  // Poll monitor events while monitoring is on.
  useEffect(() => {
    if (!active || data?.monitoring !== true) {
      setMonitorEvents([]);
      return;
    }
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/monitor/${active}?limit=8`);
        if (r.ok) setMonitorEvents((await r.json()).events.reverse());
      } catch {
        /* ignore */
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [active, data?.monitoring]);

  const runBt = async () => {
    let url =
      API +
      `/api/backtest?symbol=${active}&interval=${btInterval}&limit=500&strategy=${btStrategy}`;
    if (btStart) url += `&start_ms=${new Date(btStart).getTime()}`;
    if (btEnd) url += `&end_ms=${new Date(btEnd).getTime()}`;
    const r = await fetch(url);
    setBt(await r.json());
  };

  return (
    <div className="appShell">
      <header className="topbar">
        <div className="brand">
          <div className="brandMark">P</div>
          <div>
            <strong>PulseShift</strong>
            <span>Local quant research</span>
          </div>
        </div>
        <nav className="symbolTabs">
          {pins.map((p) => (
            <button
              key={p.symbol}
              className={p.symbol === active ? "active" : ""}
              onClick={() => setActive(p.symbol)}
              title={p.symbol}
            >
              {baseOf(p.symbol)}
              <span className="tabPrice">
                {p.market ? money(p.market.price) : "—"}
              </span>
              <span
                className="tabClose"
                title={`Unpin ${p.symbol}`}
                onClick={(e) => {
                  e.stopPropagation();
                  unpin(p.symbol);
                }}
              >
                <X size={11} />
              </span>
            </button>
          ))}
          <button
            className="addTab"
            onClick={() => setPickerOpen((v) => !v)}
            title="Pin a symbol"
          >
            <Plus size={14} />
          </button>
        </nav>
        <div className="marketStatus">
          <span className="statusDot" /> Paper only <span>·</span>{" "}
          {data?.stream_connected === false
            ? "Reconnecting"
            : "Live Binance stream"}
          <button
            className="gearButton"
            title="Provider settings"
            onClick={() => setSettingsOpen((v) => !v)}
          >
            <Settings size={15} />
          </button>
        </div>
      </header>

      {settingsOpen && (
        <div className="settings panel">
          <div className="panelHeader compact">
            <div>
              <span className="kicker">BYO key</span>
              <h2>AI providers</h2>
            </div>
          </div>
          <div className="settingsBody">
            <label>
              Provider
              <input
                value={providerName}
                onChange={(e) => setProviderName(e.target.value)}
                placeholder="jev"
              />
            </label>
            <label>
              Endpoint URL
              <input
                value={providerUrl}
                onChange={(e) => setProviderUrl(e.target.value)}
                placeholder="https://…/decide or http://127.0.0.1:8790/decide"
              />
            </label>
            <label>
              API key
              <input
                type="password"
                value={providerKey}
                onChange={(e) => setProviderKey(e.target.value)}
                placeholder={
                  providers[providerName]?.api_key_set
                    ? "saved (leave blank to keep)"
                    : "paste key"
                }
              />
            </label>
            <button className="runButton" onClick={saveProvider}>
              Save
            </button>
          </div>
          <div className="providerList">
            {Object.entries(providers).map(([name, p]: [string, any]) => (
              <div key={name} className="providerRow">
                <strong>{name}</strong>
                <span>{p.url || "no endpoint"}</span>
                <em>{p.api_key_set ? "key saved" : "no key"}</em>
              </div>
            ))}
            {!Object.keys(providers).length && (
              <em>No providers configured yet.</em>
            )}
          </div>
          <small className="edgeNote">
            Stored locally in data/config.json (gitignored). Strategies named
            after a provider (e.g. jev, laya) use it automatically.
          </small>
        </div>
      )}

      {pickerOpen && (
        <div className="picker panel">
          <div className="pickerSearch">
            <Search size={14} />
            <input
              autoFocus
              placeholder="Search Binance spot symbols (e.g. ETH, SOL)…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="pickerResults">
            {results.map((s) => (
              <button key={s.symbol} onClick={() => pin(s.symbol)}>
                <strong>{s.base}</strong>
                <span>{s.symbol}</span>
                {s.pinned ? <Pin size={13} /> : <Plus size={13} />}
              </button>
            ))}
            {!results.length && <em>Searching Binance symbols…</em>}
          </div>
        </div>
      )}

      <main className="workspace">
        {!data ? (
          <div className="loading">
            <span>PulseShift</span>
            <small>Connecting to the local quant engine…</small>
            {error && <em>{error}</em>}
          </div>
        ) : (
          <Grid
            className="layout"
            layout={layout}
            cols={12}
            rowHeight={44}
            margin={[14, 14]}
            draggableHandle=".panelHeader"
            onLayoutChange={(l: Layout[]) => {
              setLayout(l);
              localStorage.setItem("pulseshift-layout", JSON.stringify(l));
            }}
          >
            <section key="overview" className="overview panel">
              <div className="panelHeader dragHint" />
              <div className="assetBlock">
                <span>{baseOf(active)} / USDT</span>
                <strong>{money(data.market.price)}</strong>
                <small
                  className={data.market.change_24h_pct >= 0 ? "up" : "down"}
                >
                  {data.market.change_24h_pct >= 0 ? "+" : ""}
                  {pct(data.market.change_24h_pct)} today
                </small>
              </div>
              <Stat label="Bid" value={money(data.market.bid)} />
              <Stat label="Ask" value={money(data.market.ask)} />
              <Stat
                label="Spread"
                value={data.market.spread_bps.toFixed(2) + " bps"}
              />
              <Stat
                label="24h quote volume"
                value={money(data.market.volume_24h)}
              />
              <button
                className="iconButton"
                onClick={() => refresh(active)}
                title="Refresh"
              >
                <RefreshCw size={16} />
              </button>
            </section>

            <article key="chart" className="chartPanel panel">
              <div className="panelHeader">
                <div>
                  <span className="kicker">Market</span>
                  <h2>{active}</h2>
                </div>
                <div className="intervals">
                  <button
                    className={drawMode ? "active" : ""}
                    onClick={() => setDrawMode((v) => !v)}
                    title="Draw a trendline (click two points)"
                  >
                    <PenLine size={13} /> Draw
                  </button>
                  <button
                    onClick={() => {
                      localStorage.removeItem(`pulseshift-lines-${active}`);
                      setDrawVersion((v) => v + 1);
                    }}
                  >
                    Clear
                  </button>
                </div>
              </div>
              <Chart
                key={`${active}-${drawVersion}`}
                symbol={active}
                candles={data.candles}
                overlays={data.patterns}
                drawMode={drawMode}
                onDrawn={() => setDrawMode(false)}
              />
              <div className="chartFooter">
                <span>{data.candles.length} candles</span>
                <span>Streaming live</span>
              </div>
            </article>

            <section key="strategy" className="panel intelligence">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Model</span>
                  <h2>Strategy intelligence</h2>
                </div>
                <BrainCircuit size={18} />
              </div>
              {(() => {
                const d = data.decisions?.[focusStrategy] ?? data.decision;
                const meta = strategies.find((s) => s.id === focusStrategy);
                if (!d) return <em>Warming up…</em>;
                return (
                  <>
                    <div className="decision">
                      <div>
                        <span>Current action</span>
                        <strong
                          className={
                            d.action === "LONG"
                              ? "up"
                              : d.action === "SHORT"
                                ? "down"
                                : ""
                          }
                        >
                          {d.action}
                        </strong>
                      </div>
                      <small>{meta?.name ?? focusStrategy}</small>
                    </div>
                    <Gauge label="Signal confidence" value={d.confidence} />
                    <Gauge label="Regime fit" value={d.regime_confidence} />
                    <Gauge
                      label="Execution quality"
                      value={d.execution_confidence}
                    />
                    <div className="regimeRow">
                      <span>Market regime</span>
                      <strong>{d.regime}</strong>
                    </div>
                    <ul className="reasons">
                      {(d.reasons || []).map((r: string) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                    {meta?.params?.length > 0 && (
                      <div className="paramEditor">
                        <span className="kicker">Parameters</span>
                        {meta.params.map((p: any) => {
                          const key = `${focusStrategy}:${p.name}`;
                          return (
                            <label key={p.name} className="paramRow">
                              <span>{p.label || p.name}</span>
                              <input
                                type="number"
                                step={p.step || 1}
                                min={p.min}
                                max={p.max}
                                value={paramDrafts[key] ?? p.value}
                                onChange={(e) =>
                                  setParamDrafts((d) => ({
                                    ...d,
                                    [key]: e.target.value,
                                  }))
                                }
                                onBlur={(e) => {
                                  const v = Number(e.target.value);
                                  if (!Number.isNaN(v) && v !== p.value)
                                    configureStrategy(focusStrategy, {
                                      [p.name]: v,
                                    });
                                  setParamDrafts((d) => {
                                    const next = { ...d };
                                    delete next[key];
                                    return next;
                                  });
                                }}
                              />
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </>
                );
              })()}
            </section>

            <section key="edge" className="panel edgePanel">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Live edge</span>
                  <h2>Entry / exit</h2>
                </div>
              </div>
              {data.edge ? (
                <>
                  <div
                    className={`verdict ${data.edge.verdict.startsWith("ENTER") ? "up" : data.edge.verdict === "NO_TRADE" ? "" : "warn"}`}
                  >
                    {data.edge.verdict.replace(/_/g, " ")}
                  </div>
                  <div className="twoCol">
                    <Stat
                      label="Win probability"
                      value={pct(data.edge.win_probability * 100)}
                    />
                    <Stat
                      label="Net edge"
                      value={`${data.edge.net_edge_pct >= 0 ? "+" : ""}${data.edge.net_edge_pct.toFixed(3)}%`}
                    />
                    <Stat
                      label="Breakeven move"
                      value={`${data.edge.breakeven_move_pct.toFixed(3)}%`}
                    />
                    <Stat
                      label="Expected move"
                      value={`${data.edge.expected_move_pct.toFixed(3)}%`}
                    />
                    <Stat
                      label="Entry (bid)"
                      value={money(data.edge.suggested_entry)}
                    />
                    <Stat
                      label="Target"
                      value={
                        data.edge.suggested_target
                          ? money(data.edge.suggested_target)
                          : "—"
                      }
                    />
                    <Stat
                      label="Stop"
                      value={
                        data.edge.suggested_stop
                          ? money(data.edge.suggested_stop)
                          : "—"
                      }
                    />
                  </div>
                  <small className="edgeNote">{data.edge.note}</small>
                </>
              ) : (
                <em>Warming up…</em>
              )}
            </section>

            <section key="board" className="panel strategyBoard">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Compare</span>
                  <h2>Strategy board</h2>
                </div>
              </div>
              <div className="styleChips">
                {["all", "scalping", "day", "swing"].map((s) => (
                  <button
                    key={s}
                    className={styleFilter === s ? "active" : ""}
                    onClick={() => setStyleFilter(s)}
                  >
                    {s === "all" ? "All" : s === "day" ? "Day trade" : s}
                  </button>
                ))}
              </div>
              <div className="boardRows">
                {strategies
                  .filter(
                    (s) =>
                      styleFilter === "all" ||
                      (s.styles || []).includes(styleFilter),
                  )
                  .map((s) => {
                    const d = data.decisions?.[s.id];
                    return (
                      <button
                        key={s.id}
                        className={`boardRow ${focusStrategy === s.id ? "active" : ""}`}
                        onClick={() => setFocusStrategy(s.id)}
                        title={s.description}
                      >
                        <span className="boardName">
                          {s.name}
                          {!s.builtin && <em>plugin</em>}
                        </span>
                        <span
                          className={`boardAction ${d?.action === "LONG" ? "up" : d?.action === "SHORT" ? "down" : ""}`}
                        >
                          {d?.action ?? "—"}
                        </span>
                        <span className="boardConf">
                          {d ? Math.round(d.confidence * 100) + "%" : "—"}
                        </span>
                      </button>
                    );
                  })}
              </div>
            </section>

            <section key="monitor" className="panel monitorPanel">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Watch</span>
                  <h2>Signal monitor</h2>
                </div>
                <button
                  className={`monitorToggle ${data.monitoring ? "on" : ""}`}
                  onClick={toggleMonitor}
                >
                  {data.monitoring ? "Monitoring" : "Start monitoring"}
                </button>
              </div>
              {data.monitoring ? (
                monitorEvents.length ? (
                  <ul className="monitorList">
                    {monitorEvents.map((e, i) => (
                      <li key={e.ts + i}>
                        <span>{new Date(e.ts).toLocaleTimeString()}</span>
                        <strong
                          className={
                            e.edge?.verdict?.startsWith("ENTER")
                              ? "up"
                              : e.edge?.verdict === "NO_TRADE"
                                ? "down"
                                : ""
                          }
                        >
                          {(e.edge?.verdict || "—").replace(/_/g, " ")}
                        </strong>
                        <em>
                          {Object.entries(e.decisions || {})
                            .map(([sid, a]) => `${sid}:${a}`)
                            .join(" · ")}
                        </em>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <em>Recording signals to data/monitor/…</em>
                )
              ) : (
                <em>Off — enable to log live entry/exit calls.</em>
              )}
            </section>

            <section key="portfolio" className="panel portfolio">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Paper account</span>
                  <h2>Portfolio</h2>
                </div>
                <WalletCards size={18} />
              </div>
              <div className="equityRow">
                <strong>{money(data.account.equity)}</strong>
                <span
                  className={data.account.total_return_pct >= 0 ? "up" : "down"}
                >
                  {pct(data.account.total_return_pct)}
                </span>
              </div>
              <div className="twoCol">
                <Stat label="Cash" value={money(data.account.cash)} />
                <Stat
                  label={baseOf(active) + " position"}
                  value={
                    data.account.position
                      ? Number(data.account.position.qty).toFixed(6)
                      : "0"
                  }
                />
                <Stat
                  label="Entry"
                  value={
                    data.account.position?.entry_price
                      ? money(data.account.position.entry_price)
                      : "—"
                  }
                />
                <Stat
                  label="Unrealized P&L"
                  value={money(data.account.unrealized_pnl)}
                />
              </div>
              {Object.keys(data.account.positions || {}).length > 1 && (
                <div className="positionChips">
                  {Object.entries(data.account.positions).map(
                    ([sym, p]: [string, any]) => (
                      <span key={sym} className="chip">
                        {baseOf(sym)} {money(p.market_value)}
                      </span>
                    ),
                  )}
                </div>
              )}
            </section>

            <section key="execution" className="panel execution">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">Simulator</span>
                  <h2>Paper execution</h2>
                </div>
                <BarChart3 size={18} />
              </div>
              <label>Notional (USDT)</label>
              <input
                type="number"
                value={amount}
                min={10}
                step={10}
                onChange={(e) => setAmount(Number(e.target.value))}
              />
              <div className="tradeButtons">
                <button
                  className="tradeButton buy"
                  onClick={() => order("BUY")}
                >
                  <span>Buy {baseOf(active)}</span>
                  <small>Simulated ask fill</small>
                </button>
                <button
                  className="tradeButton sell"
                  onClick={() => order("SELL")}
                >
                  <span>Sell {baseOf(active)}</span>
                  <small>Simulated bid fill</small>
                </button>
              </div>
              <button
                className="secondary actionButton"
                onClick={() => order("CLOSE")}
              >
                Close full position
              </button>
              <button
                className="linkButton"
                onClick={async () => {
                  await fetch(API + "/api/reset", { method: "POST" });
                  refresh(active);
                }}
              >
                <RotateCcw size={14} /> Reset paper account
              </button>
              {error && <div className="inlineError">{error}</div>}
            </section>

            <section key="ledger" className="panel ledger">
              <div className="panelHeader">
                <div>
                  <span className="kicker">Activity</span>
                  <h2>Execution ledger</h2>
                </div>
                <Activity size={18} />
              </div>
              <div className="tableWrap">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Price</th>
                      <th>Fee</th>
                      <th>P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.account.trades.length ? (
                      data.account.trades.map((t: any) => (
                        <tr key={t.ts + t.symbol}>
                          <td>{new Date(t.ts).toLocaleTimeString()}</td>
                          <td>{baseOf(t.symbol)}</td>
                          <td className={t.side === "BUY" ? "up" : "down"}>
                            {t.side}
                          </td>
                          <td>{Number(t.qty).toFixed(6)}</td>
                          <td>{money(t.price)}</td>
                          <td>{money(t.fee)}</td>
                          <td>{money(t.pnl)}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan={7} className="empty">
                          No paper executions yet. Use the simulator to create
                          the first trade.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>

            <section key="arena" className="panel arena">
              <div className="panelHeader">
                <div>
                  <span className="kicker">Research</span>
                  <h2>Strategy arena</h2>
                </div>
                <div className="arenaControls">
                  <select
                    value={btStrategy}
                    onChange={(e) => setBtStrategy(e.target.value)}
                  >
                    {strategies.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </select>
                  <select
                    value={btInterval}
                    onChange={(e) => setBtInterval(e.target.value)}
                  >
                    {["1m", "5m", "15m", "1h", "4h", "1d"].map((i) => (
                      <option key={i} value={i}>
                        {i}
                      </option>
                    ))}
                  </select>
                  <button className="runButton" onClick={runBt}>
                    <Play size={14} /> Run
                  </button>
                </div>
              </div>
              <div className="arenaRange">
                <label>
                  From
                  <input
                    type="datetime-local"
                    value={btStart}
                    onChange={(e) => setBtStart(e.target.value)}
                  />
                </label>
                <label>
                  To
                  <input
                    type="datetime-local"
                    value={btEnd}
                    onChange={(e) => setBtEnd(e.target.value)}
                  />
                </label>
              </div>
              <p>
                Historical backtest on {active} — pick any past window (leave
                dates blank for the latest 500 candles).
                {bt?.window &&
                  ` Window: ${new Date(bt.window.start_ms).toLocaleDateString()} – ${new Date(bt.window.end_ms).toLocaleDateString()}, ${bt.window.candles} candles.`}
              </p>
              {bt ? (
                <>
                  {bt.candles?.length > 0 && (
                    <Chart
                      key={`bt-${bt.strategy_id}-${bt.window?.start_ms}`}
                      symbol={active}
                      candles={bt.candles}
                      overlays={bt.patterns}
                      drawMode={false}
                    />
                  )}
                  <div className="arenaStats">
                    <Stat label="Return" value={pct(bt.return_pct)} />
                    <Stat label="Round trips" value={String(bt.round_trips)} />
                    <Stat label="Win rate" value={pct(bt.win_rate_pct)} />
                    <Stat
                      label="Max drawdown"
                      value={pct(bt.max_drawdown_pct)}
                    />
                    <Stat
                      label="Profit factor"
                      value={bt.profit_factor ?? "—"}
                    />
                  </div>
                </>
              ) : (
                <div className="arenaEmpty">
                  <FlaskConical size={20} />
                  <span>No experiment results yet</span>
                </div>
              )}
            </section>
          </Grid>
        )}
      </main>
    </div>
  );
}

type TrendLine = { t1: number; p1: number; t2: number; p2: number };
const _tsOf = (c: Candle) => Math.floor(c.open_time / 1000);

function Chart({
  symbol,
  candles,
  overlays,
  drawMode,
  onDrawn,
}: {
  symbol: string;
  candles: Candle[];
  overlays?: any;
  drawMode: boolean;
  onDrawn?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const pendingRef = useRef<TrendLine | null>(null);
  const drawModeRef = useRef(drawMode);
  drawModeRef.current = drawMode;

  const storageKey = `pulseshift-lines-${symbol}`;
  const loadLines = (): TrendLine[] => {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || "[]");
    } catch {
      return [];
    }
  };

  useEffect(() => {
    if (!ref.current || !candles.length) return;
    const chart = createChart(ref.current, {
      layout: {
        background: { color: "#ffffff" },
        textColor: "#6b7280",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      },
      grid: {
        vertLines: { color: "#f0f2f5" },
        horzLines: { color: "#f0f2f5" },
      },
      rightPriceScale: { borderColor: "#e5e7eb" },
      timeScale: {
        borderColor: "#e5e7eb",
        timeVisible: true,
        secondsVisible: false,
      },
      autoSize: true,
    });
    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#168b6a",
      downColor: "#c44f5e",
      wickUpColor: "#168b6a",
      wickDownColor: "#c44f5e",
      borderVisible: false,
    });
    series.setData(
      candles.map((c) => ({
        time: (c.open_time / 1000) as any,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );
    // Saved manual trendlines.
    for (const l of loadLines()) {
      const line = chart.addSeries(LineSeries, {
        color: "#7c3aed",
        lineWidth: 2,
      });
      line.setData([
        { time: l.t1 as any, value: l.p1 },
        { time: l.t2 as any, value: l.p2 },
      ]);
    }

    // Pattern overlays: polylines, S/R levels, entry/stop/target markers.
    const markers: any[] = [];
    const addOverlayLine = (
      pts: [number, number][],
      color: string,
      style = 0,
    ) => {
      if (!pts?.length) return;
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth: 1,
        lineStyle: style,
        lastValueVisible: false,
        priceLineVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(pts.map(([t, p]) => ({ time: t as any, value: p })));
    };
    for (const lv of overlays?.levels || []) {
      addOverlayLine(
        [
          [lv.t0, lv.price],
          [lv.t1, lv.price],
        ],
        lv.kind === "support" ? "#168b6a" : "#c44f5e",
        2,
      );
    }
    for (const pat of overlays?.patterns || []) {
      const color =
        pat.bias === "bullish"
          ? "#168b6a"
          : pat.bias === "bearish"
            ? "#c44f5e"
            : "#64748b";
      const pts = pat.points || [];
      for (let i = 0; i + 1 < pts.length; i += 2)
        addOverlayLine([pts[i], pts[i + 1]], color);
      const last = candles[candles.length - 1];
      const t = Math.floor(last.open_time / 1000);
      markers.push(
        {
          time: t as any,
          position: "aboveBar",
          color,
          shape: "circle",
          text: `${pat.name} ${Math.round(pat.confidence * 100)}%`,
        },
        {
          time: t as any,
          position: "aboveBar",
          color: "#2563eb",
          shape: "arrowUp",
          text: `ENTRY ${pat.entry}`,
        },
        {
          time: t as any,
          position: "belowBar",
          color: "#c44f5e",
          shape: "arrowDown",
          text: `STOP ${pat.stop}`,
        },
        {
          time: t as any,
          position: "aboveBar",
          color: "#168b6a",
          shape: "arrowUp",
          text: `TARGET ${pat.target}`,
        },
      );
      addOverlayLine(
        [
          [_tsOf(candles[0]), pat.entry],
          [_tsOf(last), pat.entry],
        ],
        "#2563eb",
        1,
      );
    }
    if (markers.length) createSeriesMarkers(series, markers);

    // Two-click trendline drawing.
    chart.subscribeClick((param: any) => {
      if (!drawModeRef.current || !param.point) return;
      const time = param.time as number;
      const price = series.coordinateToPrice(param.point.y);
      if (time == null || price == null) return;
      if (!pendingRef.current) {
        pendingRef.current = { t1: time, p1: price, t2: time, p2: price };
        return;
      }
      const first = pendingRef.current;
      pendingRef.current = null;
      const line = { ...first, t2: time, p2: price };
      const all = [...loadLines(), line];
      localStorage.setItem(storageKey, JSON.stringify(all));
      const ls = chart.addSeries(LineSeries, {
        color: "#7c3aed",
        lineWidth: 2,
      });
      ls.setData([
        { time: line.t1 as any, value: line.p1 },
        { time: line.t2 as any, value: line.p2 },
      ]);
      onDrawn?.();
    });

    chart.timeScale().fitContent();
    chartRef.current = chart;
    seriesRef.current = series;
    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = null;
    };
  }, [candles.length > 0]);

  useEffect(() => {
    const c = candles[candles.length - 1];
    if (!c || !seriesRef.current) return;
    seriesRef.current.update({
      time: (c.open_time / 1000) as any,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    });
  }, [candles]);

  return <div ref={ref} className="chart" />;
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
function Gauge({ label, value }: { label: string; value: number }) {
  return (
    <div className="gauge">
      <div>
        <span>{label}</span>
        <strong>{Math.round(value * 100)}%</strong>
      </div>
      <div className="track">
        <i style={{ width: Math.max(0, Math.min(100, value * 100)) + "%" }} />
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
