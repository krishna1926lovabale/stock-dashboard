import streamlit as st
import asyncio
import pandas as pd
from kiteconnect import KiteConnect
from telethon import TelegramClient
import pytz
import datetime
import re
import io
import time  # Added to fix sleep error

# --- USER CONFIG ---
api_id = 28823723
api_hash = 'da6ee58c8bdab55b4fb0f627b6686ee5'
channel_username = 'nirmalbangofficial'
tz = pytz.timezone("Asia/Kolkata")

KITE_API_KEY = 't35fuxndouudlvyu'
ACCESS_TOKEN_PATH = r"C:\Users\krish\Downloads\access_token.txt"

NSE_CSV = r'C:\Users\krish\Downloads\nse_symbols.csv'  # update if needed

# --- Helper functions (same as before, pasted here) ---

def load_access_token(path=ACCESS_TOKEN_PATH):
    try:
        with open(path, 'r') as f:
            return f.read().strip()
    except:
        return None

def init_kite_client():
    kite = KiteConnect(api_key=KITE_API_KEY)
    token = load_access_token()
    if not token:
        raise Exception("No valid Kite access token found")
    kite.set_access_token(token)
    return kite

def load_nse_symbols(csv_path=NSE_CSV):
    df = pd.read_csv(csv_path)
    name_cols = [c for c in df.columns if 'company' in c.lower() or 'name' in c.lower()]
    symbol_col = [c for c in df.columns if 'symbol' in c.lower()]
    if not name_cols or not symbol_col:
        st.error("NSE symbols CSV missing required columns")
        return pd.DataFrame()
    name_col = name_cols[0]
    sym_col = symbol_col[0]
    df['NAME_CLEAN'] = df[name_col].str.replace('&', 'and', regex=False)
    df['NAME_CLEAN'] = df['NAME_CLEAN'].str.replace('[^a-zA-Z0-9 ]', '', regex=True).str.lower().str.strip()
    df['SYMBOL'] = df[sym_col].str.strip()
    return df[['SYMBOL', 'NAME_CLEAN']]

def clean_name(name):
    name = name.replace('&', 'and')
    name = re.sub(r'[^a-zA-Z0-9 ]', '', name)
    return name.strip().lower()

def match_to_nse_symbol(stock_name, nse_df):
    stock_name_clean = clean_name(stock_name)
    row = nse_df[nse_df['NAME_CLEAN'] == stock_name_clean]
    if not row.empty:
        return row['SYMBOL'].values[0]
    for idx, cname in enumerate(nse_df['NAME_CLEAN']):
        if stock_name_clean in cname or cname in stock_name_clean:
            return nse_df.iloc[idx]['SYMBOL']
    for idx, cname in enumerate(nse_df['NAME_CLEAN']):
        if stock_name_clean.split()[0] in cname:
            return nse_df.iloc[idx]['SYMBOL']
    return ''

def extract_stocks_from_message(msg):
    pattern = r"\*(.*?)\* *\| *\*CMP\* *Rs\.? *([0-9]+)"
    stocks = []
    for m in re.finditer(pattern, msg):
        tg_name = m.group(1).strip()
        tg_cmp = m.group(2).strip()
        stocks.append((tg_name, tg_cmp))
    return stocks

def calc_pivots(open_, high_, low_):
    try:
        open_, high_, low_ = float(open_), float(high_), float(low_)
        pivot = (high_ + low_ + open_) / 3
        r1 = 2 * pivot - low_
        s1 = 2 * pivot - high_
        return int(round(r1)), int(round(s1))
    except:
        return '', ''

# --- Async Telegram fetch ---
async def fetch_signals(date_input):
    client = TelegramClient('anon', api_id, api_hash)
    await client.start()
    nse_df = load_nse_symbols()
    if nse_df.empty:
        st.error("NSE symbols data missing")
        return []

    all_records = []
    unmapped = []

    if date_input == "":
        target_date = datetime.datetime.now(tz)
    else:
        try:
            if '-' in date_input:
                target_date = datetime.datetime.strptime(date_input, '%Y-%m-%d')
            else:
                target_date = datetime.datetime.strptime(date_input, '%d%m%y')
            target_date = tz.localize(target_date)
        except:
            st.error("Invalid date format for fetching signals")
            return []

    channel_entity = await client.get_entity(channel_username)
    async for msg in client.iter_messages(channel_entity, limit=1000):
        if msg.text is None:
            continue
        msg_dt = msg.date.astimezone(tz)
        if msg_dt.date() != target_date.date():
            continue
        stocks = extract_stocks_from_message(msg.text)
        if not stocks:
            continue
        for tg_name, tg_cmp in stocks:
            nse_symbol = match_to_nse_symbol(tg_name, nse_df)
            if not nse_symbol:
                unmapped.append(tg_name)
                continue
            record = {
                'Telegram Stock Name': tg_name,
                'Telegram CMP': tg_cmp,
                'NSE Symbol': nse_symbol,
                'Date': msg_dt.strftime('%d-%m-%Y'),
                'Time': msg_dt.strftime('%H:%M'),
            }
            all_records.append(record)
    await client.disconnect()
    return all_records

