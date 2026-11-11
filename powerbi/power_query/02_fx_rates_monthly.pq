// =====================================================================================
// Query name: fx_rates_monthly
// USD -> LocalCurrency monthly exchange rate, straight from the Frankfurter API
// (European Central Bank reference rates, free, no API key).
// Author: Balachandra Srinevasalu · MIT License
//
// RULE: one rate per calendar month = the LAST rate published on or before month end.
//       For the current month this is the latest rate available on refresh day.
//
// Why Web.Contents with RelativePath + Query: it keeps the base URL static, which is
// what lets the Power BI Service schedule refreshes for a web source. Set the data
// source privacy level to Public and authentication to Anonymous.
//
// Prefer doing this inside Databricks instead? Use notebooks/load_fx_rates.py +
// sql/02 and sql/03 and skip this query.
// =====================================================================================
let
    MonthsBack  = 12,                                                    // history to fetch
    StartDate   = Date.StartOfMonth(Date.AddMonths(Date.From(DateTime.LocalNow()), -MonthsBack)),
    StartText   = Date.ToText(StartDate, "yyyy-MM-dd"),

    // GET https://api.frankfurter.dev/v1/<start>..?base=USD&symbols=INR
    Response    = Json.Document(
                    Web.Contents(
                        "https://api.frankfurter.dev",
                        [ RelativePath = "v1/" & StartText & "..",
                          Query = [ base = "USD", symbols = LocalCurrency ] ]
                    )),

    // Response[rates] = record of { "2026-10-01" = [INR = 95.1], ... }
    RatesRecord = Response[rates],
    Daily       = Table.FromRecords(
                    List.Transform(
                        Record.FieldNames(RatesRecord),
                        (d) => [ rate_date = Date.FromText(d),
                                 exchange_rate = Record.Field(Record.Field(RatesRecord, d), LocalCurrency) ]
                    )),
    TypedDaily  = Table.TransformColumnTypes(Daily, {{"rate_date", type date}, {"exchange_rate", type number}}),
    WithMonth   = Table.AddColumn(TypedDaily, "calendar_month", each Date.ToText([rate_date], "yyyy-MM"), type text),

    // Last available rate on/before month end = row with the max rate_date in each month
    Monthly     = Table.Group(WithMonth, {"calendar_month"}, {
                    {"rate_effective_date", each List.Max([rate_date]), type date},
                    {"exchange_rate", each Table.Max(_, "rate_date")[exchange_rate], type number}
                  }),
    AddCcy      = Table.AddColumn(Monthly, "local_currency", each LocalCurrency, type text)
in
    AddCcy

// NOTE: if a month has no fixing yet (e.g. refresh on the 1st falling on a weekend)
// that month is missing here; the DAX measure [Exchange Rate] then falls back to the
// latest earlier month (see measures.dax).
