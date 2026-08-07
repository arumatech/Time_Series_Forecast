namespace ZRISK;

using {managed} from '@sap/cds/common';

entity FLATFILE : managed {

    key DCSID : String(20)
    @title: 'DCS ID';
    key MIC : String(4)
    @title: 'MIC';
    key PRICETYPE : String(2)
    @title: 'Price Type';
    key MKEYDT : Date
    @title: 'Maturity Keydate';
    key PRICEDATE : Date
    @title: 'Price Date';
    PRICE : Decimal(20,8)
    @title: 'Price';
    PER : Integer
    @title: 'Per';
    UOM : String(3)
    @title: 'Unit Of Measure';
    CURRENCY : String(5)
    @title: 'Currency';
    FILENAME : String(100)
    @title: 'File name';

}


entity INVENTORY_WEEKLY : managed {
    key period : String(20)
    @title: 'Period';

    crude_stocks_excl_spr_kbbl : String(20);

    crude_stocks_spr_kbbl : String(20);

    total_gasoline_stocks_kbbl : String(20);

    distillate_stocks_kbbl : String(20);
 
}