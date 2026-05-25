//+------------------------------------------------------------------+
//|                                    ScalpCity_Webhook_EA.mq5     |
//|         Polls webhook server and executes Scalp City signals     |
//|         Full partial TP management matching Telegram group       |
//+------------------------------------------------------------------+
#property copyright "MAXN Scalp City"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade        trade;
CPositionInfo posInfo;

//+------------------------------------------------------------------+
//| INPUTS                                                           |
//+------------------------------------------------------------------+
input group "=== SERVER SETTINGS ==="
input string WebhookURL    = "https://YOUR-APP-NAME.onrender.com/poll"; // Your Render URL
input int    PollSeconds   = 2;    // How often to poll server (seconds)

input group "=== TRADE SETTINGS ==="
input double LotSize       = 0.05; // Total lot size
input double Partial_Lots  = 0.01; // Lots to close at Partial
input double TP1_Lots      = 0.01; // Lots to close at TP1
input double TP2_Lots      = 0.01; // Lots to close at TP2
input double TP3_Lots      = 0.01; // Lots to close at TP3 (leaves 0.01 running)
input double BE_Buffer     = 2.0;  // Break-even buffer in price points after TP1
input int    Slippage      = 30;   // Max slippage in points

input group "=== EA SETTINGS ==="
input int    Magic         = 20250101;
input string EA_Comment    = "ScalpCity_Webhook";
input bool   LogAll        = true; // Log all poll responses

//+------------------------------------------------------------------+
//| GLOBAL STATE                                                     |
//+------------------------------------------------------------------+
datetime lastPollTime  = 0;
int      httpHandle    = INVALID_HANDLE;

// Active trade tracking
bool   partialHit  = false;
bool   tp1Hit      = false;
bool   tp2Hit      = false;
bool   tp3Hit      = false;
int    posDir      = 0;      // 1=long, -1=short, 0=flat
double tpPartialLvl= 0;
double tp1Lvl      = 0;
double tp2Lvl      = 0;
double tp3Lvl      = 0;
double slLvl       = 0;

//+------------------------------------------------------------------+
//| INIT                                                             |
//+------------------------------------------------------------------+
int OnInit()
{
    trade.SetExpertMagicNumber(Magic);
    trade.SetDeviationInPoints(Slippage);

    if(WebhookURL == "https://YOUR-APP-NAME.onrender.com/poll")
    {
        Alert("⚠️ Please set your Render webhook URL in EA settings!");
        Print("ERROR: WebhookURL not set. Update in EA inputs.");
        return INIT_FAILED;
    }

    Print("ScalpCity Webhook EA started. Polling: ", WebhookURL);
    EventSetTimer(PollSeconds);
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| DEINIT                                                           |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    EventKillTimer();
}

//+------------------------------------------------------------------+
//| HELPER: Count our positions                                      |
//+------------------------------------------------------------------+
int CountPositionsByDir(int dir)
{
    int n = 0;
    for(int i = PositionsTotal()-1; i >= 0; i--)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
        {
            if(dir == 1  && posInfo.PositionType()==POSITION_TYPE_BUY)  n++;
            if(dir == -1 && posInfo.PositionType()==POSITION_TYPE_SELL) n++;
        }
    }
    return n;
}

double GetMyLots()
{
    double tot = 0;
    for(int i = PositionsTotal()-1; i >= 0; i--)
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
            tot += posInfo.Volume();
    return tot;
}

bool ClosePartialLots(double lots)
{
    lots = NormalizeDouble(lots, 2);
    for(int i = 0; i < PositionsTotal(); i++)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
        {
            double vol = NormalizeDouble(MathMin(lots, posInfo.Volume()), 2);
            if(vol > 0 && trade.PositionClosePartial(posInfo.Ticket(), vol))
            {
                PrintFormat("Partial close %.2f lots ticket #%d", vol, posInfo.Ticket());
                return true;
            }
        }
    }
    return false;
}

void MoveToBreakEven()
{
    for(int i = 0; i < PositionsTotal(); i++)
    {
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
        {
            double ep    = posInfo.PriceOpen();
            double curSL = posInfo.StopLoss();
            double newSL;
            if(posInfo.PositionType()==POSITION_TYPE_BUY)
            {
                newSL = NormalizeDouble(ep + BE_Buffer, _Digits);
                if(curSL < newSL) trade.PositionModify(posInfo.Ticket(), newSL, posInfo.TakeProfit());
            }
            else
            {
                newSL = NormalizeDouble(ep - BE_Buffer, _Digits);
                if(curSL == 0 || curSL > newSL)
                    trade.PositionModify(posInfo.Ticket(), newSL, posInfo.TakeProfit());
            }
        }
    }
    PrintFormat("Break-even applied (buffer=%.2f)", BE_Buffer);
}