# --- Kite live update ---
def update_live_data(df, kite):
    symbols = df['NSE Symbol'].unique()
    kite_symbols = [f"NSE:{sym}" for sym in symbols if sym]

    try:
        quotes = kite.quote(kite_symbols)
    except Exception as e:
        st.error(f"Error fetching live quotes: {e}")
        return df

    for i, row in df.iterrows():
        sym = row['NSE Symbol']
        if not sym:
            continue
        kite_sym = f"NSE:{sym}"
        if kite_sym not in quotes:
            continue
        q = quotes[kite_sym]
        ohlc = q.get('ohlc', {})
        last_price = q.get('last_price', None)

        df.at[i, 'Real-time CMP'] = last_price if last_price is not None else None
        df.at[i, "Today's Open"] = ohlc.get('open', None)
        df.at[i, "Today's High"] = ohlc.get('high', None)
        df.at[i, "Today's Low"] = ohlc.get('low', None)

        try:
            r1, s1 = calc_pivots(
                df.at[i, "Today's Open"],
                df.at[i, "Today's High"],
                df.at[i, "Today's Low"],
            )
            df.at[i, "Target (R1)"] = r1
            df.at[i, "Stop Loss (S1)"] = s1
        except:
            df.at[i, "Target (R1)"] = None
            df.at[i, "Stop Loss (S1)"] = None
    return df

# --- Excel export helper ---
def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()

# --- Main Streamlit app ---

st.set_page_config(page_title="Uber Telegram Stock Signals Dashboard", layout="wide")

st.markdown("""
<style>
body {
    background-color: #0e1117;
    color: white;
}
h1 {
    color: #00d8ff;
}
.stButton>button {
    background-color: #00d8ff;
    color: black;
    font-weight: bold;
}
.stDataFrame>div {
    border: 1px solid #00d8ff;
    border-radius: 5px;
}
</style>
""", unsafe_allow_html=True)

st.title("🚀 Uber Telegram Stock Signals Dashboard")

with st.sidebar:
    st.header("Settings")
    selected_date = st.date_input("Select Date to Fetch", value=datetime.datetime.now(tz).date())
    refresh_toggle = st.checkbox("Auto-refresh every 60 seconds", value=True)

placeholder_status = st.empty()
placeholder_table = st.empty()

def load_and_display_data(date_str):
    placeholder_status.info(f"⏳ Fetching Telegram signals for {date_str}...")
    try:
        all_records = asyncio.run(fetch_signals(date_str))
        if not all_records:
            placeholder_status.warning(f"No signals found for {date_str}.")
            placeholder_table.empty()
            return None

        df = pd.DataFrame(all_records)
        kite = init_kite_client()
        placeholder_status.info("⏳ Fetching live market data from Kite Connect...")
        df = update_live_data(df, kite)
        placeholder_status.success(f"✅ Data loaded for {date_str} ({len(df)} records).")
        placeholder_table.dataframe(df)
        return df
    except Exception as e:
        placeholder_status.error(f"Error fetching data: {e}")
        return None

# Initial load
df = load_and_display_data(selected_date.strftime('%Y-%m-%d'))

# Excel download button if data present
if df is not None and not df.empty:
    excel_data = to_excel_bytes(df)
    st.download_button(
        label="💾 Download data as Excel",
        data=excel_data,
        file_name=f"telegram_stock_signals_{selected_date}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

# Auto-refresh loop
if refresh_toggle:
    st.info("Auto-refresh enabled. Reloading every 60 seconds...")
    count = 0
    while True:
        time.sleep(60)
        df = load_and_display_data(selected_date.strftime('%Y-%m-%d'))
        if df is None:
            break
        count += 1
        if count >= 60:  # safety max 1 hour refresh
            break
