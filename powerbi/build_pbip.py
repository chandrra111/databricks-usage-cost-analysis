#!/usr/bin/env python3
"""
Builds the Power BI Project (PBIP) for the Databricks Cost Analysis report:
  powerbi/pbip/DatabricksCostAnalysis.pbip
  powerbi/pbip/DatabricksCostAnalysis.SemanticModel/   (TMDL)
  powerbi/pbip/DatabricksCostAnalysis.Report/          (PBIR, visuals ready)

Open the .pbip in Power BI Desktop, then File > Export > Power BI template to get a .pbit.
Author: Balachandra Srinevasalu · MIT License
"""
import hashlib
import json
import shutil
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "pbip"
NAME = "DatabricksCostAnalysis"
SM = OUT / f"{NAME}.SemanticModel"
RP = OUT / f"{NAME}.Report"

DEMO_PATH = "chandrra111/databricks-usage-cost-analysis/main/sample_data/demo_billing_usage.csv"

S = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"
V_VISUAL, V_PAGE, V_REPORT = "2.4.0", "2.0.0", "3.0.0"

NAVY, ORANGE, BLUE, INK2 = "#14213D", "#EB6834", "#2A78D6", "#52514E"


def gid(*parts):
    """Deterministic GUID so rebuilds produce stable diffs."""
    return str(uuid.UUID(hashlib.md5("|".join(parts).encode()).hexdigest()))


def hid(*parts):
    return hashlib.md5("|".join(parts).encode()).hexdigest()[:20]


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def wjson(path: Path, obj):
    write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


# =====================================================================================
# 1. SEMANTIC MODEL (TMDL)
# =====================================================================================
PARAMS = [
    # name, default value, extra meta, description
    ("DataSource", "Demo", 'List={"Demo", "Databricks"}, DefaultValue="Demo", ',
     "Demo = sample data from GitHub. Databricks = your workspace."),
    ("ServerHostname", "adb-0000000000000000.0.azuredatabricks.net", "",
     "SQL Warehouse > Connection details > Server hostname"),
    ("HttpPath", "/sql/1.0/warehouses/0000000000000000", "",
     "SQL Warehouse > Connection details > HTTP path"),
    ("Catalog", "main", "", "Catalog holding the view"),
    ("Schema", "finops", "", "Schema holding the view"),
    ("ViewName", "vw_billing_usage_with_attribution", "", "Attribution view name"),
    ("LocalCurrency", "INR", "", "ISO currency to convert USD into (INR, EUR, GBP, AUD ...). USD = no conversion"),
    ("DemoDataPath", DEMO_PATH, "", "raw.githubusercontent.com path of the demo CSV"),
]

TEXT_COLS = ["workspace_name", "compute_category", "warehouse_name", "compute_resource_name",
             "billing_origin_product", "sku_name", "attributed_to", "identity_type", "query_source",
             "client_application", "statement_type", "attribution_method", "attribution_confidence"]
NUM_COLS = ["allocated_dbus", "list_price", "total_duration_ms", "total_usage_price_usd"]
INT_COLS = ["query_count"]
ALL_SRC = ["usage_date"] + TEXT_COLS + ["is_serverless"] + NUM_COLS[:2] + INT_COLS + NUM_COLS[2:]

