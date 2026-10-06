import React, { useCallback, useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  createChart,
  createSeriesMarkers,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
} from "lightweight-charts";
import GridLayout, { Layout, WidthProvider } from "react-grid-layout";
import {
  Activity,
  BarChart3,
  BrainCircuit,
  FlaskConical,
  LayoutGrid,
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
  edges?: Record<string, any>;
  account: any;
  candles: Candle[];
  strategies?: string[];
  mode: string;
  stream_connected?: boolean;
  stream_last_event_ms?: number;
  monitoring?: boolean;
  patterns?: any;
};
type SignalMarker = {
  time: number;
  side: "BUY" | "SELL" | "SETUP_UP" | "SETUP_DOWN";
  price: number;
  text: string;
  open?: boolean;
};
type SignalPlan = {
  side: "LONG" | "SHORT";
  active: boolean;
  entry: number;
  stop: number;
  target: number;
  horizon_candles?: number;
  expected_move_bps?: number;
  cost_bps?: number;
  reliability_ic?: number;
  timeframe_model?: string;
};
type ChartSignals = {
  markers?: SignalMarker[];
  plan?: SignalPlan | null;
  decision?: any;
  summary?: any;
};
const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

type PinEntry = { symbol: string; market: any; stream_connected: boolean };
type SymbolResult = {
  symbol: string;
  base: string;
  quote: string;
  pinned: boolean;
};

