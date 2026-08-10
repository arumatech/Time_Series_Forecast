using { ZFOR } from '../db/forecast';

service ForecastService {

    entity EIA_PRICE as projection on ZFOR.EIA_PRICE;

}