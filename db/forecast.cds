namespace ZFOR;

entity EIA_PRICE {

    key DATE       : Date;

    WTI_CRUDE      : Decimal(15, 4);

    BRENT_CRUDE    : Decimal(15, 4);
}