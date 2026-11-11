// =====================================================================================
// Query name: billing_usage
// Reads the attribution view from Databricks using the parameters in 00_parameters.pq
// Author: Balachandra Srinevasalu · MIT License
//
// Authentication on first refresh: choose "Personal Access Token" (or Azure AD /
// OAuth). The identity needs SELECT on the view (and therefore on the system tables).
// Import mode is recommended; the view is already aggregated per day.
// =====================================================================================
let
    Source   = Databricks.Catalogs(ServerHostname, HttpPath, [Catalog = null, Database = null, EnableAutomaticProxyDiscovery = null]),
    Cat      = Source{[Name = Catalog, Kind = "Database"]}[Data],
    Sch      = Cat{[Name = Schema, Kind = "Schema"]}[Data],
    View     = Sch{[Name = "vw_billing_usage_with_attribution", Kind = "View"]}[Data],

    // Keep columns the report uses (drop statement_text_preview here if the model gets large)
    Typed    = Table.TransformColumnTypes(View, {
                   {"usage_date", type date},
                   {"allocated_dbus", type number},
                   {"list_price", type number},
                   {"total_usage_price_usd", type number},
                   {"query_count", Int64.Type},
                   {"total_duration_ms", type number}
               }),

    // Helper columns used for slicers and the FX relationship
    AddMonth = Table.AddColumn(Typed, "calendar_month", each Date.ToText([usage_date], "yyyy-MM"), type text),
    AddDay   = Table.AddColumn(AddMonth, "calendar_day", each Date.Day([usage_date]), Int64.Type),
    AddWeek  = Table.AddColumn(AddDay, "calendar_week", each Date.ToText([usage_date], "ddd"), type text),
    AddMins  = Table.AddColumn(AddWeek, "total_duration_in_min", each [total_duration_ms] / 60000, type number)
in
    AddMins
