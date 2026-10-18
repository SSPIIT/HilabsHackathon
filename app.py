"""
PTD provider rates - Executive and Analyst views (Streamlit).

Reads the outputs the pipeline already produced; changes nothing.
Run from the project root:
    pip install "streamlit>=1.40" altair
    streamlit run app.py
"""

import re
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

OUT = Path("data/output")
INT = Path("data/interim")
VERIFIED_CENSUS = {"census_exact", "census_non_exact"}

# ---- look: white page, navy hero, blue numbers, orange accent -----------------------
NAVY, NAVY_2 = "#0b1c4d", "#14307e"
BLUE, BLUE_LIGHT = "#2563eb", "#dbe6fd"
INK, MUTED, LINE = "#0b1f4d", "#5b6b8c", "#e6ecf7"
ORANGE = "#f47b20"
STATUS = {"high": "#2e7d32", "medium": "#c98a00", "low": "#c62828"}

st.set_page_config(page_title="Provider Rate Transparency", page_icon="📍", layout="wide")

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap');
html, body, [class*="css"], .stApp, .stMarkdown, button, input {{ font-family: 'Plus Jakarta Sans', sans-serif; }}
.stApp {{ background: #ffffff; color: {INK}; }}
.block-container {{ padding-top: 3.6rem; max-width: 1280px; }}
h1, h2, h3, h4 {{ color: {INK}; font-weight: 700; letter-spacing: -0.01em; }}

.hero {{ background: linear-gradient(120deg, {NAVY} 0%, {NAVY_2} 100%); border-radius: 0 0 36px 36px;
        padding: 40px 48px 44px; color: #fff; display: flex; justify-content: space-between;
        align-items: center; gap: 24px; margin-bottom: 28px; }}
.hero .eyebrow {{ font-size: 15px; font-weight: 600; opacity: .9; margin-bottom: 10px; }}
.hero h1 {{ color: #fff; font-size: 40px; line-height: 1.12; font-weight: 800; margin: 0 0 12px; }}
.hero p, .stMarkdown .hero p {{ color: #c9d6f7 !important; font-size: 16px; margin: 0; max-width: 640px; }}
.hero .eyebrow, .hero h1 {{ color: #ffffff !important; }}
.pill {{ display: inline-block; background: linear-gradient(90deg, #2ec5f0, #6a5cff); color: #fff;
        border-radius: 999px; padding: 6px 16px; font-size: 13px; font-weight: 700; margin-top: 18px; }}

.card {{ background: #fff; border: 1px solid {LINE}; border-radius: 22px; padding: 26px 28px;
        box-shadow: 0 6px 24px rgba(11, 28, 77, .06); min-height: 230px; }}
.card .big {{ color: {BLUE}; font-size: 46px; font-weight: 800; line-height: 1; margin-bottom: 14px; }}
.card .label {{ color: {INK}; font-size: 16px; line-height: 1.45; }}
.card .src {{ color: {MUTED}; font-size: 13px; margin-top: 18px; }}
.section {{ font-size: 24px; font-weight: 800; color: {INK}; margin: 34px 0 6px; }}
.sub {{ color: {MUTED}; font-size: 14px; margin-bottom: 14px; }}

.funnel {{ display: flex; gap: 8px; flex-wrap: wrap; }}
.step {{ flex: 1 1 140px; background: #f4f7ff; border-radius: 16px; padding: 16px 16px; border: 1px solid {LINE}; }}
.step .n {{ color: {BLUE}; font-size: 24px; font-weight: 800; }}
.step .t {{ color: {MUTED}; font-size: 13px; margin-top: 4px; }}
.legend span {{ display: inline-block; margin-right: 18px; font-size: 13px; color: {MUTED}; }}
.legend i {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }}

.stTabs [data-baseweb="tab-list"] {{ gap: 6px; }}
.stTabs [data-baseweb="tab"], .stTabs [role="tab"], .stTabs [role="tab"] p {{ font-weight: 700; color: {INK} !important; opacity: 1 !important; }}
.stTabs [aria-selected="true"], .stTabs [aria-selected="true"] p {{ color: {BLUE} !important; }}
[data-testid="stMetricValue"] {{ color: {BLUE}; font-weight: 800; font-size: 28px; }}
.stDownloadButton button {{ background: {ORANGE}; color: #fff; border: 0; border-radius: 999px;
        font-weight: 700; padding: 8px 22px; }}

/* keep text readable on the white page even if Streamlit runs in dark theme */
[data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] *, label, label p,
[data-testid="stMetricLabel"], [data-testid="stMetricLabel"] *,
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] *,
.stMarkdown p, .stMarkdown li, .stMarkdown strong, .stMarkdown h3,
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary * {{ color: {INK} !important; }}
[data-testid="stCaptionContainer"] * {{ color: {MUTED} !important; }}
.stTextInput input, .stNumberInput input, [data-baseweb="select"] > div, [data-baseweb="input"] {{
    background: #f4f7ff !important; color: {INK} !important; border-color: {LINE} !important; }}
.stTextInput input::placeholder {{ color: {MUTED} !important; }}
[data-baseweb="select"] *, [data-baseweb="tag"] * {{ color: {INK} !important; }}
.stSelectbox div[data-baseweb="select"] > div, .stMultiSelect div[data-baseweb="select"] > div {{
    background-color: #f4f7ff !important; border: 1px solid {LINE} !important; }}
[data-baseweb="popover"] li, [data-baseweb="menu"] li {{ background: #ffffff !important; color: {INK} !important; }}
header[data-testid="stHeader"] {{ background: #ffffff !important; }}
header[data-testid="stHeader"] * {{ color: {INK} !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ background-color: {BLUE} !important; }}
</style>
""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- data
@st.cache_data(show_spinner=False)
def load(path):
    p = Path(path)
    if not p.exists():
        return None
    return pd.read_csv(p, dtype=str, keep_default_na=False, low_memory=False)


@st.cache_data(show_spinner=False)
def read_text(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else None


def first(*paths):
    for p in paths:
        d = load(p)
        if d is not None:
            return d.copy()
    return None


def tin9(s):
    d = re.sub(r"\D", "", str(s or ""))
    return d.zfill(9) if d else ""


def num(s):
    return pd.to_numeric(s, errors="coerce")


def card(big, label, src=""):
    s = f'<div class="src">{src}</div>' if src else ""
    return f'<div class="card"><div class="big">{big}</div><div class="label">{label}</div>{s}</div>'


def chart_style(ch):
    """Charts sit on a navy panel with white text, whatever the Streamlit theme."""
    return (ch.configure(background=NAVY, padding={"left": 12, "right": 28, "top": 10, "bottom": 10})
              .configure_view(strokeWidth=0)
              .configure_axis(labelColor="#ffffff", titleColor="#ffffff", gridColor="#2a3f86",
                              domainColor="#5b6fae", tickColor="#5b6fae"))


def hbar(series, value_title, color="#4f8bff", pct=False, height=None):
    """Horizontal bars: one hue, thin rounded bars, value labels, hover tooltip."""
    d = series.rename_axis("item").reset_index(name="value")
    total = d["value"].sum()
    d["share"] = d["value"] / total if total else 0
    fmt = ".1%" if pct else ",.0f"
    base = alt.Chart(d).encode(
        y=alt.Y("item:N", sort=None, title=None, axis=alt.Axis(labelColor="#ffffff", labelFontSize=12,
                                                               labelLimit=260, ticks=False, domain=False)),
        x=alt.X("value:Q", title=None, axis=None),
        tooltip=[alt.Tooltip("item:N", title="Item"), alt.Tooltip("value:Q", title=value_title, format=fmt)]
                + ([] if pct else [alt.Tooltip("share:Q", title="Share", format=".1%")]),
    )
    bars = base.mark_bar(color=color, cornerRadiusEnd=4, height=16)
    labels = base.mark_text(align="left", dx=6, color="#ffffff", fontSize=12).encode(
        text=alt.Text("value:Q", format=fmt))
    h = height or max(90, 34 * len(d))
    return chart_style((bars + labels).properties(height=h))


c6 = load(OUT / "c6_provider_rate_file.csv")
c5w = load(OUT / "c5_npi_tin_address.csv")
c5l = first(INT / "c5_npi_tin_address_long.csv")
c4 = first(OUT / "c4_rates.csv", INT / "c4_rates.csv")
nppes = first(INT / "c3_nppes_npi.csv", INT / "c5_nppes_npi.csv")
c1 = load(INT / "c1_plans.csv")
c2ca = load(INT / "c2_plans_ca.csv")
ppo = first(OUT / "c2_ppo_plans_ca.csv", INT / "c2_ppo_plans_ca.csv")
prov = first(INT / "c3_providers.csv", INT / "c5_providers.csv")

if c6 is None or c5w is None or c4 is None:
    st.error("Missing pipeline outputs. Expected data/output/c6_provider_rate_file.csv, "
             "data/output/c5_npi_tin_address.csv and data/output/c4_rates.csv - run `dvc repro` first.")
    st.stop()

c6, c5w = c6.copy(), c5w.copy()
c6["TIN"] = c6["TIN"].map(tin9)
for df in (c5w, c5l, c4, prov):
    if df is not None and "tin" in df.columns:
        df["tin"] = df["tin"].map(tin9)

names = dict(zip(c5w["npi"], c5w["business_name"]))
if nppes is not None and "name" in nppes.columns:
    names.update({n: nm for n, nm in zip(nppes["npi"], nppes["name"]) if nm})
etype = dict(zip(nppes["npi"], nppes["entity_type"])) if nppes is not None else {}
specialty = c6.drop_duplicates("NPI").set_index("NPI")["Specialty"].to_dict()

if c5l is not None:
    n_src = num(c5l["n_sources"]).fillna(0)
    c5l["verified"] = (((n_src >= 2) & c5l["validity_note"].isin(VERIFIED_CENSUS))
                       | (c5l["validity_note"] == "manual"))
    c5l["is_primary"] = c5l["rank"].astype(str).str.replace(".0", "", regex=False) == "1"

tab_exec, tab_an = st.tabs(["Executive view", "Analyst view"])

# ============================================================================ EXECUTIVE
with tab_exec:
    st.markdown(f"""
    <div class="hero">
      <div>
        <div class="eyebrow">Team DeQu · Data Pipeline Curation for Provider Rates</div>
        <h1>Centene California commercial PPO:<br>who is paid what, and where</h1>
        <p>Every provider in the Health Net of California in-network file, linked to a scored practice
           address and one explained rate per service.</p>
        <span class="pill">NPI · TIN · Address · Billing code</span>
      </div>
      <svg width="230" height="170" viewBox="0 0 230 170" aria-hidden="true">
        <rect x="6" y="14" width="70" height="88" rx="12" fill="#fff"/>
        <g stroke="{BLUE}" stroke-width="5" stroke-linecap="round">
          <line x1="20" y1="36" x2="50" y2="36"/><line x1="20" y1="52" x2="62" y2="52"/>
          <line x1="20" y1="68" x2="62" y2="68"/><line x1="20" y1="84" x2="44" y2="84"/></g>
        <path d="M76 58 H112" stroke="#fff" stroke-width="4" fill="none"/>
        <circle cx="140" cy="88" r="38" fill="#fff"/>
        <circle cx="140" cy="88" r="14" fill="none" stroke="{BLUE}" stroke-width="6"/>
        <path d="M178 88 H196" stroke="#fff" stroke-width="4"/>
        <g fill="#fff"><circle cx="212" cy="40" r="14"/><circle cx="212" cy="88" r="14"/><circle cx="212" cy="136" r="14"/></g>
        <g stroke="{BLUE}" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round">
          <path d="M205 40 l5 5 l9 -10"/><path d="M205 88 l5 5 l9 -10"/><path d="M205 136 l5 5 l9 -10"/></g>
      </svg>
    </div>""", unsafe_allow_html=True)

    # ---- headline cards
    pairs_total = len(c6[["NPI", "TIN"]].drop_duplicates())
    linked = (c6.groupby(["NPI", "TIN"])["Address"].apply(lambda s: (s != "").any())).mean()
    v_npi = c5l.groupby("npi")["verified"].any().mean() if c5l is not None else float("nan")
    single = c4["kept_rule"].isin(["single", "duplicates"]).mean() if "kept_rule" in c4 else float("nan")
    n_files = None
    if ppo is not None:
        url_col = next((c for c in ppo.columns if "url" in c.lower()), None)
        if url_col:
            n_files = ppo[url_col].str.replace(r"\?.*$", "", regex=True).nunique()
    k = st.columns(4)
    k[0].markdown(card(f"{linked:.0%}", f"of {pairs_total:,} in-network providers (NPI + TIN) linked to a "
                       "practice address", "C5 address mapping"), unsafe_allow_html=True)
    k[1].markdown(card(f"{v_npi:.0%}", "of NPIs have a <b>verified</b> address: two independent sources "
                       "agree and the Census geocoder finds it", "C5 · coverage report"), unsafe_allow_html=True)
    k[2].markdown(card(f"{single:.0%}", f"of {len(c4):,} provider-service rates had a single price in the "
                       "file; the rest were resolved by stated rules", "C4 rate extraction"), unsafe_allow_html=True)
    k[3].markdown(card(f"{n_files if n_files is not None else '-'}",
                       f"rate file covers all {len(ppo):,} PPO group plan rows" if ppo is not None else
                       "rate file in scope", "C1-C2 plan mapping"), unsafe_allow_html=True)

    # ---- funnel
    st.markdown('<div class="section">From Centene\'s index to the final file</div>', unsafe_allow_html=True)
    steps = []
    if c1 is not None:
        steps.append((len(c1), "Centene index rows"))
    if c2ca is not None:
        steps.append((len(c2ca), "California rows"))
    if ppo is not None:
        steps.append((len(ppo), "PPO group plan rows"))
    if n_files is not None:
        steps.append((n_files, "rate file(s)"))
    if prov is not None:
        steps.append((len(prov[["npi", "tin"]].drop_duplicates()), "providers (NPI+TIN) in file"))
    steps += [(len(c4), "rates kept, one per service"), (len(c6), "final rows NPI·TIN·address·code")]
    st.markdown('<div class="funnel">' + "".join(
        f'<div class="step"><div class="n">{n:,}</div><div class="t">{t}</div></div>' for n, t in steps)
        + "</div>", unsafe_allow_html=True)

    # ---- address quality
    if c5l is not None:
        st.markdown('<div class="section">How sure are we of each address?</div>'
                    '<div class="sub">Used 4 sources to map NPI to address</div>',
                    unsafe_allow_html=True)
        left, right = st.columns(2)
        with left:
            st.markdown("**Primary address confidence**")
            conf = c5l[c5l["is_primary"]]["confidence"].value_counts().reindex(["high", "medium", "low"]).fillna(0)
            d = conf.rename_axis("item").reset_index(name="value")
            ch = alt.Chart(d).mark_bar(cornerRadiusEnd=4, height=18).encode(
                y=alt.Y("item:N", sort=["high", "medium", "low"], title=None,
                        axis=alt.Axis(labelColor="#ffffff", ticks=False, domain=False)),
                x=alt.X("value:Q", axis=None),
                color=alt.Color("item:N", scale=alt.Scale(domain=list(STATUS), range=list(STATUS.values())),
                                legend=None),
                tooltip=[alt.Tooltip("item:N", title="Confidence"), alt.Tooltip("value:Q", title="Providers", format=",")])
            lab = ch.mark_text(align="left", dx=6).encode(text=alt.Text("value:Q", format=","), color=alt.value("#ffffff"))
            st.altair_chart(chart_style((ch + lab).properties(height=130)), theme=None)
        with right:
            st.markdown("**Share of NPIs with a verified address, by provider type**")
            et = c5l.assign(t=c5l["npi"].map(etype).map({"1": "Type 1 · individual",
                                                         "2": "Type 2 · organization"}).fillna("Manual lookup"))
            by = et.groupby(["t", "npi"])["verified"].any().groupby("t").mean().sort_values(ascending=False)
            st.altair_chart(hbar(by, "Verified", pct=True, height=130), theme=None)
        left, right = st.columns(2)
        with left:
            st.markdown("**Why an address is not yet verified**")
            nv = c5l[~c5l["verified"]]
            reason = pd.Series("2+ sources, Census could not place it", index=nv.index)
            reason[num(nv["n_sources"]).fillna(0) < 2] = "only one source so far"
            st.altair_chart(hbar(reason.value_counts(), "Addresses", color=ORANGE, height=100), theme=None)
        with right:
            st.markdown("**Which sources back the primary address**")
            src = (c5l[c5l["is_primary"]]["sources"].str.split(" | ", regex=False).explode()
                   .replace({"nppes_location": "NPPES", "nppes_mailing": "NPPES", "nppes_practice_location": "NPPES",
                             "medi-cal-ffs-provider-listing": "Medi-Cal FFS listing",
                             "ca_facility_listing": "CA licensed facilities",
                             "cms_doctors_clinicians": "CMS Doctors & Clinicians"}))
            src = src.where(~src.str.startswith("manual"), "Manual lookup")
            src = src.groupby(level=0).apply(lambda s: s.drop_duplicates()).value_counts().head(6)
            st.altair_chart(hbar(src, "Primary addresses", height=40 * len(src)), theme=None)


    # ---- rates
    st.markdown('<div class="section">Rates</div><div class="sub">One rate per provider and service; '
                'every choice carries the rule that made it.</div>', unsafe_allow_html=True)
    left, right = st.columns(2)
    with left:
        st.markdown("**How each kept rate was decided**")
        lab = {"single": "single price in file", "duplicates": "same price repeated", "most_groups":
               "listed by most provider groups", "tie_middle": "tie, middle value", "rate_type":
               "agreed price over estimate", "expired_removed": "expired price removed", "fake_removed":
               "placeholder price removed"}
        st.altair_chart(hbar(c4["kept_rule"].map(lambda r: lab.get(r, r)).value_counts(), "Rates"), theme=None)
    with right:
        st.markdown("**Top specialties by number of providers**")
        sp = c6.drop_duplicates("NPI")["Specialty"].replace("", "unknown").value_counts().head(8)
        st.altair_chart(hbar(sp, "Providers"), theme=None)

    for title, path in (("Coverage report (full text)", OUT / "coverage_report.txt"),):
        txt = read_text(path)
        if txt:
            with st.expander(title):
                st.code(txt, language=None)

# ============================================================================ ANALYST
with tab_an:
    st.markdown('<div class="section" style="margin-top:8px">Analyst workspace</div>'
                '<div class="sub">Search any provider or code, trace every number to its source and rule, '
                'and export what you filter.</div>', unsafe_allow_html=True)
    a1, a2, a3, a4 = st.tabs(["Provider lookup", "Code lookup", "Browse & download", "Evidence"])

    with a1:
        q = st.text_input("Search by NPI, TIN or provider name", placeholder="e.g. 1003311051, 050528655 or HODGKIN")
        hits = c5w
        if q:
            ql = q.strip().upper()
            digits = re.sub(r"\D", "", ql)
            mask = c5w["business_name"].str.upper().str.contains(re.escape(ql), na=False)
            if len(digits) >= 3 and not re.search(r"[A-Z]", ql):     # an NPI / TIN, not a name
                mask |= c5w["npi"].str.contains(digits) | c5w["tin"].str.contains(digits)
            hits = c5w[mask]
        st.caption(f"{len(hits):,} matching NPI + TIN")
        if len(hits):
            opts = (hits["npi"] + " · " + hits["tin"] + " · " + hits["business_name"]).tolist()[:500]
            pick = st.selectbox("Provider", opts)
            npi, tin = pick.split(" · ")[:2]
            st.markdown(f"### {names.get(npi, '')}")
            c = st.columns(4)
            c[0].metric("NPI", npi)
            c[1].metric("TIN", tin)
            c[2].metric("Type", {"1": "Individual", "2": "Organization"}.get(etype.get(npi, ""), "unknown"))
            c[3].metric("Specialty", specialty.get(npi, "") or "unknown")
            if c5l is not None:
                st.markdown("**Addresses (C5)**")
                ad = c5l[(c5l["npi"] == npi) & (c5l["tin"] == tin)]
                show = [x for x in ["rank", "address_1", "address_2", "city", "zip", "score", "confidence",
                                    "verified", "sources", "n_sources", "validity_note", "flags"] if x in ad.columns]
                st.dataframe(ad[show], hide_index=True)
            st.markdown("**Rates (C4): one per service, with the rule that kept it**")
            rr = c4[(c4["npi"] == npi) & (c4["tin"] == tin)]
            show = [x for x in ["billing_code_type", "billing_code", "code_detail", "description", "modifiers",
                                "billing_class", "pos_group", "negotiated_rate", "kept_rule",
                                "n_distinct_amounts", "min_rate", "max_rate", "flag"] if x in rr.columns]
            st.dataframe(rr[show], hide_index=True)

    with a2:
        codes = sorted(c6["Billing Code"].unique(), key=lambda x: (len(x), x))
        code = st.selectbox("Billing code", codes, index=codes.index("99213") if "99213" in codes else 0)
        per = c6[c6["Billing Code"] == code].drop_duplicates(["NPI", "TIN"]).copy()
        per["Rate_num"] = num(per["Rate"])
        sp = st.multiselect("Specialty", sorted(per["Specialty"].unique()))
        if sp:
            per = per[per["Specialty"].isin(sp)]
        med = per["Rate_num"].median()
        c = st.columns(5)
        c[0].metric("Providers", f"{per['NPI'].nunique():,}")
        c[1].metric("Min", f"${per['Rate_num'].min():,.2f}")
        c[2].metric("Median", f"${med:,.2f}")
        c[3].metric("Max", f"${per['Rate_num'].max():,.2f}")
        c[4].metric("Max / median", f"{per['Rate_num'].max() / med:.1f}x" if med else "-")
        if per["Rate_num"].notna().sum() > 1:
            st.markdown("**Rates across providers**")
            hist = alt.Chart(per.dropna(subset=["Rate_num"])).mark_bar(color="#4f8bff", cornerRadiusEnd=4).encode(
                x=alt.X("Rate_num:Q", bin=alt.Bin(maxbins=30), title="Negotiated rate ($)"),
                y=alt.Y("count():Q", title="Providers"),
                tooltip=[alt.Tooltip("count():Q", title="Providers")])
            rule = alt.Chart(pd.DataFrame({"m": [med]})).mark_rule(color=ORANGE, strokeDash=[4, 4]).encode(
                x="m:Q", tooltip=[alt.Tooltip("m:Q", title="Median", format="$,.2f")])
            st.altair_chart(chart_style((hist + rule).properties(height=220)), theme=None)
            st.caption("dashed orange line = median")
        per = per.assign(Provider=per["NPI"].map(names), vs_median=(per["Rate_num"] / med).round(2) if med else None)
        st.dataframe(per.sort_values("Rate_num")[["NPI", "TIN", "Provider", "Specialty", "Address", "Rate",
                                                  "vs_median"]], hide_index=True)

    with a3:
        f = st.columns(4)
        sp = f[0].multiselect("Specialty", sorted(c6["Specialty"].unique()), key="b_sp")
        cd = f[1].text_input("Billing code starts with", key="b_cd")
        city = f[2].text_input("City contains", key="b_city")
        rmin = f[3].number_input("Min rate ($)", 0.0, value=0.0)
        view = c6
        if sp:
            view = view[view["Specialty"].isin(sp)]
        if cd:
            view = view[view["Billing Code"].str.startswith(cd.strip())]
        if city:
            view = view[view["Address"].str.upper().str.contains(re.escape(city.strip().upper()))]
        if rmin:
            view = view[num(view["Rate"]) >= rmin]
        st.caption(f"{len(view):,} rows (showing up to 5,000)")
        st.dataframe(view.head(5000), hide_index=True)
        st.download_button("Download these rows (CSV)", view.to_csv(index=False).encode("utf-8"),
                           file_name="provider_rates_filtered.csv", mime="text/csv")

    with a4:
        st.markdown("Method evidence behind the address scores (C5).")
        dis = load(INT / "c5_nppes_disagreements.csv")
        if dis is not None:
            st.markdown(f"**NPPES disagrees with the other sources: {len(dis):,} NPIs**")
            st.dataframe(dis, hide_index=True)
        ref = load(INT / "c5_referee_cases.csv")
        if ref is not None:
            st.markdown(f"**Referee test: {len(ref):,} cases**")
            if "verdict" in ref.columns:
                st.altair_chart(hbar(ref["verdict"].value_counts(), "Cases", color="#6a5cff"), theme=None)
            st.dataframe(ref, hide_index=True)
        if dis is None and ref is None:
            st.info("Run c5_report.py and c5_test_referee.py to populate this tab.")