BILLING_M = f"""let
    // ---- Source 1: sample data published in the GitHub repo (no credentials needed) ----
    FromDemo = () =>
        let
            Raw = Csv.Document(
                Web.Contents("https://raw.githubusercontent.com", [RelativePath = DemoDataPath]),
                [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
            Promoted = Table.PromoteHeaders(Raw, [PromoteAllScalars = true])
        in
            Promoted,

    // ---- Source 2: the attribution view in your Databricks workspace ----
    FromDatabricks = () =>
        let
            Src = Databricks.Catalogs(ServerHostname, HttpPath, [Catalog = null, Database = null, EnableAutomaticProxyDiscovery = null]),
            Cat = Src{{[Name = Catalog, Kind = "Database"]}}[Data],
            Sch = Cat{{[Name = Schema, Kind = "Schema"]}}[Data],
            Vw = Sch{{[Name = ViewName, Kind = "View"]}}[Data]
        in
            Vw,

    Source = if DataSource = "Databricks" then FromDatabricks() else FromDemo(),
    Selected = Table.SelectColumns(Source, {{{json.dumps(ALL_SRC)[1:-1]}}}, MissingField.UseNull),
    Typed = Table.TransformColumnTypes(Selected, {{
        {{"usage_date", type date}}, {{"is_serverless", type logical}}, {{"query_count", Int64.Type}},
        {", ".join('{"%s", type number}' % c for c in NUM_COLS)},
        {", ".join('{"%s", type text}' % c for c in TEXT_COLS)}
    }}, "en-US"),
    AddMonth = Table.AddColumn(Typed, "calendar_month", each Date.ToText([usage_date], "yyyy-MM", "en-US"), type text),
    AddDay = Table.AddColumn(AddMonth, "calendar_day", each Date.Day([usage_date]), Int64.Type),
    AddWeekday = Table.AddColumn(AddDay, "calendar_weekday", each Date.ToText([usage_date], "ddd", "en-US"), type text),
    AddMinutes = Table.AddColumn(AddWeekday, "total_duration_in_min", each [total_duration_ms] / 60000, type number)
in
    AddMinutes"""

FX_M = """let
    // One rate per month = last ECB reference rate on or before month end (Frankfurter API).
    MonthsBack = 24,
    Today = Date.From(DateTime.LocalNow()),
    StartDate = Date.StartOfMonth(Date.AddMonths(Today, -MonthsBack)),
    Months = List.Transform({0..MonthsBack}, each Date.ToText(Date.AddMonths(StartDate, _), "yyyy-MM", "en-US")),

    UsdTable = Table.FromRecords(List.Transform(Months, each [calendar_month = _, rate_effective_date = Date.FromText(_ & "-01"), exchange_rate = 1.0])),

    FromApi = () =>
        let
            Response = Json.Document(Web.Contents("https://api.frankfurter.dev",
                [RelativePath = "v1/" & Date.ToText(Date.AddDays(StartDate, -10), "yyyy-MM-dd", "en-US") & "..",
                 Query = [base = "USD", symbols = LocalCurrency]])),
            Rates = Response[rates],
            Daily = Table.FromRecords(List.Transform(Record.FieldNames(Rates), (d) =>
                [rate_date = Date.FromText(d), exchange_rate = Record.Field(Record.Field(Rates, d), LocalCurrency)])),
            Typed = Table.TransformColumnTypes(Daily, {{"rate_date", type date}, {"exchange_rate", type number}}),
            // For each month: latest rate dated on/before the month end
            PerMonth = Table.FromRecords(List.Transform(Months, (m) =>
                let
                    MonthEnd = Date.EndOfMonth(Date.FromText(m & "-01")),
                    Eligible = Table.SelectRows(Typed, each [rate_date] <= MonthEnd),
                    Last = if Table.IsEmpty(Eligible) then null else Table.Max(Eligible, "rate_date")
                in
                    [calendar_month = m,
                     rate_effective_date = if Last = null then null else Last[rate_date],
                     exchange_rate = if Last = null then null else Last[exchange_rate]])),
            NonEmpty = Table.SelectRows(PerMonth, each [exchange_rate] <> null)
        in
            NonEmpty,

    Result = if Text.Upper(LocalCurrency) = "USD" then UsdTable else FromApi(),
    Typed = Table.TransformColumnTypes(Result, {{"calendar_month", type text}, {"rate_effective_date", type date}, {"exchange_rate", type number}}),
    AddCcy = Table.AddColumn(Typed, "local_currency", each Text.Upper(LocalCurrency), type text)
in
    AddCcy"""

