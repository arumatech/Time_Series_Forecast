namespace forecast.data;
 
entity EIA_WTI_Prices {
    key DATE  : Date;
        VALUE : Decimal(10,3);
}
 
entity EIA_Gulf_Gasoline_Prices {
    key DATE  : Date;
        VALUE : Decimal(10,3);
}
 
@cds.persistence.name: 'FORECAST_RESULTS'
entity Forecast_Results {
 
    key RUN_ID          : String(50);
    key FORECAST_DATE   : Date;
 
    COMMODITY           : String(100);
    MODEL               : String(50);
    PREDICTED_VALUE     : Decimal(15,6);
    UNIT                : String(20);
    RUN_DATE            : Date;
 
}
 
@cds.persistence.name: 'FORECAST_DATA_FRED_CURRENCY'
entity FRED_Currency {
    key DATE          : Date;
    key SERIES_ID     : String(20);
 
        VARIABLE_NAME : String(50);
        VALUE         : Decimal(18,8);
        UNIT          : String(30);
        SOURCE        : String(20);
}