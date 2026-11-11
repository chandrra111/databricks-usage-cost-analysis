// =====================================================================================
// Power BI parameters  (Home > Transform data > Manage Parameters > New Parameter)
// Author: Balachandra Srinevasalu · MIT License
//
// Create each of these as a TEXT parameter. When the report is saved as a .pbit
// template, Power BI prompts every new user for these values on open, so anyone can
// point the template at their own Databricks workspace without editing queries.
// =====================================================================================

// ServerHostname  e.g.  adb-1234567890123456.7.azuredatabricks.net
//                       dbc-a1b2c3d4-e5f6.cloud.databricks.com   (AWS)
//                       1234567890123456.7.gcp.databricks.com    (GCP)
//                 Copy it from: SQL Warehouse > Connection details > Server hostname
"adb-0000000000000000.0.azuredatabricks.net" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// HttpPath        e.g.  /sql/1.0/warehouses/abcdef1234567890
//                 Copy it from: SQL Warehouse > Connection details > HTTP path
"/sql/1.0/warehouses/0000000000000000" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// Catalog         Catalog that holds vw_billing_usage_with_attribution   e.g. main
"main" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// Schema          Schema that holds the view                             e.g. finops
"finops" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]

// LocalCurrency   ISO code to convert USD into                           e.g. INR, EUR, GBP
"INR" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]