MEASURES = [
    # name, expression, format, folder
    ("Total Cost USD", "SUM ( billing_usage[total_usage_price_usd] )", "#,0.00", "Cost"),
    ("Allocated DBUs", "SUM ( billing_usage[allocated_dbus] )", "#,0", "Usage"),
    ("Total Query Count", "SUM ( billing_usage[query_count] )", "#,0", "Usage"),
    ("Total Duration (min)", "SUM ( billing_usage[total_duration_in_min] )", "#,0", "Usage"),
    ("Month Rate", """
VAR m = SELECTEDVALUE ( billing_usage[calendar_month] )
VAR r = LOOKUPVALUE ( fx_rates_monthly[exchange_rate], fx_rates_monthly[calendar_month], m )
VAR lastKnown =
    CALCULATE (
        MAX ( fx_rates_monthly[exchange_rate] ),
        TOPN ( 1, FILTER ( ALL ( fx_rates_monthly ), fx_rates_monthly[calendar_month] <= m ), fx_rates_monthly[calendar_month], DESC )
    )
RETURN COALESCE ( r, lastKnown )""", "0.0000", "Currency"),
    ("Total Cost Local", """
SUMX (
    VALUES ( billing_usage[calendar_month] ),
    CALCULATE ( SUM ( billing_usage[total_usage_price_usd] ) ) * [Month Rate]
)""", "#,0", "Cost"),
    ("Exchange Rate", "DIVIDE ( [Total Cost Local], [Total Cost USD] )", "0.00", "Currency"),
    ("Local Currency", 'SELECTEDVALUE ( fx_rates_monthly[local_currency], "USD" )', None, "Currency"),
    ("Cost Share %", "DIVIDE ( [Total Cost Local], CALCULATE ( [Total Cost Local], ALLSELECTED () ) )", "0.0%", "Cost"),
    ("Avg Cost per Day", "DIVIDE ( [Total Cost Local], DISTINCTCOUNT ( billing_usage[usage_date] ) )", "#,0", "Cost"),
    ("Identities with Spend", "CALCULATE ( DISTINCTCOUNT ( billing_usage[attributed_to] ), billing_usage[total_usage_price_usd] > 0 )", "#,0", "Usage"),
    ("Selected Period", """
VAR mn = MIN ( billing_usage[calendar_month] )
VAR mx = MAX ( billing_usage[calendar_month] )
RETURN IF ( mn = mx, mn, mn & " to " & mx )""", None, "Labels"),
    ("Cost Title", '"Total cost (" & [Local Currency] & ")"', None, "Labels"),
    ("Last Refreshed", '"Data as of " & FORMAT ( MAX ( billing_usage[usage_date] ), "dd-mmm-yyyy" )', None, "Labels"),
]


def tmdl_expr(expr: str, indent: str) -> str:
    """Single line stays inline; multi-line uses ``` fences."""
    expr = expr.strip("\n")
    if "\n" not in expr:
        return " " + expr
    body = "\n".join(indent + "\t\t" + ln if ln.strip() else "" for ln in expr.split("\n"))
    return f" ```\n{body}\n{indent}\t\t```"


def column(name, dtype, fmt=None, summarize="none", source=True, sort_by=None):
    lines = [f"\tcolumn {name}", f"\t\tdataType: {dtype}"]
    if fmt:
        lines.append(f"\t\tformatString: {fmt}")
    lines += [f"\t\tlineageTag: {gid('col', name)}", f"\t\tsummarizeBy: {summarize}"]
    if source:
        lines.append(f"\t\tsourceColumn: {name}")
    lines += ["", "\t\tannotation SummarizationSetBy = Automatic", ""]
    return "\n".join(lines)


def m_partition(name, m):
    body = "\n".join("\t\t\t\t" + ln if ln.strip() else "" for ln in m.split("\n"))
    return f"\tpartition {name} = m\n\t\tmode: import\n\t\tsource =\n{body}\n\n"