//+------------------------------------------------------------------+
//| OPEN TRADE FROM SIGNAL                                           |
//+------------------------------------------------------------------+
bool OpenTrade(int dir, double sigPrice, double sigSL, double sigTP1,
               double sigTP2, double sigTP3, double sigPartial)
{
    // Use signal prices directly from TradingView (already correct levels)
    double slPx, tp3Px;
    double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
    double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);

    if(dir == 1) // BUY
    {
        slPx  = NormalizeDouble(sigSL,  _Digits);
        tp3Px = NormalizeDouble(sigTP3, _Digits);
        tpPartialLvl = sigPartial > 0 ? sigPartial : ask + 4.0;
        tp1Lvl       = sigTP1;
        tp2Lvl       = sigTP2;
        tp3Lvl       = sigTP3;
        slLvl        = sigSL;
    }
    else // SELL
    {
        slPx  = NormalizeDouble(sigSL,  _Digits);
        tp3Px = NormalizeDouble(sigTP3, _Digits);
        tpPartialLvl = sigPartial > 0 ? sigPartial : bid - 4.0;
        tp1Lvl       = sigTP1;
        tp2Lvl       = sigTP2;
        tp3Lvl       = sigTP3;
        slLvl        = sigSL;
    }

    bool res = (dir == 1) ? trade.Buy(LotSize,  _Symbol, 0, slPx, tp3Px, EA_Comment)
                          : trade.Sell(LotSize, _Symbol, 0, slPx, tp3Px, EA_Comment);
    if(res)
    {
        posDir     = dir;
        partialHit = false;
        tp1Hit     = false;
        tp2Hit     = false;
        tp3Hit     = false;
        PrintFormat("[TRADE OPEN] dir=%d SL=%.5f TP1=%.5f TP2=%.5f TP3=%.5f lots=%.2f",
                    dir, slPx, tp1Lvl, tp2Lvl, tp3Px, LotSize);
    }
    else
        PrintFormat("[TRADE FAIL] %d %s", trade.ResultRetcode(), trade.ResultRetcodeDescription());

    return res;
}

//+------------------------------------------------------------------+
//| MANAGE OPEN TRADE — called every tick                            |
//+------------------------------------------------------------------+
void ManageTrade()
{
    if(posDir == 0) return;

    double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
    double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);

    // PARTIAL (~40 pips)
    if(!partialHit)
    {
        bool hit = (posDir==1) ? (bid >= tpPartialLvl) : (ask <= tpPartialLvl);
        if(hit && ClosePartialLots(Partial_Lots))
        {
            partialHit = true;
            PrintFormat("[PARTIAL HIT] closed %.2f lots", Partial_Lots);
        }
    }

    // TP1 — close 0.01, move to BE
    if(partialHit && !tp1Hit)
    {
        bool hit = (posDir==1) ? (bid >= tp1Lvl) : (ask <= tp1Lvl);
        if(hit && ClosePartialLots(TP1_Lots))
        {
            tp1Hit = true;
            MoveToBreakEven();
            PrintFormat("[TP1 HIT] closed %.2f lots, BE applied", TP1_Lots);
        }
    }

    // TP2 — close 0.01
    if(tp1Hit && !tp2Hit)
    {
        bool hit = (posDir==1) ? (bid >= tp2Lvl) : (ask <= tp2Lvl);
        if(hit && ClosePartialLots(TP2_Lots))
        {
            tp2Hit = true;
            PrintFormat("[TP2 HIT] closed %.2f lots", TP2_Lots);
        }
    }

    // TP3 — close 0.01, leave 0.01 running (diamond hands)
    if(tp2Hit && !tp3Hit)
    {
        bool hit = (posDir==1) ? (bid >= tp3Lvl) : (ask <= tp3Lvl);
        if(hit && ClosePartialLots(TP3_Lots))
        {
            tp3Hit = true;
            PrintFormat("[TP3 HIT] closed %.2f lots — 0.01 left running", TP3_Lots);
        }
    }

    // Detect full close
    if(GetMyLots() <= 0.0)
    {
        PrintFormat("[POSITION CLOSED] dir=%d", posDir);
        posDir = 0; partialHit=false; tp1Hit=false; tp2Hit=false; tp3Hit=false;
    }
}

//+------------------------------------------------------------------+
//| PARSE JSON VALUE                                                 |
//+------------------------------------------------------------------+
string JsonGet(string json, string key)
{
    string search = "\"" + key + "\":";
    int pos = StringFind(json, search);
    if(pos < 0) return "";
    pos += StringLen(search);

    // Skip whitespace
    while(pos < StringLen(json) && StringGetCharacter(json,pos)==' ') pos++;

    bool isStr = (StringGetCharacter(json,pos)=='"');
    if(isStr) pos++;

    string val = "";
    while(pos < StringLen(json))
    {
        ushort c = StringGetCharacter(json, pos);
        if(isStr  && c == '"')  break;
        if(!isStr && (c==',' || c=='}' || c==']')) break;
        val += ShortToString(c);
        pos++;
    }
    return val;
}

double JsonGetDouble(string json, string key)
{
    string val = JsonGet(json, key);
    if(val == "" || val == "null") return 0;
    return StringToDouble(val);
}

