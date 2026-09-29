import React,{useEffect,useMemo,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {createChart,CandlestickSeries} from 'lightweight-charts';
import {Activity,BrainCircuit,FlaskConical,Play,RotateCcw,ShieldCheck,TrendingUp,WalletCards} from 'lucide-react';
import './styles.css';

type Candle={open_time:number;open:number;high:number;low:number;close:number;volume:number;close_time:number};
type MarketData={market:any;decision:any;account:any;candles:Candle[];strategy:string;mode:string};
const API='http://127.0.0.1:8000';
const money=(n:number)=>new Intl.NumberFormat('en-US',{style:'currency',currency:'USD',maximumFractionDigits:2}).format(n||0);
const pct=(n:number)=>Number(n||0).toFixed(2)+'%';

function App(){
 const [data,setData]=useState<MarketData|null>(null); const [error,setError]=useState(''); const [bt,setBt]=useState<any>(null); const [amount,setAmount]=useState(250); const chartRef=useRef<HTMLDivElement>(null);
 const refresh=async()=>{try{const r=await fetch(API+'/api/market');if(!r.ok)throw new Error(await r.text());setData(await r.json());setError('')}catch(e:any){setError(e.message)}};
 useEffect(()=>{refresh();const id=setInterval(refresh,5000);return()=>clearInterval(id)},[]);
 useEffect(()=>{if(!chartRef.current||!data?.candles)return;chartRef.current.innerHTML='';const chart=createChart(chartRef.current,{height:410,layout:{background:{color:'#0A0D12'},textColor:'#7f8aa3'},grid:{vertLines:{color:'#171c25'},horzLines:{color:'#171c25'}},rightPriceScale:{borderColor:'#202633'},timeScale:{borderColor:'#202633',timeVisible:true,secondsVisible:false}});const series=chart.addSeries(CandlestickSeries,{upColor:'#38d996',downColor:'#ff6b7a',wickUpColor:'#38d996',wickDownColor:'#ff6b7a',borderVisible:false});series.setData(data.candles.map(c=>({time:(c.open_time/1000) as any,open:c.open,high:c.high,low:c.low,close:c.close})));chart.timeScale().fitContent();const obs=new ResizeObserver(()=>chart.applyOptions({width:chartRef.current?.clientWidth||800}));obs.observe(chartRef.current);return()=>{obs.disconnect();chart.remove()}},[data?.candles]);
 const order=async(side:string)=>{const r=await fetch(API+'/api/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({side,amount_usd:amount})});if(!r.ok){alert((await r.json()).detail||'Order failed')}refresh()};
 const runBt=async()=>{const r=await fetch(API+'/api/backtest?interval=5m&limit=500');setBt(await r.json())};
 if(!data)return <div className="loading">PulseShift · connecting to local quant engine…<small>{error}</small></div>;
 const d=data.decision,a=data.account,m=data.market;
 return <div className="shell">
   <header className="topbar"><div className="brand"><div className="mark">PS</div><div><b>PULSESHIFT</b><span>LOCAL QUANT LAB</span></div></div><nav><button className="active">Terminal</button><button>Replay</button><button>Research</button></nav><div className="status"><span className="dot"/> PAPER · BINANCE</div></header>
   <main className="workspace">
    <section className="heroCard panel">
      <div className="instrument"><div><span className="eyebrow">SPOT / BTCUSDT</span><h1>{money(m.price)}</h1><div className={m.change_24h_pct>=0?'positive':'negative'}>{m.change_24h_pct>=0?'+':''}{pct(m.change_24h_pct)} · 24H</div></div><div className="quoteStrip"><Metric label="Bid" value={money(m.bid)}/><Metric label="Ask" value={money(m.ask)}/><Metric label="Spread" value={m.spread_bps.toFixed(2)+' bps'}/></div></div>
      <div ref={chartRef} className="chart"/>
      <div className="chartFooter"><span>1m · 120 candles</span><span>Auto refresh 5s</span></div>
    </section>
    <aside className="rail">
      <div className="panel decisionCard"><div className="sectionTitle"><BrainCircuit size={16}/> Strategy intelligence</div><div className="decisionRow"><div><span>Action</span><strong className={d.action==='LONG'?'positive':d.action==='SHORT'?'negative':''}>{d.action}</strong></div><div className="model">{data.strategy}</div></div><Gauge label="Signal confidence" value={d.confidence}/><Gauge label="Regime confidence" value={d.regime_confidence}/><Gauge label="Execution quality" value={d.execution_confidence}/><div className="regime"><span>Regime</span><b>{d.regime}</b></div><div className="reasonList">{d.reasons.map((r:string)=><div key={r}><span className="bullet"/>{r}</div>)}</div></div>
      <div className="panel accountCard"><div className="sectionTitle"><WalletCards size={16}/> Paper portfolio</div><div className="equity">{money(a.equity)}<span className={a.total_return_pct>=0?'positive':'negative'}>{pct(a.total_return_pct)}</span></div><div className="accountGrid"><Metric label="Cash" value={money(a.cash)}/><Metric label="BTC" value={Number(a.btc_qty).toFixed(6)}/><Metric label="Entry" value={a.entry_price?money(a.entry_price):'—'}/><Metric label="Unrealized" value={money(a.unrealized_pnl)}/></div></div>
      <div className="panel orderCard"><div className="sectionTitle"><ShieldCheck size={16}/> Paper execution</div><label>Notional (USDT)</label><input type="number" value={amount} min={10} step={10} onChange={e=>setAmount(Number(e.target.value))}/><div className="orderButtons"><button className="buy" onClick={()=>order('BUY')}>Buy BTC</button><button className="sell" onClick={()=>order('SELL')}>Sell BTC</button></div><button className="secondary" onClick={()=>order('CLOSE')}>Close position</button><button className="reset" onClick={async()=>{await fetch(API+'/api/reset',{method:'POST'});refresh()}}><RotateCcw size={14}/> Reset account</button></div>
    </aside>
    <section className="panel activityPanel"><div className="sectionTitle"><Activity size={16}/> Execution ledger</div><div className="tableWrap"><table><thead><tr><th>Time</th><th>Side</th><th>Qty BTC</th><th>Price</th><th>Fee</th><th>P&L</th></tr></thead><tbody>{a.trades.length?a.trades.map((t:any)=><tr key={t.ts}><td>{new Date(t.ts).toLocaleTimeString()}</td><td className={t.side==='BUY'?'positive':'negative'}>{t.side}</td><td>{Number(t.qty).toFixed(6)}</td><td>{money(t.price)}</td><td>{money(t.fee)}</td><td>{money(t.pnl)}</td></tr>):<tr><td colSpan={6} className="empty">No paper executions yet.</td></tr>}</tbody></table></div></section>
    <section className="panel labPanel"><div className="labHead"><div><div className="sectionTitle"><FlaskConical size={16}/> Strategy arena</div><p>Quick baseline validation using the same Python strategy engine.</p></div><button onClick={runBt}><Play size={14}/> Run 500 × 5m</button></div>{bt&&<div className="metricGrid"><Metric label="Return" value={pct(bt.return_pct)}/><Metric label="Round trips" value={String(bt.round_trips)}/><Metric label="Win rate" value={pct(bt.win_rate_pct)}/><Metric label="Max drawdown" value={pct(bt.max_drawdown_pct)}/><Metric label="Profit factor" value={bt.profit_factor??'—'}/></div>}</section>
   </main>
   {error&&<div className="errorBar">{error}</div>}
 </div>
}
function Metric({label,value}:{label:string,value:string}){return <div className="metric"><span>{label}</span><b>{value}</b></div>}
function Gauge({label,value}:{label:string,value:number}){return <div className="gauge"><div><span>{label}</span><b>{Math.round(value*100)}%</b></div><div className="track"><i style={{width:Math.max(0,Math.min(100,value*100))+'%'}}/></div></div>}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
