using { ZFOR } from '../db/oil';

@path: 'oil'
service OilService {

    // EIA
    entity EIA_PRICES_DAILY
        as projection on ZFOR.EIA_PRICES_DAILY;

    entity EIA_INVENTORY_WEEKLY
        as projection on ZFOR.EIA_INVENTORY_WEEKLY;

    entity EIA_REFINERY_UTIL_WEEKLY
        as projection on ZFOR.EIA_REFINERY_UTIL_WEEKLY;

    entity EIA_IMPORTS_EXPORTS_WEEKLY
        as projection on ZFOR.EIA_IMPORTS_EXPORTS_WEEKLY;


    // FRED
    entity FRED_CURRENCY_DAILY
        as projection on ZFOR.FRED_CURRENCY_DAILY;

    entity FRED_INFLATION_MONTHLY
        as projection on ZFOR.FRED_INFLATION_MONTHLY;


    // Yahoo Finance
    entity YAHOO_FUTURES_DAILY
        as projection on ZFOR.YAHOO_FUTURES_DAILY;


    // World Bank
    entity WORLDBANK_MACRO_ANNUAL
        as projection on ZFOR.WORLDBANK_MACRO_ANNUAL;

}