//+------------------------------------------------------------------+
//| PROCESS SIGNALS FROM SERVER                                      |
//+------------------------------------------------------------------+
void ProcessSignals(string response)
{
    // Find signals array
    int arrStart = StringFind(response, "\"signals\":[");
    if(arrStart < 0) return;
    arrStart += 11;

    int arrEnd = StringFind(response, "]", arrStart);
    if(arrEnd < 0) return;

    string arr = StringSubstr(response, arrStart, arrEnd - arrStart);
    if(StringLen(arr) < 5) return;

    // Split by signal objects
    int pos = 0;
    while(pos < StringLen(arr))
    {
        int objStart = StringFind(arr, "{", pos);
        if(objStart < 0) break;

        // Find matching closing brace
        int depth = 0, objEnd = objStart;
        for(int i = objStart; i < StringLen(arr); i++)
        {
            ushort c = StringGetCharacter(arr, i);
            if(c == '{') depth++;
            if(c == '}') { depth--; if(depth==0){ objEnd=i; break; } }
        }

        string obj = StringSubstr(arr, objStart, objEnd - objStart + 1);
        ProcessOneSignal(obj);
        pos = objEnd + 1;
    }
}

void ProcessOneSignal(string obj)
{
    string action  = JsonGet(obj, "action");
    string ticker  = JsonGet(obj, "ticker");
    double price   = JsonGetDouble(obj, "price");
    double partial = JsonGetDouble(obj, "partial");
    double tp1     = JsonGetDouble(obj, "tp1");
    double tp2     = JsonGetDouble(obj, "tp2");
    double tp3     = JsonGetDouble(obj, "tp3");
    double sl      = JsonGetDouble(obj, "sl");
    string tf      = JsonGet(obj, "tf");
    string event_s = JsonGet(obj, "event");

    PrintFormat("[SIGNAL] action=%s ticker=%s tf=%s price=%.5f", action, ticker, tf, price);

    // ── ENTRY SIGNALS ──
    if(action == "BUY")
    {
        // Check if better price for existing long
        bool longOpen   = (CountPositionsByDir(1) > 0);
        double ask      = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
        bool betterPrice = !longOpen || (ask < GetWorstLongPrice());

        if(betterPrice)
            OpenTrade(1, price, sl, tp1, tp2, tp3, partial);
        else
            Print("[BUY skipped] New entry worse than existing long");
    }
    else if(action == "SELL")
    {
        bool shortOpen  = (CountPositionsByDir(-1) > 0);
        double bid      = SymbolInfoDouble(_Symbol, SYMBOL_BID);
        bool betterPrice = !shortOpen || (bid > GetWorstShortPrice());

        if(betterPrice)
            OpenTrade(-1, price, sl, tp1, tp2, tp3, partial);
        else
            Print("[SELL skipped] New entry worse than existing short");
    }
    // ── MANAGEMENT SIGNALS (from Telegram TP/SL hits) ──
    // These are informational — EA manages its own levels
    // but we log them for reference
    else if(action == "PARTIAL") Print("[INFO] Partial hit confirmed by TradingView");
    else if(action == "TP1")     Print("[INFO] TP1 hit confirmed by TradingView");
    else if(action == "TP2")     Print("[INFO] TP2 hit confirmed by TradingView");
    else if(action == "TP3")     Print("[INFO] TP3 hit confirmed by TradingView");
    else if(action == "SL")      Print("[INFO] SL hit confirmed by TradingView");
}

double GetWorstLongPrice()
{
    double worst = 0;
    for(int i=PositionsTotal()-1;i>=0;i--)
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
            if(posInfo.PositionType()==POSITION_TYPE_BUY)
                if(posInfo.PriceOpen() > worst) worst = posInfo.PriceOpen();
    return worst;
}

double GetWorstShortPrice()
{
    double worst = DBL_MAX;
    for(int i=PositionsTotal()-1;i>=0;i--)
        if(posInfo.SelectByIndex(i) && posInfo.Symbol()==_Symbol && posInfo.Magic()==Magic)
            if(posInfo.PositionType()==POSITION_TYPE_SELL)
                if(posInfo.PriceOpen() < worst) worst = posInfo.PriceOpen();
    return worst;
}

//+------------------------------------------------------------------+
//| TIMER — polls webhook server                                     |
//+------------------------------------------------------------------+
void OnTimer()
{
    // Poll webhook server for new signals
    string headers = "Content-Type: application/json\r\n";
    string result  = "";
    char   post[], response[];

    int res = WebRequest("GET", WebhookURL, headers, 5000, post, response, headers);

    if(res == -1)
    {
        int err = GetLastError();
        if(err == 4060)
            Print("ERROR: Add '", WebhookURL, "' to MT5 allowed URLs (Tools → Options → Expert Advisors)");
        else
            PrintFormat("Poll ERROR: %d", err);
        return;
    }

    result = CharArrayToString(response);
    if(LogAll) PrintFormat("[POLL] %s", result);

    // Check if there are signals
    if(StringFind(result, "\"count\":0") >= 0) return; // no signals
    if(StringFind(result, "\"signals\":[]") >= 0) return;

    ProcessSignals(result);
}

//+------------------------------------------------------------------+
//| ON TICK — manage open trades                                     |
//+------------------------------------------------------------------+
void OnTick()
{
    ManageTrade();
}
//+------------------------------------------------------------------+
