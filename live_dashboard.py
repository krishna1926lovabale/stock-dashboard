import streamlit as st
import asyncio
import pandas as pd
import yfinance as yf
from telethon import TelegramClient
import pytz
import datetime
import re
import io
import time

# --- USER CONFIG ---
api_id = 28823723
api_hash = 'da6ee58c8bdab55b4fb0f627b6686ee5'
channel_username = 'nirmalbangofficial'
tz = pytz.timezone("Asia/Kolkata")
NSE_CSV = 'nse_symbols.csv'  # Must be uploaded to your project

# --- Helper Functions ---


def load_nse_symbols(csv_path=NSE_CSV):
    df = pd.read_csv(csv_path)
    name_cols = [
        c for c in df.columns if 'company' in c.lower() or 'name' in c.lower()
    ]
    symbol_col = [c for c in df.columns if 'symbol' in c.lower()]
    if not name_cols or not symbol_col:
        st.error("NSE symbols CSV missing required columns")
        return pd.DataFrame()
    name_col = name_cols[0]
    sym_col = symbol_col[0]
    df['NAME_CLEAN'] = df[name_col].str.replace('&', 'and', regex=False)
    df['NAME_CLEAN'] = df['NAME_CLEAN'].str.replace(
        '[^a-zA-Z0-9 ]', '', regex=True).str.lower().str.strip()
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


def get_yf_data(nse_symbol):
    try:
        yf_symbol = nse_symbol.upper() + ".NS"
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(period="1d")
        info = ticker.info
        if hist.empty:
            return None
        latest = hist.iloc[-1]
        return {
            'Real-time CMP': round(latest['Close']),
            "Today's Open": round(latest['Open']),
            "Today's High": round(latest['High']),
            "Today's Low": round(latest['Low']),
            "52W High": round(info.get('fiftyTwoWeekHigh', 0)),
            "52W Low": round(info.get('fiftyTwoWeekLow', 0))
        }
    except Exception as e:
        st.warning(f"Yahoo Finance error for {nse_symbol}: {e}")
        return None


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
                target_date = datetime.datetime.strptime(
                    date_input, '%Y-%m-%d')
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


def to_excel_bytes(df):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False)
    return output.getvalue()


# --- Streamlit App UI ---

st.set_page_config(page_title="Uber Telegram Stock Signals Dashboard",
                   layout="wide")

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
""",
            unsafe_allow_html=True)

st.title("🚀 Uber Telegram Stock Signals Dashboard")

with st.sidebar:
    st.header("Settings")
    selected_date = st.date_input("Select Date to Fetch",
                                  value=datetime.datetime.now(tz).date())
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

        placeholder_status.info("⏳ Fetching market data from Yahoo Finance...")
        for i, row in df.iterrows():
            symbol = row['NSE Symbol']
            data = get_yf_data(symbol)
            if data:
                df.at[i, 'Real-time CMP'] = data['Real-time CMP']
                df.at[i, "Today's Open"] = data["Today's Open"]
                df.at[i, "Today's High"] = data["Today's High"]
                df.at[i, "Today's Low"] = data["Today's Low"]
                df.at[i, "52W High"] = data["52W High"]
                df.at[i, "52W Low"] = data["52W Low"]
                r1, s1 = calc_pivots(data["Today's Open"],
                                     data["Today's High"], data["Today's Low"])
                df.at[i, "Target (R1)"] = r1
                df.at[i, "Stop Loss (S1)"] = s1

        placeholder_status.success(
            f"✅ Data loaded for {date_str} ({len(df)} records).")
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
        mime=
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
        if count >= 60:
            break