def build_model():
    write(SM / "definition.pbism", json.dumps({
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
        "version": "4.0", "settings": {}}, indent=2) + "\n")
    write(SM / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n\n")

    order = [p[0] for p in PARAMS] + ["billing_usage", "fx_rates_monthly"]
    write(SM / "definition" / "model.tmdl", f"""model Model
\tculture: en-US
\tdefaultPowerBIDataSourceVersion: powerBI_V3
\tsourceQueryCulture: en-US
\tdataAccessOptions
\t\tlegacyRedirects
\t\treturnErrorValuesAsNull

annotation PBI_QueryOrder = {json.dumps(order)}

annotation __PBI_TimeIntelligenceEnabled = 0

annotation PBI_ProTooling = ["DevMode"]

ref table billing_usage
ref table fx_rates_monthly

ref cultureInfo en-US

""")

    exprs = []
    for name, val, extra, desc in PARAMS:
        exprs.append(
            f'/// {desc}\nexpression {name} = "{val}" meta [IsParameterQuery=true, {extra}Type="Text", IsParameterQueryRequired=true]\n'
            f"\tlineageTag: {gid('param', name)}\n\n\tannotation PBI_ResultType = Text\n")
    write(SM / "definition" / "expressions.tmdl", "\n".join(exprs) + "\n")

    write(SM / "definition" / "cultures" / "en-US.tmdl", "cultureInfo en-US\n\n")

    # billing_usage
    parts = [f"table billing_usage\n\tlineageTag: {gid('table', 'billing_usage')}\n"]
    for mname, expr, fmt, folder in MEASURES:
        block = f"\tmeasure '{mname}' ={tmdl_expr(expr, chr(9))}\n"
        if fmt:
            block += f"\t\tformatString: {fmt}\n"
        block += f"\t\tdisplayFolder: {folder}\n\t\tlineageTag: {gid('measure', mname)}\n"
        parts.append(block)
    parts.append(column("usage_date", "dateTime", "yyyy-mm-dd"))
    for c in TEXT_COLS:
        parts.append(column(c, "string"))
    parts.append(column("is_serverless", "boolean"))
    parts.append(column("allocated_dbus", "double", "#,0.00", "sum"))
    parts.append(column("list_price", "double", "0.000", "none"))
    parts.append(column("query_count", "int64", "0", "sum"))
    parts.append(column("total_duration_ms", "double", "#,0", "sum"))
    parts.append(column("total_usage_price_usd", "double", "#,0.00", "sum"))
    parts.append(column("calendar_month", "string"))
    parts.append(column("calendar_day", "int64", "0", "none"))
    parts.append(column("calendar_weekday", "string"))
    parts.append(column("total_duration_in_min", "double", "#,0.0", "sum"))
    parts.append(m_partition("billing_usage", BILLING_M))
    parts.append("\tannotation PBI_ResultType = Table\n")
    write(SM / "definition" / "tables" / "billing_usage.tmdl", "\n".join(parts))

    # fx_rates_monthly
    parts = [f"table fx_rates_monthly\n\tlineageTag: {gid('table', 'fx')}\n",
             column("calendar_month", "string"),
             column("rate_effective_date", "dateTime", "yyyy-mm-dd"),
             column("exchange_rate", "double", "0.0000", "none"),
             column("local_currency", "string"),
             m_partition("fx_rates_monthly", FX_M),
             "\tannotation PBI_ResultType = Table\n"]
    write(SM / "definition" / "tables" / "fx_rates_monthly.tmdl", "\n".join(parts))

    write(SM / "definition" / "relationships.tmdl",
          f"relationship {gid('rel', 'month')}\n\tfromColumn: billing_usage.calendar_month\n\ttoColumn: fx_rates_monthly.calendar_month\n\n")


# =====================================================================================
# 2. REPORT (PBIR)
# =====================================================================================
T = "billing_usage"


def lit(v):
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


def color(hex_):
    return {"solid": {"color": lit(hex_)}}


def col(p, e=T):
    return {"Column": {"Expression": {"SourceRef": {"Entity": e}}, "Property": p}}


def mea(p, e=T):
    return {"Measure": {"Expression": {"SourceRef": {"Entity": e}}, "Property": p}}


def proj(f):
    k = "Column" if "Column" in f else "Measure"
    e, p = f[k]["Expression"]["SourceRef"]["Entity"], f[k]["Property"]
    return {"field": f, "queryRef": f"{e}.{p}", "nativeQueryRef": p}


def container(title=None, bg="#FFFFFF", border=True, title_size=11):
    o = {"background": [{"properties": {"show": lit(True), "color": color(bg), "transparency": lit(0)}}],
         "border": [{"properties": {"show": lit(border), "color": color("#E3E2DC"), "radius": lit(8)}}],
         "dropShadow": [{"properties": {"show": lit(False)}}]}
    if title:
        o["title"] = [{"properties": {"show": lit(True), "text": lit(title), "fontColor": color(NAVY),
                                      "fontSize": lit(title_size), "bold": lit(True)}}]
    else:
        o["title"] = [{"properties": {"show": lit(False)}}]
    return o


class Page:
    def __init__(self, key, display):
        self.key, self.display, self.name = key, display, hid("page", key)
        self.visuals = []

    def add(self, vtype, x, y, w, h, query=None, objects=None, vco=None, sort=None, extra=None):
        name = hid(self.key, vtype, str(len(self.visuals)))
        visual = {"visualType": vtype}
        if query:
            visual["query"] = {"queryState": query}
            if sort:
                visual["query"]["sortDefinition"] = {"sort": [{"field": sort[0], "direction": sort[1]}], "isDefaultSort": True}
        if objects:
            visual["objects"] = objects
        if vco:
            visual["visualContainerObjects"] = vco
        visual["drillFilterOtherVisuals"] = True
        if extra:
            visual.update(extra)
        self.visuals.append({
            "$schema": f"{S}/visualContainer/{V_VISUAL}/schema.json",
            "name": name,
            "position": {"x": x, "y": y, "z": len(self.visuals) * 1000, "width": w, "height": h,
                         "tabOrder": len(self.visuals) * 1000},
            "visual": visual})
        return self

    # ---------- building blocks ----------
    def header(self, title):
        runs = [{"value": title, "textStyle": {"fontSize": "20pt", "fontWeight": "bold", "color": "#FFFFFF",
                                               "fontFamily": "Segoe UI Semibold"}}]
        self.add("textbox", 0, 0, 1280, 56,
                 objects={"general": [{"properties": {"paragraphs": [{"textRuns": runs}]}}]},
                 vco={"background": [{"properties": {"show": lit(True), "color": color(NAVY), "transparency": lit(0)}}],
                      "padding": [{"properties": {"top": lit(10), "left": lit(20)}}]})
        # data-as-of label (card bound to a measure) on the right of the header
        self.add("card", 1040, 6, 230, 44, query={"Values": {"projections": [proj(mea("Last Refreshed"))]}},
                 objects={"labels": [{"properties": {"fontSize": lit(11), "color": color("#FFFFFF")}}],
                          "categoryLabels": [{"properties": {"show": lit(False)}}]},
                 vco={"background": [{"properties": {"show": lit(False)}}], "title": [{"properties": {"show": lit(False)}}],
                      "border": [{"properties": {"show": lit(False)}}]})
        return self

    def slicers(self, fields, y=64, x0=10, total_w=1260, h=56):
        w = (total_w - (len(fields) - 1) * 8) / len(fields)
        for i, f in enumerate(fields):
            self.add("slicer", round(x0 + i * (w + 8)), y, round(w), h,
                     query={"Values": {"projections": [proj(col(f))]}},
                     objects={"data": [{"properties": {"mode": lit("Dropdown")}}],
                              "header": [{"properties": {"show": lit(True), "fontColor": color(INK2), "textSize": lit(9)}}],
                              "general": [{"properties": {"selfFilterEnabled": lit(True)}}]},
                     vco=container())
        return self

    def card(self, x, y, w, h, measure, label, fmt_size=22):
        return self.add("card", x, y, w, h, query={"Values": {"projections": [proj(mea(measure))]}},
                        objects={"labels": [{"properties": {"fontSize": lit(fmt_size), "color": color(NAVY)}}],
                                 "categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(10), "color": color(INK2)}}]},
                        vco=container())

    def chart(self, vtype, x, y, w, h, category, measure, title, sort_desc=True, labels=True, top_n=None, color_hex=BLUE):
        objects = {"labels": [{"properties": {"show": lit(labels), "color": color(INK2), "fontSize": lit(8)}}],
                   "dataPoint": [{"properties": {"fill": color(color_hex)}}],
                   "valueAxis": [{"properties": {"show": lit(not labels), "gridlineShow": lit(False)}}],
                   "categoryAxis": [{"properties": {"fontSize": lit(9)}}]}
        sort = (mea(measure), "Descending") if sort_desc else (col(category), "Ascending")
        self.add(vtype, x, y, w, h,
                 query={"Category": {"projections": [proj(col(category))]},
                        "Y": {"projections": [proj(mea(measure))]}},
                 objects=objects, vco=container(title), sort=sort)
        if top_n:
            v = self.visuals[-1]
            v["filterConfig"] = {"filters": [{
                "name": hid(v["name"], "topn"), "field": col(category), "type": "TopN",
                "filter": {"Version": 2, "From": [{"Name": "subquery", "Expression": {"Subquery": {"Query": {
                    "Version": 2, "From": [{"Name": "b", "Entity": T, "Type": 0}],
                    "Select": [{"Column": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": category}, "Name": "field"}],
                    "OrderBy": [{"Direction": 2, "Expression": {"Measure": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": measure}}}],
                    "Top": top_n}}}, "Type": 2}, {"Name": "b", "Entity": T, "Type": 0}],
                    "Where": [{"Condition": {"In": {"Expressions": [{"Column": {"Expression": {"SourceRef": {"Source": "b"}}, "Property": category}}],
                                                    "Table": {"SourceRef": {"Source": "subquery"}}}}}]}}]}
        return self

    def donut(self, x, y, w, h, category, measure, title):
        return self.add("donutChart", x, y, w, h,
                        query={"Category": {"projections": [proj(col(category))]},
                               "Y": {"projections": [proj(mea(measure))]}},
                        objects={"labels": [{"properties": {"show": lit(True), "labelStyle": lit("Category, percent of total"),
                                                            "fontSize": lit(8)}}],
                                 "legend": [{"properties": {"show": lit(False)}}]},
                        vco=container(title), sort=(mea(measure), "Descending"))

    def text(self, x, y, w, h, paragraphs):
        paras = []
        for p in paragraphs:
            if isinstance(p, str):
                p = [(p, {})]
            runs = [{"value": t, "textStyle": {"fontSize": st.get("size", "11pt"), "color": st.get("color", "#252423"),
                                               **({"fontWeight": "bold"} if st.get("bold") else {})}} for t, st in p]
            paras.append({"textRuns": runs})
        return self.add("textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paras}}]},
                        vco={"background": [{"properties": {"show": lit(False)}}]})


SLICERS = ["calendar_month", "workspace_name", "warehouse_name", "compute_category", "billing_origin_product", "query_source"]


def build_pages():
    pages = []

    # ---------------- About ----------------
    p = Page("about", "About").header("Databricks Cost Analysis")
    H = {"bold": True, "color": ORANGE, "size": "12pt"}
    p.text(40, 80, 1200, 600, [
        [("What this report shows", H)],
        "Databricks compute consumption and cost across workspaces, SQL warehouses, job clusters, serverless notebooks, apps and more. "
        "Cost is attributed to the identity that drove it (user or service principal): SQL Warehouse cost is split across users "
        "in proportion to the queries they ran each day, and everything else is attributed from billing metadata.",
        "",
        [("Currency conversion", H)],
        "Cost is DBUs x Databricks list price (USD). It is converted to the LocalCurrency parameter using the Frankfurter API "
        "(ECB reference rates): one rate per month = the last available rate on or before the month end.",
        "",
        [("Connect your own workspace", H)],
        "Home > Transform data > Edit parameters: set DataSource = Databricks, then ServerHostname, HttpPath, Catalog, Schema "
        "(where you created vw_billing_usage_with_attribution) and LocalCurrency. Sign in with a token or Microsoft Entra ID and Refresh. "
        "DataSource = Demo loads sample data from GitHub.",
        "",
        [("Disclaimer", H)],
        "List-price cost excludes negotiated discounts and, for classic compute, the cloud provider's VM/storage charges. "
        "Converted figures may closely match but will not exactly match your cloud invoice (timing, rounding, rate source, pricing).",
        "",
        [("Source & author", H)],
        "github.com/chandrra111/databricks-usage-cost-analysis  ·  Template by Balachandra Srinevasalu  ·  MIT License",
    ])
    pages.append(p)

    # ---------------- Cost Summary ----------------
    p = Page("summary", "Cost Summary").header("Databricks Cost Summary").slicers(SLICERS)
    cards = [("Total Cost Local", 0), ("Total Cost USD", 0), ("Exchange Rate", 0), ("Total Query Count", 0), ("Allocated DBUs", 0)]
    cw = (1260 - 4 * 8) / 5
    for i, (m, _) in enumerate(cards):
        p.card(round(10 + i * (cw + 8)), 128, round(cw), 84, m, m)
    w3 = (1260 - 16) / 3
    xs = [10, round(10 + w3 + 8), round(10 + 2 * (w3 + 8))]
    p.chart("clusteredBarChart", xs[0], 220, round(w3), 244, "billing_origin_product", "Total Cost Local", "Cost by billing product")
    p.donut(xs[1], 220, round(w3), 244, "compute_category", "Total Cost Local", "Cost by compute category")
    p.donut(xs[2], 220, round(w3), 244, "identity_type", "Total Cost Local", "Cost by identity type")
    p.chart("clusteredColumnChart", xs[0], 472, round(w3), 240, "calendar_month", "Allocated DBUs", "DBUs by month", sort_desc=False)
    p.chart("clusteredBarChart", xs[1], 472, round(w3), 240, "workspace_name", "Total Cost Local", "Cost by workspace")
    p.chart("clusteredColumnChart", xs[2], 472, round(w3), 240, "calendar_month", "Total Cost Local", "Cost by month", sort_desc=False, color_hex=ORANGE)
    pages.append(p)

    # ---------------- Daily Trends ----------------
    p = Page("daily", "Daily Trends").header("Databricks Daily Cost Trends").slicers(SLICERS)
    for i, (m, _) in enumerate([("Total Cost Local", 0), ("Avg Cost per Day", 0), ("Total Query Count", 0), ("Total Duration (min)", 0)]):
        w4 = (1260 - 24) / 4
        p.card(round(10 + i * (w4 + 8)), 128, round(w4), 84, m, m)
    p.chart("clusteredColumnChart", 10, 220, 1260, 240, "usage_date", "Total Cost Local", "Daily cost", sort_desc=False, labels=False)
    p.chart("clusteredColumnChart", 10, 468, 626, 244, "usage_date", "Total Query Count", "Daily query count", sort_desc=False, labels=False, color_hex="#1BAF7A")
    p.chart("clusteredColumnChart", 644, 468, 626, 244, "usage_date", "Total Duration (min)", "Daily query duration (min)", sort_desc=False, labels=False, color_hex=ORANGE)
    pages.append(p)

    # ---------------- Cost Breakdown (RCA) ----------------
    p = Page("rca", "Cost Breakdown (RCA)").header("Databricks Cost Breakdown (RCA)").slicers(SLICERS)
    p.add("decompositionTreeVisual", 10, 128, 1260, 584,
          query={"Analyze": {"projections": [proj(mea("Total Cost Local"))]},
                 "ExplainBy": {"projections": [proj(col(c)) for c in
                                               ["workspace_name", "warehouse_name", "compute_category", "identity_type",
                                                "attributed_to", "query_source", "billing_origin_product", "sku_name"]]}},
          vco=container("Click + to drill: workspace → warehouse → category → identity → user → tool"))
    pages.append(p)

    # ---------------- Cost by User ----------------
    p = Page("users", "Cost by User").header("Databricks Cost by User").slicers(SLICERS)
    p.add("slicer", 10, 128, 300, 584, query={"Values": {"projections": [proj(col("attributed_to"))]}},
          objects={"data": [{"properties": {"mode": lit("Basic")}}],
                   "general": [{"properties": {"selfFilterEnabled": lit(True)}}],
                   "header": [{"properties": {"show": lit(True), "text": lit("Search user / service principal")}}]},
          vco=container())
    p.chart("clusteredBarChart", 318, 128, 472, 584, "attributed_to", "Total Cost Local", "Cost by identity")
    p.chart("clusteredBarChart", 798, 128, 472, 584, "attributed_to", "Total Query Count", "Query count by identity", color_hex="#1BAF7A")
    pages.append(p)

    # ---------------- Details ----------------
    p = Page("details", "Details").header("Databricks Cost Details").slicers(SLICERS)
    p.add("pivotTable", 10, 128, 1260, 300,
          query={"Rows": {"projections": [proj(col("attributed_to"))]},
                 "Columns": {"projections": [proj(col("calendar_month"))]},
                 "Values": {"projections": [proj(mea("Total Cost Local"))]}},
          vco=container("Cost by identity × month"), sort=(mea("Total Cost Local"), "Descending"))
    detail_cols = ["usage_date", "workspace_name", "compute_category", "compute_resource_name", "sku_name",
                   "attributed_to", "identity_type", "query_source", "attribution_method"]
    p.add("tableEx", 10, 436, 1260, 276,
          query={"Values": {"projections": [proj(col(c)) for c in detail_cols] +
                            [proj(mea(m)) for m in ["Allocated DBUs", "Total Query Count", "Total Cost USD", "Total Cost Local"]]}},
          vco=container("Detail"), sort=(col("usage_date"), "Descending"))
    pages.append(p)
    return pages


THEME = {
    "name": "Databricks Cost Analysis",
    "dataColors": ["#2A78D6", "#EB6834", "#1BAF7A", "#EDA100", "#E87BA4", "#008300", "#6250D6", "#E34948"],
    "background": "#FFFFFF", "foreground": NAVY, "tableAccent": BLUE,
    "good": "#1AAB40", "neutral": "#D9B300", "bad": "#D64554",
    "textClasses": {
        "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": INK2},
        "title": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": NAVY},
        "callout": {"fontFace": "Segoe UI Semibold", "fontSize": 22, "color": NAVY},
    },
    "visualStyles": {"page": {"*": {"background": [{"color": {"solid": {"color": "#F4F4F1"}}, "transparency": 0}]}}},
}


def build_report():
    wjson(RP / "definition.pbir", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
        "version": "4.0", "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    wjson(RP / "definition" / "version.json", {"$schema": f"{S}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    wjson(RP / "StaticResources" / "RegisteredResources" / "DatabricksCostTheme.json", THEME)
    wjson(RP / "definition" / "report.json", {
        "$schema": f"{S}/report/{V_REPORT}/schema.json",
        "themeCollection": {"customTheme": {"name": "DatabricksCostTheme.json",
                                            "reportVersionAtImport": {"visual": V_VISUAL, "page": V_PAGE, "report": V_REPORT},
                                            "type": "RegisteredResources"}},
        "resourcePackages": [{"name": "RegisteredResources", "type": "RegisteredResources",
                              "items": [{"name": "DatabricksCostTheme.json", "path": "DatabricksCostTheme.json", "type": "CustomTheme"}]}],
        "settings": {"useStylableVisualContainerHeader": True, "exportDataMode": "AllowSummarized",
                     "defaultDrillFilterOtherVisuals": True, "allowChangeFilterTypes": True},
    })
    pages = build_pages()
    wjson(RP / "definition" / "pages" / "pages.json", {
        "$schema": f"{S}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": [p.name for p in pages], "activePageName": pages[1].name})
    for p in pages:
        base = RP / "definition" / "pages" / p.name
        wjson(base / "page.json", {"$schema": f"{S}/page/{V_PAGE}/schema.json", "name": p.name,
                                   "displayName": p.display, "displayOption": "FitToPage", "height": 720, "width": 1280})
        for v in p.visuals:
            wjson(base / "visuals" / v["name"] / "visual.json", v)


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    build_model()
    build_report()
    wjson(OUT / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}], "settings": {"enableAutoRecovery": True}})
    write(OUT / ".gitignore", "**/.pbi/localSettings.json\n**/.pbi/cache.abf\n")
    print("PBIP written to", OUT)


if __name__ == "__main__":
    main()
