namespace ZFOR;

using {managed} from '@sap/cds/common';


// ============================================================================
// EIA - Daily Prices
// ============================================================================

entity EIA_PRICES_DAILY : managed {

    key PERIOD : Date;

    WTI_CRUDE_USD_BBL : Decimal(15, 4);

    BRENT_CRUDE_USD_BBL : Decimal(15, 4);

    LA_RBOB_CARBOB_USD_GAL : Decimal(15, 4);

    GULF_GASOLINE_USD_GAL : Decimal(15, 4);

    NYH_GASOLINE_USD_GAL : Decimal(15, 4);

    GULF_ULSD_DIESEL_USD_GAL : Decimal(15, 4);

    GULF_JETFUEL_USD_GAL : Decimal(15, 4);
}


// ============================================================================
// EIA - Weekly Inventory
// ============================================================================

entity EIA_INVENTORY_WEEKLY : managed {

    key PERIOD : Date;

    CRUDE_STOCKS_EXCL_SPR_KBBL : Decimal(15, 4);

    CRUDE_STOCKS_SPR_KBBL : Decimal(15, 4);

    TOTAL_GASOLINE_STOCKS_KBBL : Decimal(15, 4);

    DISTILLATE_STOCKS_KBBL : Decimal(15, 4);
}


// ============================================================================
// EIA - Weekly Refinery Utilization
// ============================================================================

entity EIA_REFINERY_UTIL_WEEKLY : managed {

    key PERIOD : Date;

    REFINERY_UTILIZATION_PCT : Decimal(15, 4);
}


// ============================================================================
// EIA - Weekly Imports / Exports
// ============================================================================

entity EIA_IMPORTS_EXPORTS_WEEKLY : managed {

    key PERIOD : Date;

    CRUDE_IMPORTS_KBBL_D : Decimal(15, 4);

    CRUDE_EXPORTS_KBBL_D : Decimal(15, 4);
}


// ============================================================================
// FRED - Daily Currency
// ============================================================================

entity FRED_CURRENCY_DAILY : managed {

    key PERIOD : Date;

    USD_BROAD_INDEX : Decimal(15, 6);

    USD_PER_EUR : Decimal(15, 6);

    CNY_PER_USD : Decimal(15, 6);

    JPY_PER_USD : Decimal(15, 6);
}


// ============================================================================
// FRED - Monthly Inflation / Macro
// ============================================================================

entity FRED_INFLATION_MONTHLY : managed {

    key PERIOD : Date;

    CPI_ALL_URBAN : Decimal(15, 6);

    CPI_ENERGY : Decimal(15, 6);

    PPI_ALL_COMMODITIES : Decimal(15, 6);

    FED_FUNDS_RATE : Decimal(15, 6);
}


// ============================================================================
// Yahoo Finance - Daily Futures
// ============================================================================

entity YAHOO_FUTURES_DAILY : managed {

    key PERIOD : Date;

    WTI_FUTURE : Decimal(15, 6);

    BRENT_FUTURE : Decimal(15, 6);

    RBOB_GASOLINE_FUTURE : Decimal(15, 6);

    HEATING_OIL_FUTURE : Decimal(15, 6);

    NATGAS_FUTURE : Decimal(15, 6);

    DOLLAR_INDEX : Decimal(15, 6);
}


// ============================================================================
// World Bank - Annual Macro
// ============================================================================

entity WORLDBANK_MACRO_ANNUAL : managed {

    key YEAR : Integer;

    USA_GDP_GROWTH_PCT : Decimal(15, 6);

    USA_INFLATION_CPI_PCT : Decimal(15, 6);

    WORLD_GDP_GROWTH_PCT : Decimal(15, 6);

    WORLD_INFLATION_CPI_PCT : Decimal(15, 6);
}