import React,{useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createChart,CandlestickSeries} from 'lightweight-charts';
import {Activity,BarChart3,BrainCircuit,FlaskConical,Play,RefreshCw,RotateCcw,WalletCards} from 'lucide-react';
import './styles.css';

type Candle={open_time:number;open:number;high:number;low:number;close:number;volume:number;close_time:number};
type MarketData={market:any;decision:any;account:any;candles:Candle[];strategy:string;mode:string};
const API='http://127.0.0.1:8000';
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(n||0);
const pct=(n:number)=>Number(n||0).toFixed(2)+'%';

function App(){
 const [data,setData]=useState<MarketData|null>(null);
 const [error,setError]=useState('');
 const [bt,setBt]=useState<any>(null);
 const [amount,setAmount]=useState(250);
 const chartRef=useRef<HTMLDivElement>(null);
 const chartApiRef=useRef<any>(null);
 const seriesRef=useRef<any>(null);

 const refresh=async()=>{try{const r=await fetch(API+'/api/market');if(!r.ok)throw new Error(await r.text());setData(await r.json());setError('')}catch(e:any){setError(e.message)}};
 useEffect(()=>{refresh();const ws=new WebSocket('ws://127.0.0.1:8000/ws/market');ws.onmessage=(event)=>{const packet=JSON.parse(event.data);if(packet.type!=='market')return;setData(current=>{if(!current)return current;const next={...current,market:packet.market,decision:packet.decision||current.decision,account:packet.account||current.account,stream_connected:packet.stream_connected};if(packet.candle){const candles=[...current.candles];const i=candles.length-1;if(i>=0&&candles[i].open_time===packet.candle.open_time)candles[i]=packet.candle;else candles.push(packet.candle);next.candles=candles.slice(-120)}return next})};ws.onerror=()=>setError('Live market stream disconnected');ws.onopen=()=>setError('');return()=>ws.close()},[]);
 useEffect(()=>{if(!chartRef.current||!data?.candles?.length||chartApiRef.current)return;const chart=createChart(chartRef.current,{height:430,layout:{background:{color:'#ffffff'},textColor:'#6b7280',fontFamily:'ui-monospace, SFMono-Regular, Menlo, monospace'},grid:{vertLines:{color:'#f0f2f5'},horzLines:{color:'#f0f2f5'}},rightPriceScale:{borderColor:'#e5e7eb'},timeScale:{borderColor:'#e5e7eb',timeVisible:true,secondsVisible:false}});const series=chart.addSeries(CandlestickSeries,{upColor:'#168b6a',downColor:'#c44f5e',wickUpColor:'#168b6a',wickDownColor:'#c44f5e',borderVisible:false});series.setData(data.candles.map(c=>({time:(c.open_time/1000) as any,open:c.open,high:c.high,low:c.low,close:c.close})));chart.timeScale().fitContent();chartApiRef.current=chart;seriesRef.current=series;const obs=new ResizeObserver(()=>chart.applyOptions({width:chartRef.current?.clientWidth||900}));obs.observe(chartRef.current);return()=>{obs.disconnect();chart.remove();chartApiRef.current=null;seriesRef.current=null}},[!!data?.candles?.length]);
 useEffect(()=>{const c=data?.candles?.[data.candles.length-1];if(!c||!seriesRef.current)return;seriesRef.current.update({time:(c.open_time/1000) as any,open:c.open,high:c.high,low:c.low,close:c.close})},[data?.candles?.[data.candles.length-1]?.close,data?.candles?.[data.candles.length-1]?.high,data?.candles?.[data.candles.length-1]?.low]);

 const order=async(side:string)=>{const r=await fetch(API+'/api/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({side,amount_usd:amount})});if(!r.ok){setError((await r.json()).detail||'Order failed');return}setError('');refresh()};
 const runBt=async()=>{const r=await fetch(API+'/api/backtest?interval=5m&limit=500');setBt(await r.json())};

 if(!data)return <div className="loading"><span>PulseShift</span><small>Connecting to the local quant engine…</small>{error&&<em>{error}</em>}</div>;
 const d=data.decision,a=data.account,m=data.market;
 return <div className="appShell">
   <header className="topbar">
    <div className="brand"><div className="brandMark">P</div><div><strong>PulseShift</strong><span>Local quant research</span></div></div>
    <nav><button className="active">Terminal</button><button disabled>Replay</button><button disabled>Research</button></nav>
    <div className="marketStatus"><span className="statusDot"/> Paper only <span>·</span> {data.stream_connected===false?"Reconnecting":"Live Binance stream"}</div>
   </header>

   <main className="workspace">
    <section className="overview panel">
      <div className="assetBlock"><span>BTC / USDT</span><strong>{money(m.price)}</strong><small className={m.change_24h_pct>=0?'up':'down'}>{m.change_24h_pct>=0?'+':''}{pct(m.change_24h_pct)} today</small></div>
      <Stat label="Bid" value={money(m.bid)}/>
      <Stat label="Ask" value={money(m.ask)}/>
      <Stat label="Spread" value={m.spread_bps.toFixed(2)+' bps'}/>
      <Stat label="24h quote volume" value={money(m.volume_24h)}/>
      <button className="iconButton" onClick={refresh} title="Refresh"><RefreshCw size={16}/></button>
    </section>

    <section className="mainGrid">
      <article className="chartPanel panel">
        <div className="panelHeader"><div><span className="kicker">Market</span><h2>BTC/USDT</h2></div><div className="intervals"><button className="active">1m</button><button disabled>5m</button><button disabled>15m</button><button disabled>1h</button></div></div>
        <div ref={chartRef} className="chart"/>
        <div className="chartFooter"><span>120 candles</span><span>Refreshes every 5 seconds</span></div>
      </article>

      <aside className="sideColumn">
        <section className="panel intelligence">
          <div className="panelHeader compact"><div><span className="kicker">Model</span><h2>Strategy intelligence</h2></div><BrainCircuit size={18}/></div>
          <div className="decision"><div><span>Current action</span><strong className={d.action==='LONG'?'up':d.action==='SHORT'?'down':''}>{d.action}</strong></div><small>{data.strategy}</small></div>
          <Gauge label="Signal confidence" value={d.confidence}/>
          <Gauge label="Regime fit" value={d.regime_confidence}/>
          <Gauge label="Execution quality" value={d.execution_confidence}/>
          <div className="regimeRow"><span>Market regime</span><strong>{d.regime}</strong></div>
          <ul className="reasons">{d.reasons.map((r:string)=><li key={r}>{r}</li>)}</ul>
        </section>

        <section className="panel portfolio">
          <div className="panelHeader compact"><div><span className="kicker">Paper account</span><h2>Portfolio</h2></div><WalletCards size={18}/></div>
          <div className="equityRow"><strong>{money(a.equity)}</strong><span className={a.total_return_pct>=0?'up':'down'}>{pct(a.total_return_pct)}</span></div>
          <div className="twoCol"><Stat label="Cash" value={money(a.cash)}/><Stat label="BTC position" value={Number(a.btc_qty).toFixed(6)}/><Stat label="Entry" value={a.entry_price?money(a.entry_price):'—'}/><Stat label="Unrealized P&L" value={money(a.unrealized_pnl)}/></div>
        </section>

        <section className="panel execution">
          <div className="panelHeader compact"><div><span className="kicker">Simulator</span><h2>Paper execution</h2></div><BarChart3 size={18}/></div>
          <label>Notional (USDT)</label><input type="number" value={amount} min={10} step={10} onChange={e=>setAmount(Number(e.target.value))}/>
          <div className="tradeButtons"><button className="tradeButton buy" onClick={()=>order('BUY')}><span>Buy BTC</span><small>Simulated ask fill</small></button><button className="tradeButton sell" onClick={()=>order('SELL')}><span>Sell BTC</span><small>Simulated bid fill</small></button></div>
          <button className="secondary actionButton" onClick={()=>order('CLOSE')}>Close full position</button>
          <button className="linkButton" onClick={async()=>{await fetch(API+'/api/reset',{method:'POST'});refresh()}}><RotateCcw size={14}/> Reset paper account</button>
          {error&&<div className="inlineError">{error}</div>}
        </section>
      </aside>
    </section>

    <section className="bottomGrid">
      <section className="panel ledger">
        <div className="panelHeader"><div><span className="kicker">Activity</span><h2>Execution ledger</h2></div><Activity size={18}/></div>
        <div className="tableWrap"><table><thead><tr><th>Time</th><th>Side</th><th>Qty BTC</th><th>Price</th><th>Fee</th><th>P&L</th></tr></thead><tbody>{a.trades.length?a.trades.map((t:any)=><tr key={t.ts}><td>{new Date(t.ts).toLocaleTimeString()}</td><td className={t.side==='BUY'?'up':'down'}>{t.side}</td><td>{Number(t.qty).toFixed(6)}</td><td>{money(t.price)}</td><td>{money(t.fee)}</td><td>{money(t.pnl)}</td></tr>):<tr><td colSpan={6} className="empty">No paper executions yet. Use the simulator to create the first trade.</td></tr>}</tbody></table></div>
      </section>

      <section className="panel arena">
        <div className="panelHeader"><div><span className="kicker">Research</span><h2>Strategy arena</h2></div><button className="runButton" onClick={runBt}><Play size={14}/> Run baseline</button></div>
        <p>Run the EMA Momentum baseline across the latest 500 five-minute candles.</p>
        {bt?<div className="arenaStats"><Stat label="Return" value={pct(bt.return_pct)}/><Stat label="Round trips" value={String(bt.round_trips)}/><Stat label="Win rate" value={pct(bt.win_rate_pct)}/><Stat label="Max drawdown" value={pct(bt.max_drawdown_pct)}/><Stat label="Profit factor" value={bt.profit_factor??'—'}/></div>:<div className="arenaEmpty"><FlaskConical size={20}/><span>No experiment results yet</span></div>}
      </section>
    </section>
   </main>
 </div>
}

function Stat({label,value}:{label:string,value:string}){return <div className="stat"><span>{label}</span><strong>{value}</strong></div>}
function Gauge({label,value}:{label:string,value:number}){return <div className="gauge"><div><span>{label}</span><strong>{Math.round(value*100)}%</strong></div><div className="track"><i style={{width:Math.max(0,Math.min(100,value*100))+'%'}}/></div></div>}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