// API base URL: set VITE_API_URL in the repo-root .env to override.
const API = (
  (import.meta.env.VITE_API_URL as string | undefined) ||
  "http://127.0.0.1:8000"
).replace(/\/$/, "");
const WS_API = API.replace(/^http/, "ws");
const money = (n: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(n || 0);
const pct = (n: number) => Number(n || 0).toFixed(2) + "%";
const baseOf = (symbol: string) => symbol.replace(/USDT$/, "");

const INDICATOR_LABELS: Record<string, string> = {
  vol: "Volume histogram",
  ema: "EMA 9 / 50",
  bb: "Bollinger Bands (20, 2σ)",
  rsi: "RSI 14",
  macd: "MACD (12, 26, 9)",
};

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
  { i: "trend", x: 0, y: 26, w: 8, h: 8, minW: 4, minH: 4 },
  { i: "forward", x: 8, y: 29, w: 4, h: 8, minW: 3, minH: 5 },
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
  const isMulti = active === "MULTI";
  const [data, setData] = useState<MarketData | null>(null);
  const [error, setError] = useState("");
  const [bt, setBt] = useState<any>(null);
  const [evolution, setEvolution] = useState<any>(null);
  const [evolving, setEvolving] = useState(false);
  const [evoError, setEvoError] = useState("");
  const [amount, setAmount] = useState(250);
  const [layout, setLayout] = useState<Layout[]>(loadLayout);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SymbolResult[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [searchState, setSearchState] = useState<"loading" | "done" | "error">(
    "loading",
  );
  const [searchError, setSearchError] = useState("");
  const [searchNonce, setSearchNonce] = useState(0);
  const [strategies, setStrategies] = useState<any[]>([]);
  const [focusStrategy, setFocusStrategy] = useState<string>(() => {
    try {
      return localStorage.getItem("pulseshift-focus") || "ai_regime_fusion";
    } catch {
      return "ai_regime_fusion";
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("pulseshift-focus", focusStrategy);
    } catch {
      /* ignore */
    }
  }, [focusStrategy]);
  const [apiLink, setApiLink] = useState<"connecting" | "live" | "lost">(
    "connecting",
  );
  const [btStrategy, setBtStrategy] = useState("ema_momentum");
  const [styleFilter, setStyleFilter] = useState("all");
  const [paramDrafts, setParamDrafts] = useState<Record<string, string>>({});
  const [monitorEvents, setMonitorEvents] = useState<any[]>([]);
  const [btInterval, setBtInterval] = useState("5m");
  const [btStart, setBtStart] = useState("");
  const [btEnd, setBtEnd] = useState("");
  const [ind, setInd] = useState<string[]>(["vol", "ema"]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [providers, setProviders] = useState<any>({});
  const [providerName, setProviderName] = useState("jev");
  const [providerUrl, setProviderUrl] = useState("");
  const [providerKey, setProviderKey] = useState("");
  const [drawMode, setDrawMode] = useState(false);
  const [drawVersion, setDrawVersion] = useState(0);
  const [tf, setTf] = useState<string>(() => {
    try {
      return localStorage.getItem("pulseshift-tf") || "1m";
    } catch {
      return "1m";
    }
  });
  const [sig, setSig] = useState<any>(null);
  const [sigStrategy, setSigStrategy] = useState<string>(() => {
    try {
      return (
        localStorage.getItem("pulseshift-sig-strategy") || "ai_regime_fusion"
      );
    } catch {
      return "ai_regime_fusion";
    }
  });
  const [trendPf, setTrendPf] = useState<any>(null);
  const [fwd, setFwd] = useState<any>(null);

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
    if (
      !active ||
      (active !== "MULTI" && !pins.some((p) => p.symbol === active))
    ) {
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
    if (!active || active === "MULTI") return;
    setData(null);
    setBt(null);
    refresh(active);
    // Live stream with automatic reconnect: a restarted API used to leave
    // the dashboard silently frozen on its last packet.
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let disposed = false;
    let attempt = 0;
    const connect = () => {
      if (disposed) return;
      ws = new WebSocket(`${WS_API}/ws/market?symbol=${active}`);
      ws.onopen = () => {
        attempt = 0;
        setApiLink("live");
        setError("");
        refresh(active); // catch up on anything missed while disconnected
      };
      ws.onclose = () => {
        if (disposed) return;
        setApiLink("lost");
        const delay = Math.min(1000 * 2 ** attempt, 15000);
        attempt += 1;
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = handleMessage;
    };
    const handleMessage = (event: MessageEvent) => {
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
          patterns: packet.patterns || current.patterns,
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
    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [active, refresh]);

  // Symbol search. Stale responses are ignored, and failures surface as an
  // error with a retry instead of an endless "Searching…".
  useEffect(() => {
    if (!pickerOpen) return;
    let cancelled = false;
    setSearchState("loading");
    const id = setTimeout(async () => {
      try {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), 35000);
        const r = await fetch(
          API + "/api/symbols?query=" + encodeURIComponent(query),
          { signal: ctrl.signal },
        );
        clearTimeout(timer);
        if (cancelled) return;
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          setSearchError(body.detail || `Search failed (${r.status})`);
          setSearchState("error");
          return;
        }
        setResults((await r.json()).symbols);
        setSearchState("done");
      } catch (e: any) {
        if (cancelled) return;
        setSearchError(
          e?.name === "AbortError"
            ? "Binance symbol list timed out"
            : "Cannot reach the local API",
        );
        setSearchState("error");
      }
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(id);
    };
  }, [query, pickerOpen, searchNonce]);

  // While a request is in flight, filter what we already have so the list
  // always matches the typed text (previously the old list lingered).
  const needle = query.trim().toUpperCase();
  const shownResults =
    searchState === "loading" && needle
      ? results.filter(
          (r) => r.symbol.includes(needle) || r.base.includes(needle),
        )
      : results;

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

  // Strategy signals for the main chart: candles for the chosen timeframe
  // plus BUY/EXIT markers, model setups and the entry/stop/target plan.
  useEffect(() => {
    try {
      localStorage.setItem("pulseshift-tf", tf);
      localStorage.setItem("pulseshift-sig-strategy", sigStrategy);
    } catch {
      /* ignore */
    }
    if (!active || active === "MULTI") return;
    let cancelled = false;
    setSig(null);
    const load = async () => {
      try {
        const r = await fetch(
          `${API}/api/signals?symbol=${active}&interval=${tf}&strategy=${sigStrategy}`,
        );
        if (r.ok && !cancelled) setSig(await r.json());
      } catch {
        /* keep last overlay */
      }
    };
    load();
    const id = setInterval(load, tf === "1m" ? 15000 : 30000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [active, tf, sigStrategy]);

  // Validated trend + vol-target portfolio (daily); refresh every 15 min.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/portfolio/trend`);
        if (r.ok) setTrendPf(await r.json());
      } catch {
        /* ignore */
      }
    };
    load();
    const id = setInterval(load, 900000);
    return () => clearInterval(id);
  }, []);

  // Forward (live paper) test; the API records once per closed daily candle.
  useEffect(() => {
    const load = async () => {
      try {
        const r = await fetch(`${API}/api/forward`);
        if (r.ok) setFwd(await r.json());
      } catch {
        /* ignore */
      }
    };
    load();
    const id = setInterval(load, 600000);
    return () => clearInterval(id);
  }, []);

  const runBt = async () => {
    let url =
      API +
      `/api/backtest?symbol=${active}&interval=${btInterval}&limit=1000&strategy=${btStrategy}`;
    if (btStart) url += `&start_ms=${new Date(btStart).getTime()}`;
    if (btEnd) url += `&end_ms=${new Date(btEnd).getTime()}`;
    const r = await fetch(url);
    setBt(await r.json());
  };

  const runEvolutionSearch = async () => {
    if (!active || active === "MULTI") return;
    setEvolving(true);
    setEvoError("");
    setEvolution(null);
    try {
      const r = await fetch(API + "/api/evolution/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          symbol: active,
          interval: btInterval,
          limit: 1000,
          strategy_id: btStrategy,
          generations: 4,
          population: 16,
          elite_fraction: 0.2,
          mutation_rate: 0.35,
          mutation_scale: 0.1,
          seed: 42,
        }),
      });
      const payload = await r.json();
      if (!r.ok) throw new Error(payload.detail || "Evolution failed");
      setEvolution(payload);
    } catch (e: any) {
      setEvoError(e.message || "Evolution failed");
    } finally {
      setEvolving(false);
    }
  };

  const promoteChampion = async () => {
    if (!evolution?.champion?.params) return;
    const r = await fetch(
      `${API}/api/evolution/promote/${evolution.strategy_id}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ params: evolution.champion.params }),
      },
    );
    if (!r.ok) {
      const payload = await r.json();
      setEvoError(payload.detail || "Could not promote champion");
      return;
    }
    await refreshStrategies();
    refresh(active);
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
          <button
            className={isMulti ? "active" : ""}
            onClick={() => setActive("MULTI")}
            title="All pinned symbols side by side"
          >
            <LayoutGrid size={13} /> All
          </button>
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
          <span className={`statusDot ${apiLink}`} /> Paper only <span>·</span>{" "}
          {apiLink === "lost"
            ? "Local API offline — reconnecting"
            : apiLink === "connecting"
              ? "Connecting"
              : data?.stream_connected === false
                ? "Binance stream reconnecting"
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
              onKeyDown={(e) => {
                if (e.key === "Enter" && shownResults[0])
                  pin(shownResults[0].symbol);
              }}
            />
          </div>
          <div className="pickerResults">
            {shownResults.map((s) => (
              <button key={s.symbol} onClick={() => pin(s.symbol)}>
                <strong>{s.base}</strong>
                <span>{s.symbol}</span>
                {s.pinned ? <Pin size={13} /> : <Plus size={13} />}
              </button>
            ))}
            {searchState === "loading" && !shownResults.length && (
              <em>Searching Binance symbols…</em>
            )}
            {searchState === "done" && !results.length && (
              <em>No USDT spot pairs match “{query.trim()}”.</em>
            )}
            {searchState === "error" && (
              <em className="pickerError">
                {searchError}.{" "}
                <button
                  className="linkButton"
                  onClick={() => setSearchNonce((n) => n + 1)}
                >
                  Retry
                </button>
              </em>
            )}
          </div>
        </div>
      )}

      <main className="workspace">
        {isMulti ? (
          <MultiView pins={pins} />
        ) : !data ? (
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
            // Controls inside a header (Run, Discover, timeframe/indicator
            // buttons, selects) must click, not start a panel drag.
            draggableCancel="button, select, input, textarea, a, label, .noDrag"
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
                  {TIMEFRAMES.map((t) => (
                    <button
                      key={t}
                      className={tf === t ? "active tfButton" : "tfButton"}
                      title={`Show ${t} candles with strategy signals`}
                      onClick={() => setTf(t)}
                    >
                      {t}
                    </button>
                  ))}
                  <select
                    className="sigStrategySelect"
                    value={sigStrategy}
                    title="Whose signals to draw on the chart"
                    onChange={(e) => setSigStrategy(e.target.value)}
                  >
                    <option value="ai_regime_fusion">AI model</option>
                    <option value="trend_vol_target">Trend (1d/4h)</option>
                  </select>
                  <span className="intervalDivider" />
                  {["vol", "ema", "bb", "rsi", "macd"].map((k) => (
                    <button
                      key={k}
                      className={ind.includes(k) ? "active" : ""}
                      title={INDICATOR_LABELS[k]}
                      onClick={() =>
                        setInd((list) =>
                          list.includes(k)
                            ? list.filter((x) => x !== k)
                            : [...list, k],
                        )
                      }
                    >
                      {k.toUpperCase()}
                    </button>
                  ))}
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
              {tf === "1m" || sig?.interval === tf ? (
                <Chart
                  key={`${active}-${tf}-${drawVersion}-${ind.join()}`}
                  symbol={active}
                  candles={tf === "1m" ? data.candles : sig.candles}
                  overlays={tf === "1m" ? data.patterns : undefined}
                  signals={sig?.interval === tf ? sig : null}
                  drawMode={drawMode}
                  onDrawn={() => setDrawMode(false)}
                  indicators={ind}
                />
              ) : (
                <div className="chartWrap chartLoading">
                  <em>Loading {tf} candles and signals…</em>
                </div>
              )}
              <SignalStrip sig={sig?.interval === tf ? sig : null} tf={tf} />
              <div className="chartFooter">
                <span>
                  {(tf === "1m" ? data.candles : sig?.candles || []).length}{" "}
                  candles · {tf}
                </span>
                <span>
                  {tf === "1m" ? "Streaming live" : "Refreshes every 30s"}
                </span>
                <span className="patternTags">
                  {(data.patterns?.patterns || []).map((p: any) => (
                    <em key={p.name} className={`patternTag ${p.bias}`}>
                      {p.name} → entry {p.entry}
                    </em>
                  ))}
                  {(data.patterns?.levels || []).map((l: any) => (
                    <em key={l.price} className="patternTag">
                      {l.kind} {l.price}
                    </em>
                  ))}
                  {!data.patterns?.patterns?.length &&
                    !data.patterns?.levels?.length && (
                      <em>No patterns detected right now</em>
                    )}
                </span>
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
              {(() => {
                const edge = data.edges?.[focusStrategy] ?? data.edge;
                const meta = strategies.find((s) => s.id === focusStrategy);
                return edge ? (
                  <>
                    <div
                      className={`verdict ${edge.verdict.startsWith("ENTER") ? "up" : edge.verdict === "NO_TRADE" ? "" : "warn"}`}
                    >
                      {edge.verdict.replace(/_/g, " ")}
                      <small className="verdictWho">
                        {meta?.name ?? focusStrategy}
                      </small>
                    </div>
                    <div className="twoCol">
                      <Stat
                        label="Win probability"
                        value={pct(edge.win_probability * 100)}
                      />
                      <Stat
                        label="Net edge"
                        value={`${edge.net_edge_pct >= 0 ? "+" : ""}${edge.net_edge_pct.toFixed(3)}%`}
                      />
                      <Stat
                        label="Breakeven move"
                        value={`${edge.breakeven_move_pct.toFixed(3)}%`}
                      />
                      <Stat
                        label="Expected move"
                        value={`${edge.expected_move_pct.toFixed(3)}%`}
                      />
                      <Stat
                        label="Entry (bid)"
                        value={money(edge.suggested_entry)}
                      />
                      <Stat
                        label="Target"
                        value={
                          edge.suggested_target
                            ? money(edge.suggested_target)
                            : "—"
                        }
                      />
                      <Stat
                        label="Stop"
                        value={
                          edge.suggested_stop ? money(edge.suggested_stop) : "—"
                        }
                      />
                    </div>
                    <small className="edgeNote">{edge.note}</small>
                  </>
                ) : (
                  <em>Warming up…</em>
                );
              })()}
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

            <section key="forward" className="panel forwardPanel">
              <ForwardPanel fwd={fwd} />
            </section>

            <section key="trend" className="panel trendPanel">
              <div className="panelHeader compact">
                <div>
                  <span className="kicker">
                    Validated · daily · {trendPf?.validation?.universe}
                  </span>
                  <h2>Trend portfolio</h2>
                </div>
                {trendPf && (
                  <span className="trendExposure">
                    Exposure {trendPf.exposure_pct}%
                  </span>
                )}
              </div>
              {trendPf ? (
                <>
                  <p className="trendNote">
                    Rule: hold coins in an uptrend (EMA 8/32, 16/64, 32/128
                    days), size each to{" "}
                    {Math.round(trendPf.rule.target_vol * 100)}% annual
                    volatility, rebalance daily. Out-of-sample (
                    {trendPf.validation.period_out_of_sample}): Sharpe{" "}
                    {trendPf.validation.sharpe} vs{" "}
                    {trendPf.validation.sharpe_equal_weight} buy&amp;hold, max
                    drawdown {trendPf.validation.max_dd_pct}% vs{" "}
                    {trendPf.validation.max_dd_equal_weight_pct}%.
                  </p>
                  <div className="panelScroll">
                    <table className="trendTable">
                      <thead>
                        <tr>
                          <th>Coin</th>
                          <th>Trend</th>
                          <th>Vol/yr</th>
                          <th>Target weight</th>
                          <th />
                        </tr>
                      </thead>
                      <tbody>
                        {trendPf.holdings.map((h: any) => (
                          <tr
                            key={h.symbol}
                            className={h.action === "HOLD" ? "" : "out"}
                          >
                            <td>{h.symbol.replace("USDT", "")}</td>
                            <td
                              className={
                                h.trend > 0 ? "up" : h.trend < 0 ? "down" : ""
                              }
                            >
                              {h.trend > 0 ? "+" : ""}
                              {h.trend.toFixed(2)}
                            </td>
                            <td>{h.vol_pct ?? "—"}%</td>
                            <td>
                              <span className="weightBar">
                                <i
                                  style={{
                                    width: `${Math.min(h.weight_pct * 20, 100)}%`,
                                  }}
                                />
                              </span>
                              {h.weight_pct.toFixed(2)}%
                            </td>
                            <td>{h.action}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <p className="trendNote">
                      Last {trendPf.window.years} yrs replay: Sharpe{" "}
                      {trendPf.window.strategy.sharpe} (buy&amp;hold{" "}
                      {trendPf.window.equal_weight.sharpe}), max DD{" "}
                      {trendPf.window.strategy.max_dd}% (
                      {trendPf.window.equal_weight.max_dd}%), turnover{" "}
                      {trendPf.annualised_turnover}×/yr. Paper only.
                    </p>
                  </div>
                </>
              ) : (
                <em>Loading daily candles for the trend portfolio…</em>
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
                  if (
                    !window.confirm(
                      "Reset the paper account? All paper trades and P&L will be erased.",
                    )
                  )
                    return;
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
                  <button
                    className="discoverButton"
                    onClick={runEvolutionSearch}
                    disabled={evolving}
                    title="Mutate this strategy and compare train / validation / holdout performance"
                  >
                    <FlaskConical size={14} />
                    {evolving ? "Evolving…" : "Discover"}
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
                dates blank for the latest 1000 candles).
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
                      signals={bt.signals}
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

              {evoError && (
                <div className="inlineError evolutionError">{evoError}</div>
              )}

              {evolution && (
                <div className="evolutionPanel">
                  <div className="evolutionHead">
                    <div>
                      <span className="kicker">Evolution result</span>
                      <h3>{evolution.strategy_name}</h3>
                    </div>
                    <button className="promoteButton" onClick={promoteChampion}>
                      Promote champion
                    </button>
                  </div>

                  <div className="evolutionSummary">
                    <Stat
                      label="Validation return"
                      value={pct(evolution.champion.validation.return_pct)}
                    />
                    <Stat
                      label="Holdout return"
                      value={pct(evolution.champion.test.return_pct)}
                    />
                    <Stat
                      label="Holdout drawdown"
                      value={pct(evolution.champion.test.max_drawdown_pct)}
                    />
                    <Stat
                      label="Holdout PF"
                      value={String(
                        evolution.champion.test.profit_factor ?? "—",
                      )}
                    />
                    <Stat
                      label="Fitness"
                      value={Number(evolution.champion.fitness).toFixed(3)}
                    />
                  </div>

                  <div className="generationStrip">
                    {evolution.history.map((g: any) => (
                      <div key={g.generation} className="generationCell">
                        <span>G{g.generation + 1}</span>
                        <strong>{Number(g.best_fitness).toFixed(2)}</strong>
                        <small>{pct(g.best_validation_return_pct)}</small>
                      </div>
                    ))}
                  </div>

                  <details className="genomeDetails">
                    <summary>Champion genome</summary>
                    <div className="genomeGrid">
                      {Object.entries(evolution.champion.params).map(
                        ([key, value]: [string, any]) => (
                          <div key={key}>
                            <span>{key}</span>
                            <strong>{Number(value).toFixed(4)}</strong>
                          </div>
                        ),
                      )}
                    </div>
                  </details>

                  <p className="evolutionNote">{evolution.note}</p>
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

// Split-screen view: every pinned symbol's chart + live signal side by side.
function MultiView({ pins }: { pins: PinEntry[] }) {
  const [data, setData] = useState<Record<string, MarketData>>({});
  useEffect(() => {
    let dead = false;
    const load = async () => {
      const out: Record<string, MarketData> = {};
      await Promise.all(
        pins.map(async (p) => {
          try {
            const r = await fetch(API + "/api/market?symbol=" + p.symbol);
            if (r.ok) out[p.symbol] = await r.json();
          } catch {
            /* ignore */
          }
        }),
      );
      if (!dead) setData(out);
    };
    load();
    const id = setInterval(load, 6000);
    return () => {
      dead = true;
      clearInterval(id);
    };
  }, [pins]);

  return (
    <div className="multiGrid">
      {pins.map((p) => {
        const d = data[p.symbol];
        const best = d?.decisions
          ? Object.entries(d.decisions)
              .filter(([, v]: [string, any]) => v && v.action)
              .sort(
                (a, b) => (b[1] as any).confidence - (a[1] as any).confidence,
              )[0]
          : null;
        const edge = d?.edge;
        return (
          <section key={p.symbol} className="panel multiCell">
            <div className="multiHead">
              <strong>{baseOf(p.symbol)}</strong>
              <span className="tabPrice">
                {p.market ? money(p.market.price) : "—"}
              </span>
              {best && (
                <em
                  className={`verdictMini ${(best[1] as any).action.toLowerCase()}`}
                >
                  {(best[1] as any).action}{" "}
                  {Math.round((best[1] as any).confidence * 100)}% · {best[0]}
                </em>
              )}
            </div>
            {d ? (
              <Chart
                symbol={p.symbol}
                candles={d.candles}
                overlays={d.patterns}
                drawMode={false}
                indicators={["vol", "ema"]}
              />
            ) : (
              <em className="multiLoading">Loading…</em>
            )}
            {edge && (
              <div
                className={`verdict mini ${edge.verdict.startsWith("ENTER") ? "up" : edge.verdict === "WAIT_EDGE" ? "warn" : ""}`}
              >
                {edge.verdict} — net edge {edge.net_edge_pct}% · win{" "}
                {Math.round(edge.win_probability * 100)}%
              </div>
            )}
            <div className="patternTags">
              {(d?.patterns?.patterns || []).map((p2: any) => (
                <em key={p2.name} className={`patternTag ${p2.bias}`}>
                  {p2.name} → {p2.entry}
                </em>
              ))}
            </div>
          </section>
        );
      })}
      {!pins.length && <em>Pin some symbols to compare them here.</em>}
    </div>
  );
}
const _tsOf = (c: Candle) => Math.floor(c.open_time / 1000);

// Indicator helpers — all computed locally from candle arrays.
function emaArr(values: number[], period: number): (number | null)[] {
  if (!values.length) return [];
  const k = 2 / (period + 1);
  const out: (number | null)[] = [null];
  let prev = values[0];
  for (let i = 1; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out.push(i >= period - 1 ? prev : null);
  }
  return out;
}
function smaArr(values: number[], period: number): (number | null)[] {
  return values.map((_, i) =>
    i < period - 1
      ? null
      : values.slice(i - period + 1, i + 1).reduce((a, b) => a + b, 0) / period,
  );
}
function rsiArr(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = closes.map(() => null);
  for (let i = period; i < closes.length; i++) {
    let g = 0;
    let l = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = closes[j] - closes[j - 1];
      if (d > 0) g += d;
      else l -= d;
    }
    out[i] = l === 0 ? 100 : 100 - 100 / (1 + g / l);
  }
  return out;
}

function Chart({
  symbol,
  candles,
  overlays,
  signals,
  drawMode,
  onDrawn,
  indicators = ["vol", "ema"],
}: {
  symbol: string;
  candles: Candle[];
  overlays?: any;
  signals?: ChartSignals | null;
  drawMode: boolean;
  onDrawn?: () => void;
  indicators?: string[];
}) {
  const ref = useRef<HTMLDivElement>(null);
  const chartRef = useRef<any>(null);
  const seriesRef = useRef<any>(null);
  const pendingRef = useRef<TrendLine | null>(null);
  const markersApiRef = useRef<any>(null);
  const patternMarkersRef = useRef<any[]>([]);
  const planLinesRef = useRef<any[]>([]);
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
      // Zoom: mouse wheel scales, drag pans; pinch on touchpads.
      handleScroll: {
        mouseWheel: true,
        pressedMouseMove: true,
        horzTouchDrag: true,
        vertTouchDrag: false,
      },
      handleScale: {
        mouseWheel: true,
        pinch: true,
        axisPressedMouseMove: true,
      },
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

    // TradingView-style indicator overlays / panes.
    const closes = candles.map((c) => c.close);
    const times = candles.map((c) => Math.floor(c.open_time / 1000));
    const noDeco = {
      lastValueVisible: false,
      priceLineVisible: false,
      crosshairMarkerVisible: false,
    };
    const toLine = (arr: (number | null)[], color: string, pane = 0, w = 1) => {
      const s = chart.addSeries(
        LineSeries,
        { color, lineWidth: w as any, ...noDeco },
        pane,
      );
      s.setData(
        arr
          .map((v, i) =>
            v == null ? null : { time: times[i] as any, value: v },
          )
          .filter((x): x is any => x != null),
      );
      return s;
    };
    let pane = 0;
    if (indicators.includes("vol")) {
      pane += 1;
      const vol = chart.addSeries(
        HistogramSeries,
        { priceFormat: { type: "volume" }, ...noDeco },
        pane,
      );
      vol.setData(
        candles.map((c) => ({
          time: Math.floor(c.open_time / 1000) as any,
          value: c.volume,
          color:
            c.close >= c.open ? "rgba(22,139,106,0.5)" : "rgba(196,79,94,0.5)",
        })),
      );
      vol.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    }
    if (indicators.includes("ema")) {
      toLine(emaArr(closes, 9), "#2563eb", 0, 2);
      toLine(emaArr(closes, 50), "#f59e0b", 0, 1);
    }
    if (indicators.includes("bb")) {
      const basis = smaArr(closes, 20);
      const dev = closes.map((_, i) => {
        if (i < 19) return null;
        const seg = closes.slice(i - 19, i + 1);
        const m = seg.reduce((a, b) => a + b, 0) / 20;
        return Math.sqrt(seg.reduce((a, v) => a + (v - m) ** 2, 0) / 19);
      });
      toLine(basis, "#64748b");
      toLine(
        basis.map((b, i) =>
          b == null || dev[i] == null ? null : b + 2 * (dev[i] as number),
        ),
        "#94a3b8",
      );
      toLine(
        basis.map((b, i) =>
          b == null || dev[i] == null ? null : b - 2 * (dev[i] as number),
        ),
        "#94a3b8",
      );
    }
    if (indicators.includes("rsi")) {
      pane += 1;
      const r = toLine(rsiArr(closes), "#7c3aed", pane, 2);
      r.createPriceLine({
        price: 70,
        color: "#c4b5fd",
        lineWidth: 1,
        lineStyle: 2,
        title: "",
      });
      r.createPriceLine({
        price: 30,
        color: "#c4b5fd",
        lineWidth: 1,
        lineStyle: 2,
        title: "",
      });
    }
    if (indicators.includes("macd")) {
      pane += 1;
      const fast = emaArr(closes, 12);
      const slow = emaArr(closes, 26);
      const macd = closes.map((_, i) =>
        fast[i] == null || slow[i] == null
          ? null
          : (fast[i] as number) - (slow[i] as number),
      );
      const vals = macd.map((v) => v ?? 0);
      const signal = emaArr(vals, 9).map((v, i) =>
        macd[i] == null ? null : v,
      );
      toLine(macd, "#2563eb", pane, 2);
      toLine(signal, "#f59e0b", pane);
      const hist = chart.addSeries(HistogramSeries, { ...noDeco }, pane);
      hist.setData(
        macd
          .map((v, i) =>
            v == null || signal[i] == null
              ? null
              : {
                  time: times[i] as any,
                  value: v - (signal[i] as number),
                  color:
                    v >= (signal[i] as number)
                      ? "rgba(22,139,106,0.5)"
                      : "rgba(196,79,94,0.5)",
                },
          )
          .filter((x): x is any => x != null),
      );
    }

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
        lv.kind === "support" ? "#0e7490" : "#0891b2",
        2,
      );
    }
    for (const pat of overlays?.patterns || []) {
      // Auto-detected pattern geometry uses cyan/teal so it cannot be
      // confused with candle direction or manual violet drawings.
      const color = pat.bias === "neutral" ? "#0e7490" : "#0891b2";
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
    patternMarkersRef.current = markers;
    markersApiRef.current = createSeriesMarkers(series, markers);

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
      markersApiRef.current = null;
      planLinesRef.current = [];
    };
  }, [candles.length > 0]);

  // Signals redraw in place (keeps zoom) whenever a new overlay arrives.
  useEffect(() => {
    const series = seriesRef.current;
    if (!series || !candles.length) return;
    const markers: any[] = [...patternMarkersRef.current];
    // Strategy signals: executed BUY/EXIT arrows, model setups (leans that
    // need not beat fees) as small dots, and the current plan as labelled
    // price lines. Markers outside the loaded candles are dropped.
    const first = _tsOf(candles[0]);
    const lastTs = _tsOf(candles[candles.length - 1]);
    for (const m of signals?.markers || []) {
      if (m.time < first || m.time > lastTs) continue;
      if (m.side === "BUY")
        markers.push({
          time: m.time as any,
          position: "belowBar",
          color: "#2563eb",
          shape: "arrowUp",
          text: m.text,
        });
      else if (m.side === "SELL")
        markers.push({
          time: m.time as any,
          position: "aboveBar",
          color: m.open ? "#6b7280" : "#b45309",
          shape: "arrowDown",
          text: m.text,
        });
      else
        markers.push({
          time: m.time as any,
          position: m.side === "SETUP_UP" ? "belowBar" : "aboveBar",
          color: m.side === "SETUP_UP" ? "#16a34a99" : "#dc262699",
          shape: "circle",
          size: 0.6,
          text: m.text,
        });
    }
    for (const line of planLinesRef.current) series.removePriceLine(line);
    planLinesRef.current = [];
    const plan = signals?.plan;
    if (plan) {
      const style = plan.active ? 0 : 2; // solid when actionable, dashed = watch
      const tag = plan.active ? plan.side : `${plan.side} watch`;
      const pfx = plan.timeframe_model === "trend" ? "TREND" : "AI";
      const add = (opts: any) =>
        planLinesRef.current.push(series.createPriceLine(opts));
      add({
        price: plan.entry,
        color: "#2563eb",
        lineWidth: 1,
        lineStyle: style,
        axisLabelVisible: true,
        title: `${pfx} ENTRY ${tag}`,
      });
      add({
        price: plan.stop,
        color: "#c44f5e",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `${pfx} STOP`,
      });
      add({
        price: plan.target,
        color: "#168b6a",
        lineWidth: 1,
        lineStyle: 2,
        axisLabelVisible: true,
        title: `${pfx} TARGET`,
      });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    markersApiRef.current?.setMarkers(markers);
  }, [signals, candles.length > 0]);

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

  return (
    <div className="chartWrap">
      <div ref={ref} className="chart" />
      <button
        className="fitButton"
        title="Reset zoom"
        onClick={() => chartRef.current?.timeScale().fitContent()}
      >
        Fit
      </button>
    </div>
  );
}

function Sparkline({ points }: { points: any[] }) {
  if (points.length < 2) return null;
  const vals = points.flatMap((p) => [p.strategy, p.equal_weight]);
  const lo = Math.min(...vals);
  const hi = Math.max(...vals);
  const span = hi - lo || 1;
  const path = (key: string) =>
    points
      .map((p, i) => {
        const x = (i / (points.length - 1)) * 100;
        const y = 36 - ((p[key] - lo) / span) * 32;
        return `${i ? "L" : "M"}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(" ");
  return (
    <svg className="fwdSpark" viewBox="0 0 100 40" preserveAspectRatio="none">
      <path d={path("equal_weight")} className="bench" />
      <path d={path("strategy")} className="strat" />
    </svg>
  );
}

function ForwardPanel({ fwd }: { fwd: any }) {
  const t = fwd?.trend;
  const ai = fwd?.ai;
  const status = t?.status || "collecting";
  const label =
    status === "on_track"
      ? "On track"
      : status === "edge_gone"
        ? "Edge gone — stop rule hit"
        : `Collecting ${t?.days_evaluated ?? 0}/${t?.stop_rule?.min_days ?? 180} days`;
  const fmt = (v: any, suffix = "") =>
    v === null || v === undefined ? "—" : `${v}${suffix}`;
  // Sharpe and drawdown are meaningless with fewer than 2 scored days.
  const enough = (t?.days_evaluated ?? 0) >= 2;
  return (
    <>
      <div className="panelHeader compact">
        <div>
          <span className="kicker">Live paper · no backfill</span>
          <h2>Forward test</h2>
        </div>
        <span className={`fwdStatus ${status}`}>{label}</span>
      </div>
      {!fwd ? (
        <em>Loading forward results…</em>
      ) : (
        <>
          <p className="trendNote">
            Trend portfolio ({t.coins} coins) recorded daily from closed candles
            since {t.first_date || "today"}. Stop rule (fixed in advance): after{" "}
            {t.stop_rule.min_days} days, Sharpe &lt; {t.stop_rule.min_sharpe} or
            drawdown beyond {t.stop_rule.max_dd_pct}% means the edge is gone.
          </p>
          <div className="panelScroll">
            <table className="trendTable">
              <thead>
                <tr>
                  <th />
                  <th>Live</th>
                  <th>Expected</th>
                  <th>Buy&amp;hold</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <td>Return</td>
                  <td>{fmt(t.live?.total, "%")}</td>
                  <td>{fmt(t.expected.cagr_pct, "%/yr")}</td>
                  <td>{fmt(t.equal_weight?.total, "%")}</td>
                </tr>
                <tr>
                  <td>Sharpe</td>
                  <td>{enough ? fmt(t.live?.sharpe) : "—"}</td>
                  <td>{t.expected.sharpe}</td>
                  <td>{enough ? fmt(t.equal_weight?.sharpe) : "—"}</td>
                </tr>
                <tr>
                  <td>Max DD</td>
                  <td>{enough ? fmt(t.live?.max_dd, "%") : "—"}</td>
                  <td>{t.expected.max_dd_pct}%</td>
                  <td>{enough ? fmt(t.equal_weight?.max_dd, "%") : "—"}</td>
                </tr>
              </tbody>
            </table>
            <Sparkline points={t.equity || []} />
            {(["core", "extended"] as const).map((g) => {
              const a = ai.groups?.[g];
              if (!a) return null;
              return (
                <p className="trendNote" key={g}>
                  AI daily signals,{" "}
                  {g === "core" ? "6 training coins" : "other coins"}:{" "}
                  {a.resolved} resolved / {a.signals} logged. IC live{" "}
                  {fmt(a.ic)} vs expected {a.expected_ic}. Resolved LONG calls{" "}
                  {a.long_calls}, avg {fmt(a.long_avg_bps, " bps")} vs{" "}
                  {a.round_trip_cost_bps} bps cost, hit rate{" "}
                  {a.long_hit_rate === null
                    ? "—"
                    : `${Math.round(a.long_hit_rate * 100)}%`}
                  .
                </p>
              );
            })}
            {t.days_evaluated < 30 && (
              <p className="trendNote">
                Early days: under ~30 days, live numbers are mostly noise.
              </p>
            )}
          </div>
        </>
      )}
    </>
  );
}

function SignalStrip({ sig, tf }: { sig: any; tf: string }) {
  if (!sig)
    return <div className="signalStrip muted">Loading {tf} signal…</div>;
  const d = sig.decision || {};
  const plan: (SignalPlan & { size?: number }) | null = sig.plan || null;
  const hasModel = plan?.reliability_ic !== undefined;
  const ic = plan?.reliability_ic ?? 0;
  const reliable = d.regime !== "NO_EDGE";
  const trades = (sig.markers || []).filter(
    (m: SignalMarker) => m.side === "BUY",
  ).length;
  const setups = (sig.markers || []).filter((m: SignalMarker) =>
    m.side.startsWith("SETUP"),
  ).length;
  return (
    <div
      className={`signalStrip ${reliable && d.action !== "FLAT" ? "" : "muted"}`}
    >
      <strong className={`sigAction ${String(d.action).toLowerCase()}`}>
        {d.action || "—"}
      </strong>
      <span>{d.regime}</span>
      {plan && (
        <span>
          {plan.active ? "Plan" : "Watch"} {plan.side}: entry{" "}
          <b>{plan.entry.toPrecision(6)}</b> · stop{" "}
          <b className="neg">{plan.stop.toPrecision(6)}</b> · target{" "}
          <b className="pos">{plan.target.toPrecision(6)}</b>
        </span>
      )}
      {plan?.expected_move_bps !== undefined && (
        <span>
          model {plan.expected_move_bps.toFixed(1)} bps / {plan.horizon_candles}{" "}
          candles vs cost {plan.cost_bps?.toFixed(0)} bps
        </span>
      )}
      {plan?.size !== undefined && (
        <span>
          size {(plan.size * 100).toFixed(0)}% of allocation (vol target)
        </span>
      )}
      {hasModel && (
        <span title="Out-of-sample correlation between the model's prediction and the realised move, measured in walk-forward tests for this timeframe">
          reliability IC {ic >= 0 ? "+" : ""}
          {ic.toFixed(3)}
          {reliable ? "" : " — no reliable edge on this timeframe"}
        </span>
      )}
      <span>
        {trades} trades{setups ? ` · ${setups} setups` : ""} in window
      </span>
      {d.reasons?.length > 0 && !reliable && (
        <span>{d.reasons[d.reasons.length - 1]}</span>
      )}
    </div>
  );
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
